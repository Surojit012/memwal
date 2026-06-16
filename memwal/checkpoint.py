from __future__ import annotations
import asyncio
from typing import Any, AsyncIterator, Iterator, Optional, Sequence
import msgpack
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
    
    return (config or {}).get("configurable", {}).get("checkpoint_id")


def _make_storage_key(thread_id: str, checkpoint_ns: str) -> str:
    
    if checkpoint_ns:
        return f"{thread_id}:{checkpoint_ns}"
    return thread_id


class WalrusCheckpointer(BaseCheckpointSaver):
    

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._walrus = WalrusClient(
            publisher_url=config.WALRUS_PUBLISHER,
            aggregator_url=config.WALRUS_AGGREGATOR,
        )
        self._sui = SuiRegistry(config)

    
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
        return _run_async(
            self.aput(config, checkpoint, metadata, new_versions)
        )

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

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)
        checkpoint_id = checkpoint["id"]

        payload = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "parent_checkpoint_id": _extract_checkpoint_id(config),
            "checkpoint_ns": checkpoint_ns,
        }
        data = msgpack.packb(payload, use_bin_type=True, default=str)

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
                "checkpoint_id": checkpoint_id,
            },
        }

    async def aget_tuple(
        self, config: RunnableConfig
    ) -> Optional[CheckpointTuple]:
    
        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)

        async with SuiRegistry(self._config) as sui:
            blob_id = await sui.lookup_blob(storage_key)

        if blob_id is None:
            return None

        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            raw = await walrus.fetch_blob(blob_id)

        payload = msgpack.unpackb(raw, raw=False)

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