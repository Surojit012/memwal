from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

import msgpack
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from memwal.config import Config, load_config
from memwal.sui import SuiRegistry
from memwal.walrus import WalrusClient


_DELETED = "__DELETED__"
_LIST_APPEND = "__list_append__"
_MESSAGE_TAG = "__memwal_message__"
_MAX_DELTA_CHAIN_HOPS = 50
DEBUG = os.environ.get("MEMWAL_DEBUG", "0") == "1"
_CHECKPOINT_STEP_RE = re.compile(r"-checkpoint-(\d+)$")


class CheckpointChainError(Exception):
    pass


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import nest_asyncio

        nest_asyncio.apply(loop)

    return asyncio.run(coro)


def _extract_thread_id(config: RunnableConfig) -> str:
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError(
            "Config must contain configurable.thread_id. "
            f"Got config: {config!r}"
        )
    return str(thread_id)


def _extract_checkpoint_ns(config: RunnableConfig) -> str:
    return (config or {}).get("configurable", {}).get("checkpoint_ns", "")


def _extract_checkpoint_id(config: RunnableConfig) -> Optional[str]:
    checkpoint_id = (config or {}).get("configurable", {}).get("checkpoint_id")
    if checkpoint_id is None:
        return None
    return str(checkpoint_id)


def _make_storage_key(thread_id: str, checkpoint_ns: str) -> str:
    if checkpoint_ns:
        return f"{thread_id}:{checkpoint_ns}"
    return thread_id


def _format_kb(num_bytes: int) -> str:
    return f"{num_bytes / 1024:.1f}KB"


def _message_kind(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, SystemMessage):
        return "system"
    return message.__class__.__name__


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    encoded: dict[str, Any] = {
        _MESSAGE_TAG: _message_kind(message),
        "content": _serialize_for_msgpack(message.content),
    }

    message_id = getattr(message, "id", None)
    if message_id is not None:
        encoded["id"] = message_id

    name = getattr(message, "name", None)
    if name is not None:
        encoded["name"] = name

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if additional_kwargs:
        encoded["additional_kwargs"] = _serialize_for_msgpack(additional_kwargs)

    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        encoded["response_metadata"] = _serialize_for_msgpack(response_metadata)

    if isinstance(message, AIMessage):
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            encoded["tool_calls"] = _serialize_for_msgpack(tool_calls)

        invalid_tool_calls = getattr(message, "invalid_tool_calls", None)
        if invalid_tool_calls:
            encoded["invalid_tool_calls"] = _serialize_for_msgpack(invalid_tool_calls)

    return encoded


def _deserialize_message(value: dict[str, Any]) -> BaseMessage:
    kind = value[_MESSAGE_TAG]
    content = _deserialize_from_msgpack(value.get("content", ""))
    kwargs: dict[str, Any] = {}

    if "id" in value:
        kwargs["id"] = value["id"]
    if "name" in value:
        kwargs["name"] = value["name"]
    if "additional_kwargs" in value:
        kwargs["additional_kwargs"] = _deserialize_from_msgpack(
            value["additional_kwargs"]
        )
    if "response_metadata" in value:
        kwargs["response_metadata"] = _deserialize_from_msgpack(
            value["response_metadata"]
        )

    if kind == "human":
        return HumanMessage(content=content, **kwargs)
    if kind == "ai":
        if "tool_calls" in value:
            kwargs["tool_calls"] = _deserialize_from_msgpack(value["tool_calls"])
        if "invalid_tool_calls" in value:
            kwargs["invalid_tool_calls"] = _deserialize_from_msgpack(
                value["invalid_tool_calls"]
            )
        return AIMessage(content=content, **kwargs)
    if kind == "system":
        return SystemMessage(content=content, **kwargs)

    return HumanMessage(content=content, **kwargs)


def _serialize_for_msgpack(value: Any) -> Any:
    if isinstance(value, BaseMessage):
        return _serialize_message(value)
    if isinstance(value, dict):
        serialized: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, (str, int, float, bool, bytes)) or key is None:
                serialized_key = key
            else:
                serialized_key = str(key)
            serialized[serialized_key] = _serialize_for_msgpack(item)
        return serialized
    if isinstance(value, list):
        return [_serialize_for_msgpack(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_for_msgpack(item) for item in value]
    if isinstance(value, (str, int, float, bool, bytes)) or value is None:
        return value
    return str(value)


def _deserialize_from_msgpack(value: Any) -> Any:
    if isinstance(value, dict):
        if _MESSAGE_TAG in value:
            return _deserialize_message(value)
        return {
            key: _deserialize_from_msgpack(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_deserialize_from_msgpack(item) for item in value]
    return value


def _content_text_bytes(value: Any) -> int:
    if isinstance(value, BaseMessage):
        return len(str(value.content).encode("utf-8"))
    if isinstance(value, dict):
        if _MESSAGE_TAG in value:
            return len(str(value.get("content", "")).encode("utf-8"))
        if set(value.keys()).issuperset({"role", "content"}):
            return len(str(value.get("content", "")).encode("utf-8"))
        return sum(_content_text_bytes(item) for item in value.values())
    if isinstance(value, list):
        return sum(_content_text_bytes(item) for item in value)
    return 0


def _debug_msgpack_payload(label: str, payload: dict[str, Any]) -> None:
    if not DEBUG:
        return

    serialized = _serialize_for_msgpack(payload)
    packed = msgpack.packb(serialized, use_bin_type=True)
    content_bytes = _content_text_bytes(serialized)
    scaffolding_bytes = max(0, len(packed) - content_bytes)
    print(f"[debug:checkpoint] {label} serialized structure: {serialized!r}")
    print(
        "[debug:checkpoint] "
        f"{label} bytes: total={len(packed)} "
        f"message_content={content_bytes} "
        f"overhead={scaffolding_bytes}"
    )


def _prune_empty_dicts(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    pruned: dict[str, Any] = {}
    for key, item in value.items():
        next_item = _prune_empty_dicts(item)
        if isinstance(next_item, dict) and not next_item:
            continue
        pruned[key] = next_item
    return pruned


def _checkpoint_step(payload: dict[str, Any]) -> int:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        step = metadata.get("step")
        if isinstance(step, int):
            return step
        if isinstance(step, str) and step.isdigit():
            return int(step)

    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, dict):
        channel_versions = checkpoint.get("channel_versions")
        if isinstance(channel_versions, dict):
            messages_version = channel_versions.get("messages")
            if isinstance(messages_version, int):
                return messages_version
            if isinstance(messages_version, str) and messages_version.isdigit():
                return int(messages_version)

        checkpoint_id = checkpoint.get("id")
        if isinstance(checkpoint_id, str):
            match = _CHECKPOINT_STEP_RE.search(checkpoint_id)
            if match:
                return int(match.group(1))

    raise CheckpointChainError(f"Unable to infer compact delta step from payload: {payload!r}")


def _compact_timestamp(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value * 1000)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return int(datetime.now(timezone.utc).timestamp() * 1000)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _expand_timestamp(value: Any) -> str:
    if isinstance(value, int):
        if value > 10_000_000_000:
            seconds = value / 1000
        else:
            seconds = value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_id_for_step(storage_key: str, step: int) -> str:
    thread_id = storage_key.split(":", 1)[0]
    return f"{thread_id}-checkpoint-{step}"


def _compact_delta_diff(diff: dict[str, Any]) -> dict[str, Any]:
    compact = copy.deepcopy(diff)
    compact.pop("parent_checkpoint_id", None)

    checkpoint = compact.get("checkpoint")
    if isinstance(checkpoint, dict):
        checkpoint.pop("id", None)
        checkpoint.pop("ts", None)

    metadata = compact.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("step", None)

    return _prune_empty_dicts(compact)


def _compact_delta_envelope(
    storage_key: str,
    payload: dict[str, Any],
    diff: dict[str, Any],
    parent_blob_id: Optional[str],
) -> dict[str, Any]:
    checkpoint = payload.get("checkpoint", {})
    timestamp = checkpoint.get("ts") if isinstance(checkpoint, dict) else None
    return {
        "t": "d",
        "s": _checkpoint_step(payload),
        "ts": _compact_timestamp(timestamp),
        "d": _compact_delta_diff(diff),
        "p": parent_blob_id,
    }


def _apply_compact_delta(
    previous_payload: dict[str, Any],
    envelope: dict[str, Any],
    storage_key: str,
) -> dict[str, Any]:
    step = envelope.get("s", envelope.get("step"))
    if not isinstance(step, int):
        raise CheckpointChainError(f"Compact delta missing integer step: {envelope!r}")

    diff = copy.deepcopy(envelope.get("d", envelope.get("diff", {})))
    if not isinstance(diff, dict):
        raise CheckpointChainError(f"Compact delta diff must be a dict: {envelope!r}")

    previous_checkpoint = previous_payload.get("checkpoint")
    previous_checkpoint_id = None
    if isinstance(previous_checkpoint, dict):
        previous_checkpoint_id = previous_checkpoint.get("id")

    checkpoint_diff = diff.setdefault("checkpoint", {})
    if not isinstance(checkpoint_diff, dict):
        raise CheckpointChainError(f"Compact delta checkpoint diff must be a dict: {envelope!r}")
    checkpoint_diff["id"] = _checkpoint_id_for_step(storage_key, step)
    checkpoint_diff["ts"] = _expand_timestamp(envelope.get("ts"))

    metadata_diff = diff.setdefault("metadata", {})
    if not isinstance(metadata_diff, dict):
        raise CheckpointChainError(f"Compact delta metadata diff must be a dict: {envelope!r}")
    metadata_diff["step"] = step

    if previous_checkpoint_id is not None:
        diff["parent_checkpoint_id"] = previous_checkpoint_id

    return _apply_diff(previous_payload, diff)


def _trace_payload_messages(label: str, payload: Any) -> None:
    if not DEBUG:
        return

    if not isinstance(payload, dict):
        return

    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, dict):
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, dict):
            messages = channel_values.get("messages")
        else:
            messages = None
    else:
        messages = payload.get("messages")

    if not isinstance(messages, list):
        return

    print(f"[trace:{label}] about to serialize. message count={len(messages)}")
    for i, message in enumerate(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        print(
            f"[trace:{label}]   msg[{i}] type={type(message).__name__} "
            f"content_repr={repr(content)[:100]}"
        )


def _trace_raw_unpacked(raw_data: Any) -> None:
    if not DEBUG:
        return

    if isinstance(raw_data, dict):
        print(f"[trace:get] raw unpacked data keys={list(raw_data.keys())}")

        checkpoint = raw_data.get("checkpoint")
        if isinstance(checkpoint, dict):
            channel_values = checkpoint.get("channel_values")
            if isinstance(channel_values, dict):
                messages = channel_values.get("messages")
            else:
                messages = None
        else:
            messages = raw_data.get("messages")

        print(f"[trace:get] raw messages field: {repr(messages)[:500]}")
    else:
        print(f"[trace:get] raw unpacked data keys={type(raw_data)}")


def _trace_reconstructed_messages(raw_data: Any) -> None:
    if not DEBUG:
        return

    if not isinstance(raw_data, dict):
        return

    checkpoint = raw_data.get("checkpoint")
    if isinstance(checkpoint, dict):
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, dict):
            messages = channel_values.get("messages")
        else:
            messages = None
    else:
        messages = raw_data.get("messages")

    if not isinstance(messages, list):
        return

    for i, message in enumerate(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        print(
            f"[trace:get]   reconstructed[{i}] type={type(message).__name__} "
            f"content_repr={repr(content)[:100]}"
        )


def _pack_payload(payload: dict[str, Any]) -> bytes:
    _trace_payload_messages("put", payload)
    serialized = _serialize_for_msgpack(payload)
    return msgpack.packb(serialized, use_bin_type=True)


def _unpack_payload(data: bytes) -> dict[str, Any]:
    raw_data = msgpack.unpackb(data, raw=False)
    _trace_raw_unpacked(raw_data)
    payload = _deserialize_from_msgpack(raw_data)
    _trace_reconstructed_messages(payload)
    if not isinstance(payload, dict):
        raise CheckpointChainError(
            f"Expected checkpoint payload to unpack as dict, got {type(payload).__name__}"
        )
    return payload


def _checkpoint_payload(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
) -> dict[str, Any]:
    return {
        "checkpoint": checkpoint,
        "metadata": metadata,
        "parent_checkpoint_id": _extract_checkpoint_id(config),
        "checkpoint_ns": _extract_checkpoint_ns(config),
    }


def _compute_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}

    for key, value in new.items():
        if key not in old:
            diff[key] = value
            continue

        old_value = old[key]
        if isinstance(old_value, dict) and isinstance(value, dict):
            nested_diff = _compute_diff(old_value, value)
            if nested_diff:
                diff[key] = nested_diff
        elif isinstance(old_value, list) and isinstance(value, list):
            if len(value) >= len(old_value) and value[: len(old_value)] == old_value:
                appended = value[len(old_value):]
                if appended:
                    diff[key] = {_LIST_APPEND: appended}
                    if DEBUG:
                        _debug_msgpack_payload(
                            f"list append diff for key {key!r}",
                            {key: {_LIST_APPEND: appended}},
                        )
            elif old_value != value:
                diff[key] = value
        elif old_value != value:
            diff[key] = value

    for key in old:
        if key not in new:
            diff[key] = _DELETED

    return diff


def _apply_diff(old: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(old)

    for key, value in diff.items():
        if value == _DELETED:
            patched.pop(key, None)
            continue

        if isinstance(value, dict) and set(value.keys()) == {_LIST_APPEND}:
            base_value = patched.get(key, [])
            append_value = value[_LIST_APPEND]
            if not isinstance(base_value, list):
                raise CheckpointChainError(
                    f"Cannot apply list append diff to non-list key {key!r}: "
                    f"{type(base_value).__name__}"
                )
            if not isinstance(append_value, list):
                raise CheckpointChainError(
                    f"List append diff for key {key!r} must contain a list, "
                    f"got {type(append_value).__name__}"
                )
            patched[key] = copy.deepcopy(base_value) + copy.deepcopy(append_value)
        elif isinstance(value, dict) and isinstance(patched.get(key), dict):
            patched[key] = _apply_diff(patched[key], value)
        else:
            patched[key] = copy.deepcopy(value)

    return patched


def _registry_value(blob_id: str, parent: Optional[str], blob_type: str) -> str:
    return json.dumps(
        {
            "blob_id": blob_id,
            "parent": parent,
            "type": blob_type,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_registry_value(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {
            "blob_id": value,
            "parent": None,
            "type": "base",
        }

    if not isinstance(parsed, dict):
        raise CheckpointChainError(
            f"Registry value must be a blob id string or JSON object, got {parsed!r}"
        )

    blob_id = parsed.get("blob_id")
    if not isinstance(blob_id, str) or not blob_id:
        raise CheckpointChainError(
            f"Registry metadata is missing a non-empty blob_id: {parsed!r}"
        )

    blob_type = parsed.get("type", "base")
    if blob_type not in {"base", "delta"}:
        raise CheckpointChainError(
            f"Registry metadata type must be 'base' or 'delta', got {blob_type!r}"
        )

    parent = parsed.get("parent")
    if parent is not None and not isinstance(parent, str):
        raise CheckpointChainError(
            f"Registry metadata parent must be a string or null, got {parent!r}"
        )

    return {
        "blob_id": blob_id,
        "parent": parent,
        "type": blob_type,
    }


def _unwrap_blob_payload(blob_id: str, raw: bytes) -> dict[str, Any]:
    payload = _unpack_payload(raw)
    blob_type = payload.get("type")

    if blob_type in {"base", "delta"}:
        return payload

    if payload.get("t") == "d":
        parent = payload.get("p")
        if parent is not None and not isinstance(parent, str):
            raise CheckpointChainError(
                f"Compact delta blob {blob_id!r} has invalid parent {parent!r}"
            )
        return {
            "type": "delta",
            "compact": True,
            "step": payload.get("s"),
            "ts": payload.get("ts"),
            "diff": payload.get("d", {}),
            "parent": parent,
            "blob_id": blob_id,
        }

    return {
        "type": "base",
        "payload": payload,
        "parent": None,
        "blob_id": blob_id,
    }


def _checkpoint_tuple_from_payload(
    thread_id: str,
    checkpoint_ns: str,
    payload: dict[str, Any],
) -> CheckpointTuple:
    checkpoint: Checkpoint = payload["checkpoint"]
    metadata: CheckpointMetadata = payload.get("metadata", {})
    parent_checkpoint_id: Optional[str] = payload.get("parent_checkpoint_id")

    parent_config: Optional[RunnableConfig] = None
    if parent_checkpoint_id is not None:
        parent_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": parent_checkpoint_id,
            },
        }

    return CheckpointTuple(
        config={
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
        },
        checkpoint=checkpoint,
        metadata=metadata,
        parent_config=parent_config,
    )


class WalrusCheckpointer(BaseCheckpointSaver):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._walrus = WalrusClient(
            publisher_url=config.WALRUS_PUBLISHER,
            aggregator_url=config.WALRUS_AGGREGATOR,
        )
        self._sui = SuiRegistry(config)
        self._strategy = config.CHECKPOINT_STRATEGY
        self._snapshot_every_n = config.SNAPSHOT_EVERY_N
        self._snapshot_cache: dict[str, dict[str, Any]] = {}
        self._snapshot_steps: dict[str, int] = {}
        self._snapshot_last_uploaded_step: dict[str, int] = {}
        self._delta_payload_cache: dict[str, dict[str, Any]] = {}
        self._delta_blob_cache: dict[str, str] = {}
        self._last_delta_hops: int = 0

        if self._strategy == "snapshot":
            print(
                "[memwal] Checkpoint strategy: "
                f"snapshot (every {self._snapshot_every_n} steps)"
            )
        elif self._strategy == "delta":
            print("[memwal] Checkpoint strategy: delta (base + diffs)")
        else:
            raise ValueError(
                "Config.CHECKPOINT_STRATEGY must be 'snapshot' or 'delta', "
                f"got {self._strategy!r}"
            )

    @classmethod
    def from_env(cls, dotenv_path=None) -> "WalrusCheckpointer":
        cfg = load_config(dotenv_path=dotenv_path)
        return cls(cfg)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return _run_async(self.aput(config, checkpoint, metadata, new_versions))

    def get(self, config: RunnableConfig) -> Optional[Checkpoint]:
        if value := self.get_tuple(config):
            return value.checkpoint
        return None

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return _run_async(self.aget_tuple(config))

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        results = _run_async(
            self._alist_internal(config, filter=filter, before=before, limit=limit)
        )
        yield from results

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return None

    def force_snapshot(self, thread_id: str) -> Optional[RunnableConfig]:
        return _run_async(self.aforce_snapshot(thread_id))

    async def aforce_snapshot(self, thread_id: str) -> Optional[RunnableConfig]:
        matches = [
            storage_key
            for storage_key in self._snapshot_cache
            if storage_key == thread_id or storage_key.startswith(f"{thread_id}:")
        ]
        if not matches:
            return None

        latest_config: Optional[RunnableConfig] = None
        for storage_key in matches:
            payload = self._snapshot_cache[storage_key]
            latest_config = await self._upload_snapshot_payload(storage_key, payload)
            self._snapshot_last_uploaded_step[storage_key] = self._snapshot_steps.get(
                storage_key,
                0,
            )
        return latest_config

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        if self._strategy == "snapshot":
            return await self._put_snapshot(config, checkpoint, metadata)
        return await self._put_delta(config, checkpoint, metadata)

    async def aget_tuple(
        self, config: RunnableConfig
    ) -> Optional[CheckpointTuple]:
        if self._strategy == "snapshot":
            return await self._get_snapshot(config)
        return await self._get_delta(config)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        results = await self._alist_internal(
            config, filter=filter, before=before, limit=limit
        )
        for item in results:
            yield item

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return None

    async def _put_snapshot(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)
        payload = _checkpoint_payload(config, checkpoint, metadata)
        full_bytes = len(_pack_payload(payload))

        step = self._snapshot_steps.get(storage_key, 0) + 1
        self._snapshot_steps[storage_key] = step
        self._snapshot_cache[storage_key] = payload

        should_upload = step == 1 or step % self._snapshot_every_n == 0
        if should_upload:
            updated_config = await self._upload_snapshot_payload(storage_key, payload)
            self._snapshot_last_uploaded_step[storage_key] = step
            print(
                "[memwal] Uploaded snapshot: "
                f"{_format_kb(full_bytes)} (saved 0.0KB vs full snapshot)"
            )
            return updated_config

        saved = full_bytes
        print(
            "[memwal] Cached volatile checkpoint: "
            f"0.0KB uploaded (saved {_format_kb(saved)} vs full snapshot)"
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
        }

    async def _upload_snapshot_payload(
        self,
        storage_key: str,
        payload: dict[str, Any],
    ) -> RunnableConfig:
        checkpoint = payload["checkpoint"]
        checkpoint_ns = payload.get("checkpoint_ns", "")
        thread_id = storage_key.split(":", 1)[0]
        data = _pack_payload(payload)

        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            blob_id = await walrus.store_blob(data, epochs=self._config.STORAGE_EPOCHS)

        async with SuiRegistry(self._config) as sui:
            await sui.register_blob(storage_key, blob_id)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
        }

    async def _get_snapshot(
        self, config: RunnableConfig
    ) -> Optional[CheckpointTuple]:
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)

        cache_step = self._snapshot_steps.get(storage_key, 0)
        uploaded_step = self._snapshot_last_uploaded_step.get(storage_key, 0)
        if storage_key in self._snapshot_cache and cache_step > uploaded_step:
            return _checkpoint_tuple_from_payload(
                thread_id,
                checkpoint_ns,
                self._snapshot_cache[storage_key],
            )

        async with SuiRegistry(self._config) as sui:
            registry_value = await sui.lookup_blob(storage_key)

        if registry_value is None:
            return None

        blob_id = _parse_registry_value(registry_value)["blob_id"]
        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            raw = await walrus.fetch_blob(blob_id)

        payload = _unpack_payload(raw)
        self._snapshot_cache[storage_key] = payload
        self._snapshot_steps.setdefault(storage_key, uploaded_step)
        return _checkpoint_tuple_from_payload(thread_id, checkpoint_ns, payload)

    async def _put_delta(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)
        payload = _checkpoint_payload(config, checkpoint, metadata)
        full_data = _pack_payload(payload)
        previous_payload = self._delta_payload_cache.get(storage_key)
        previous_blob_id = self._delta_blob_cache.get(storage_key)

        if previous_payload is None:
            chain_payload, latest_blob_id = await self._load_delta_payload(storage_key)
            previous_payload = chain_payload
            previous_blob_id = latest_blob_id

        if previous_payload is None:
            envelope = {
                "type": "base",
                "payload": payload,
                "parent": None,
            }
            data = _pack_payload(envelope)
            blob_type = "base"
            parent_blob_id = None
        else:
            diff = _compute_diff(previous_payload, payload)
            envelope = _compact_delta_envelope(
                storage_key,
                payload,
                diff,
                previous_blob_id,
            )
            if DEBUG:
                _debug_msgpack_payload("delta envelope", envelope)
            data = _pack_payload(envelope)
            blob_type = "delta"
            parent_blob_id = previous_blob_id

        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            blob_id = await walrus.store_blob(data, epochs=self._config.STORAGE_EPOCHS)

        registry_value = _registry_value(blob_id, parent_blob_id, blob_type)
        async with SuiRegistry(self._config) as sui:
            await sui.register_blob(storage_key, registry_value)

        self._delta_payload_cache[storage_key] = payload
        self._delta_blob_cache[storage_key] = blob_id

        uploaded_label = "base snapshot" if blob_type == "base" else "delta"
        saved = max(0, len(full_data) - len(data))
        print(
            f"[memwal] Uploaded {uploaded_label}: "
            f"{_format_kb(len(data))} (saved {_format_kb(saved)} vs full snapshot)"
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
        }

    async def _get_delta(
        self, config: RunnableConfig
    ) -> Optional[CheckpointTuple]:
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)

        payload, blob_id = await self._load_delta_payload(storage_key)
        if payload is None:
            return None

        self._delta_payload_cache[storage_key] = payload
        if blob_id is not None:
            self._delta_blob_cache[storage_key] = blob_id

        return _checkpoint_tuple_from_payload(thread_id, checkpoint_ns, payload)

    async def _load_delta_payload(
        self,
        storage_key: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        async with SuiRegistry(self._config) as sui:
            registry_value = await sui.lookup_blob(storage_key)

        if registry_value is None:
            self._last_delta_hops = 0
            return None, None

        latest = _parse_registry_value(registry_value)
        latest_blob_id = latest["blob_id"]
        current_blob_id: Optional[str] = latest_blob_id
        chain: list[dict[str, Any]] = []

        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            for hop in range(_MAX_DELTA_CHAIN_HOPS + 1):
                if current_blob_id is None:
                    raise CheckpointChainError(
                        f"Delta checkpoint chain ended at hop {hop} before reaching a base snapshot. "
                        "Call force_snapshot(thread_id) to create a new base snapshot."
                    )
                if hop >= _MAX_DELTA_CHAIN_HOPS:
                    raise CheckpointChainError(
                        f"Delta checkpoint chain exceeded 50 hops while reading hop {hop}. "
                        "Call force_snapshot(thread_id) to create a new base snapshot."
                    )

                raw = await walrus.fetch_blob(current_blob_id)
                envelope = _unwrap_blob_payload(current_blob_id, raw)
                envelope["blob_id"] = current_blob_id
                chain.append(envelope)

                if envelope["type"] == "base":
                    break

                parent = envelope.get("parent")
                if parent is not None and not isinstance(parent, str):
                    raise CheckpointChainError(
                        f"Delta blob {current_blob_id!r} at hop {hop} has invalid parent {parent!r}"
                    )
                current_blob_id = parent

        self._last_delta_hops = len(chain)
        print(f"[memwal] Delta chain walk: {self._last_delta_hops} hop(s)")

        payload: Optional[dict[str, Any]] = None
        for envelope in reversed(chain):
            if envelope["type"] == "base":
                payload = envelope.get("payload")
                if not isinstance(payload, dict):
                    raise CheckpointChainError(
                        f"Base blob {envelope['blob_id']!r} missing dict payload"
                    )
            elif envelope["type"] == "delta":
                if payload is None:
                    raise CheckpointChainError(
                        "Cannot apply delta before base payload is loaded"
                    )
                if envelope.get("compact") is True:
                    payload = _apply_compact_delta(payload, envelope, storage_key)
                    continue

                diff = envelope.get("diff")
                if not isinstance(diff, dict):
                    raise CheckpointChainError(
                        f"Delta blob {envelope['blob_id']!r} missing dict diff"
                    )
                payload = _apply_diff(payload, diff)
            else:
                raise CheckpointChainError(
                    f"Unknown checkpoint blob type {envelope.get('type')!r}"
                )

        return payload, latest_blob_id

    async def _alist_internal(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> list[CheckpointTuple]:
        if config is None:
            return []

        tup = await self.aget_tuple(config)
        if tup is None:
            return []

        if before is not None:
            before_id = _extract_checkpoint_id(before)
            if before_id is not None and tup.checkpoint["id"] >= before_id:
                return []

        if limit is not None and limit < 1:
            return []

        return [tup]


def _simulation_checkpoint(step: int) -> dict[str, Any]:
    chunks = {
        f"chunk_{idx}": "x" * (10 * 1024)
        for idx in range(1, step + 1)
    }
    return {
        "id": f"sim-{step}",
        "ts": f"2026-06-17T00:00:0{step}Z",
        "channel_values": {
            "messages": [
                {
                    "type": "human",
                    "content": f"step {step}",
                }
            ],
            "memory": chunks,
            "step": step,
        },
        "channel_versions": {
            "messages": step,
        },
        "versions_seen": {},
        "pending_sends": [],
    }


def _simulation_payload(step: int) -> dict[str, Any]:
    checkpoint = _simulation_checkpoint(step)
    return {
        "checkpoint": checkpoint,
        "metadata": {"source": "phase-7-test", "step": step, "writes": {}},
        "parent_checkpoint_id": f"sim-{step - 1}" if step > 1 else None,
        "checkpoint_ns": "",
    }


def _simulate_naive_payload_bytes(steps: int) -> int:
    return sum(len(_pack_payload(_simulation_payload(step))) for step in range(1, steps + 1))


def _simulate_snapshot_payload_bytes(steps: int, snapshot_every_n: int) -> int:
    total = 0
    for step in range(1, steps + 1):
        if step == 1 or step % snapshot_every_n == 0:
            total += len(_pack_payload(_simulation_payload(step)))
    return total


def _simulate_delta_payload_bytes(steps: int) -> int:
    total = 0
    previous: Optional[dict[str, Any]] = None
    for step in range(1, steps + 1):
        payload = _simulation_payload(step)
        if previous is None:
            envelope = {"type": "base", "payload": payload, "parent": None}
        else:
            diff = _compute_diff(previous, payload)
            envelope = {
                **_compact_delta_envelope("sim", payload, diff, f"sim-blob-{step - 1}"),
            }
        total += len(_pack_payload(envelope))
        previous = payload
    return total


if __name__ == "__main__":
    steps = 5
    snapshot_every_n = 5
    naive_total = _simulate_naive_payload_bytes(steps)
    snapshot_total = _simulate_snapshot_payload_bytes(steps, snapshot_every_n)
    delta_total = _simulate_delta_payload_bytes(steps)

    print("MemWal Phase 7 checkpoint strategy simulation")
    print(f"Simulated steps: {steps}")
    print(f"Snapshot every N: {snapshot_every_n}")
    print()
    print("strategy   uploaded")
    print(f"naive      {_format_kb(naive_total)}")
    print(f"snapshot   {_format_kb(snapshot_total)}")
    print(f"delta      {_format_kb(delta_total)}")
    print()
    print(f"Delta cumulative bytes uploaded: {delta_total}")
    print(f"Snapshot cumulative bytes uploaded: {snapshot_total}")
    print(f"Naive cumulative bytes uploaded: {naive_total}")
    assert delta_total < naive_total
    print("Phase 7 PASS")
