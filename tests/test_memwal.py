from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pytest

from memwal.checkpoint import WalrusCheckpointer
from memwal.config import Config, load_config
from memwal.sui import SuiRegistry
from memwal.walrus import WalrusClient


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}"


def _checkpoint(checkpoint_id: str, message: str) -> dict[str, Any]:
    return {
        "v": 1,
        "id": checkpoint_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {
            "messages": [
                {
                    "type": "human",
                    "content": message,
                }
            ]
        },
        "channel_versions": {
            "messages": 1,
        },
        "versions_seen": {},
        "pending_sends": [],
    }


def _config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
        }
    }


@pytest.fixture(scope="session")
def memwal_config() -> Config:
    return load_config()


@pytest.mark.asyncio
async def test_1_walrus_store_and_fetch(memwal_config: Config) -> None:
    original = b"hello memwal test"

    async with WalrusClient(
        memwal_config.WALRUS_PUBLISHER,
        memwal_config.WALRUS_AGGREGATOR,
    ) as walrus:
        blob_id = await walrus.store_blob(
            original,
            epochs=memwal_config.STORAGE_EPOCHS,
        )
        assert isinstance(blob_id, str)
        assert blob_id
        print(f"blob_id={blob_id}")

        fetched = await walrus.fetch_blob(blob_id)

    assert fetched == original


@pytest.mark.asyncio
async def test_2_sui_register_and_lookup(memwal_config: Config) -> None:
    thread_id = _unique_id("pytest-thread")
    expected_blob_id = "pytest-fake-blob-001"

    async with SuiRegistry(memwal_config) as registry:
        tx_digest = await registry.register_blob(thread_id, expected_blob_id)
        assert isinstance(tx_digest, str)
        assert tx_digest
        print(f"tx_digest={tx_digest}")

        blob_id = await registry.lookup_blob(thread_id)

    assert blob_id == expected_blob_id
    print(f"blob_id={blob_id}")


@pytest.mark.asyncio
async def test_3_checkpoint_put_and_get(memwal_config: Config) -> None:
    checkpointer = WalrusCheckpointer.from_env()
    thread_id = _unique_id("pytest-ckpt")
    message = "hello checkpoint memwal"
    config = _config(thread_id)
    checkpoint = _checkpoint(f"{thread_id}-checkpoint-001", message)
    metadata = {
        "source": "pytest",
        "step": 1,
        "writes": {},
    }

    checkpointer.put(config, checkpoint, metadata, {})
    result = checkpointer.get(config)

    assert result is not None
    assert result["channel_values"]["messages"][0]["content"] == message

    async with SuiRegistry(memwal_config) as registry:
        blob_id = await registry.lookup_blob(thread_id)

    assert isinstance(blob_id, str)
    assert blob_id
    print(f"thread_id={thread_id}")
    print(f"blob_id={blob_id}")


@pytest.mark.asyncio
async def test_4_cross_thread_isolation(memwal_config: Config) -> None:
    checkpointer = WalrusCheckpointer.from_env()
    now = int(time.time())
    thread_a = f"pytest-isolation-a-{now}"
    thread_b = f"pytest-isolation-b-{now}"
    message_a = f"message for {thread_a}"
    message_b = f"message for {thread_b}"

    checkpointer.put(
        _config(thread_a),
        _checkpoint(f"{thread_a}-checkpoint-001", message_a),
        {"source": "pytest", "step": 1, "writes": {}},
        {},
    )
    checkpointer.put(
        _config(thread_b),
        _checkpoint(f"{thread_b}-checkpoint-001", message_b),
        {"source": "pytest", "step": 1, "writes": {}},
        {},
    )

    result_a = checkpointer.get(_config(thread_a))
    result_b = checkpointer.get(_config(thread_b))

    assert result_a is not None
    assert result_b is not None

    actual_a = result_a["channel_values"]["messages"][0]["content"]
    actual_b = result_b["channel_values"]["messages"][0]["content"]

    assert actual_a == message_a
    assert actual_b == message_b
    assert actual_a != actual_b

    async with SuiRegistry(memwal_config) as registry:
        blob_a = await registry.lookup_blob(thread_a)
        blob_b = await registry.lookup_blob(thread_b)

    assert isinstance(blob_a, str)
    assert blob_a
    assert isinstance(blob_b, str)
    assert blob_b
    assert blob_a != blob_b

    print(f"thread_a={thread_a}")
    print(f"blob_a={blob_a}")
    print(f"thread_b={thread_b}")
    print(f"blob_b={blob_b}")
