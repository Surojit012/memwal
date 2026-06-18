from __future__ import annotations

import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from memwal.config import load_config
from memwal.sui import SuiRegistry


async def _register_and_lookup(
    registry: SuiRegistry,
    thread_id: str,
    blob_id: str,
) -> bool:
    print(f"\n[case] thread_id={thread_id!r} blob_id={blob_id!r}")
    digest = await registry.register_blob(thread_id, blob_id)
    print(f"[tx] digest: {digest}")

    result = await registry.lookup_blob(thread_id)
    print(f"[lookup] result: {result!r}")

    passed = result == blob_id
    print(f"[result] {'PASS' if passed else 'FAIL'}")
    return passed


async def main() -> int:
    config = load_config()

    async with SuiRegistry(config) as registry:
        registry_object = await registry._rpc("sui_getObject", [
            config.REGISTRY_OBJECT_ID,
            {
                "showContent": True,
                "showOwner": False,
                "showPreviousTransaction": False,
                "showStorageRebate": False,
                "showDisplay": False,
                "showType": True,
            },
        ])
        print("[registry] raw sui_getObject response before lookups:")
        print(repr(registry_object))

        static_ok = await _register_and_lookup(
            registry,
            "debug-test-static",
            "fake-blob-aaa",
        )
        numeric_ok = await _register_and_lookup(
            registry,
            "debug-test-1781681686",
            "fake-blob-bbb",
        )

    print("\n[summary]")
    print(f"debug-test-static:     {'PASS' if static_ok else 'FAIL'}")
    print(f"debug-test-1781681686: {'PASS' if numeric_ok else 'FAIL'}")

    return 0 if static_ok and numeric_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
