"""
memwal.checkpoint — WalrusCheckpointer: LangGraph drop-in checkpoint backend.

Replaces ``SqliteSaver`` (or any other ``BaseCheckpointSaver``) with one
line::

    from memwal.checkpoint import WalrusCheckpointer
    checkpointer = WalrusCheckpointer.from_env()
    graph = builder.compile(checkpointer=checkpointer)

Checkpoint data is serialised with msgpack, stored on Walrus, and the
``thread_id → blob_id`` mapping is recorded on-chain via the Sui registry
contract.

Because LangGraph's ``BaseCheckpointSaver`` interface is synchronous while
Walrus/Sui calls are async, this module uses ``asyncio.run()`` (or the
existing loop + ``nest_asyncio``) to bridge the gap transparently.
"""

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


# ---------------------------------------------------------------------------
# Async-to-sync bridge
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from synchronous code.

    If an event loop is already running (e.g. inside Jupyter or an async
    framework), ``nest_asyncio`` is applied automatically so that
    ``asyncio.run()`` doesn't raise ``RuntimeError``.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We are inside an already-running event loop — apply nest_asyncio
        # so we can call asyncio.run() without "cannot be called from a
        # running event loop".
        import nest_asyncio  # type: ignore[import-untyped]
        nest_asyncio.apply(loop)

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_thread_id(config: RunnableConfig) -> str:
    """Extract ``thread_id`` from a LangGraph config, raising on miss."""
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError(
            "Config must contain configurable.thread_id. "
            f"Got config: {config!r}"
        )
    return str(thread_id)


def _extract_checkpoint_ns(config: RunnableConfig) -> str:
    """Extract ``checkpoint_ns`` from a LangGraph config (default "")."""
    return (config or {}).get("configurable", {}).get("checkpoint_ns", "")


def _extract_checkpoint_id(config: RunnableConfig) -> Optional[str]:
    """Extract ``checkpoint_id`` from a LangGraph config (may be None)."""
    return (config or {}).get("configurable", {}).get("checkpoint_id")


def _make_storage_key(thread_id: str, checkpoint_ns: str) -> str:
    """Build the on-chain storage key used for the Sui registry lookup.

    For the default namespace (``""``), the key is just ``thread_id``.
    For sub-graphs / namespaced checkpoints the key is
    ``thread_id:checkpoint_ns``.
    """
    if checkpoint_ns:
        return f"{thread_id}:{checkpoint_ns}"
    return thread_id


# ---------------------------------------------------------------------------
# WalrusCheckpointer
# ---------------------------------------------------------------------------

class WalrusCheckpointer(BaseCheckpointSaver):
    """LangGraph checkpoint saver backed by Walrus + Sui.

    Every :meth:`put` serialises the checkpoint with msgpack, stores the
    blob on Walrus, and records the ``thread_id → blob_id`` mapping on the
    Sui blockchain.  :meth:`get_tuple` reverses the process.

    Parameters
    ----------
    config : Config
        A :class:`memwal.config.Config` instance with all required fields.
    """

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._walrus = WalrusClient(
            publisher_url=config.WALRUS_PUBLISHER,
            aggregator_url=config.WALRUS_AGGREGATOR,
        )
        self._sui = SuiRegistry(config)

    # -- Factory ----------------------------------------------------------- #

    @classmethod
    def from_env(cls, dotenv_path=None) -> "WalrusCheckpointer":
        """Create a :class:`WalrusCheckpointer` from environment variables.

        Convenience wrapper that calls :func:`memwal.config.load_config`
        and passes the result to the constructor.
        """
        cfg = load_config(dotenv_path=dotenv_path)
        return cls(cfg)

    # -- Sync interface (bridges to async) --------------------------------- #

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
        # Walrus is a blob store, not a WAL.  Pending writes are folded
        # into the next full checkpoint via put().  This is intentionally
        # a no-op — the same strategy used by MemorySaver for simple
        # checkpoint backends.
        return None

    # -- Async interface (primary implementation) -------------------------- #

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Serialize and store a checkpoint on Walrus, register on Sui."""

        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)
        checkpoint_id = checkpoint["id"]

        # Pack checkpoint + metadata into a single msgpack blob.
        payload = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "parent_checkpoint_id": _extract_checkpoint_id(config),
            "checkpoint_ns": checkpoint_ns,
        }
        data = msgpack.packb(payload, use_bin_type=True, default=str)

        # Store on Walrus.
        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            blob_id = await walrus.store_blob(data, epochs=self._config.STORAGE_EPOCHS)

        # Register thread → blob mapping on Sui.
        async with SuiRegistry(self._config) as sui:
            await sui.register_blob(storage_key, blob_id)

        # Return updated config pointing at the new checkpoint.
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
        """Fetch the latest checkpoint for a thread from Walrus via Sui."""

        thread_id = _extract_thread_id(config)
        checkpoint_ns = _extract_checkpoint_ns(config)
        storage_key = _make_storage_key(thread_id, checkpoint_ns)

        # Look up the blob_id on-chain.
        async with SuiRegistry(self._config) as sui:
            blob_id = await sui.lookup_blob(storage_key)

        if blob_id is None:
            return None

        # Fetch the blob from Walrus.
        async with WalrusClient(
            self._config.WALRUS_PUBLISHER,
            self._config.WALRUS_AGGREGATOR,
        ) as walrus:
            raw = await walrus.fetch_blob(blob_id)

        # Deserialize.
        payload = msgpack.unpackb(raw, raw=False)

        checkpoint: Checkpoint = payload["checkpoint"]
        metadata: CheckpointMetadata = payload.get("metadata", {})
        parent_checkpoint_id: Optional[str] = payload.get("parent_checkpoint_id")

        # Build parent_config if a parent checkpoint exists.
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
        """Yield checkpoint tuples.

        Walrus is a blob store, not a log-structured database — only the
        latest checkpoint per thread is retained.  This method yields at
        most one ``CheckpointTuple``.
        """
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
        # Intentional no-op — see put_writes docstring.
        return None

    # -- Internal helpers -------------------------------------------------- #

    async def _alist_internal(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> list[CheckpointTuple]:
        """Materialise the list results into a plain list (used by both
        sync ``list`` and async ``alist``)."""
        if config is None:
            return []

        tup = await self.aget_tuple(config)
        if tup is None:
            return []

        # Apply ``before`` filter — if the stored checkpoint is not before
        # the requested one, skip it.
        if before is not None:
            before_id = _extract_checkpoint_id(before)
            if before_id is not None and tup.checkpoint["id"] >= before_id:
                return []

        # Apply ``limit``.
        if limit is not None and limit < 1:
            return []

        return [tup]
