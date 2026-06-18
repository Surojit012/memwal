from __future__ import annotations
import asyncio
import base64
import contextvars
import json
import os
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import uvicorn
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError as exc:
    print(f"[error] Missing dependency: {exc}")
    print("Install with:  pip install fastapi uvicorn python-multipart")
    sys.exit(1)

from memwal.config import Config, load_config
from memwal.checkpoint import WalrusCheckpointer
from memwal.sui import SuiRegistry
from memwal.walrus import WalrusClient


PORT = int(os.environ.get("PLAYGROUND_PORT", "8420"))
HOST = os.environ.get("PLAYGROUND_HOST", "0.0.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SVG_DIR = Path(__file__).resolve().parent / "svg"


try:
    _cfg: Optional[Config] = load_config()
    print(f"[config] Loaded config — RPC: {_cfg.SUI_RPC_URL}")
    print(f"[config] Registry: {_cfg.REGISTRY_OBJECT_ID[:16]}...")
except Exception as exc:
    print(f"[config] Warning: Could not load config: {exc}")
    print("[config] Running in demo mode — API calls will fail")
    _cfg = None


_TRACE: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "memwal_playground_trace",
    default=None,
)


def _install_checkpointer_tracing() -> None:
    import memwal.checkpoint as checkpoint_module

    if getattr(checkpoint_module, "_memwal_playground_tracing_installed", False):
        return

    original_walrus_client = checkpoint_module.WalrusClient
    original_sui_registry = checkpoint_module.SuiRegistry

    class PlaygroundTracingWalrusClient(original_walrus_client):
        async def store_blob(self, data: bytes, epochs: int) -> str:
            blob_id = await super().store_blob(data, epochs)
            trace = _TRACE.get()
            if trace is not None:
                trace["blob_ids"].append(blob_id)
                trace["uploaded_bytes"] += len(data)
                trace["events"].append(
                    {
                        "type": "walrus.store",
                        "blob_id": blob_id,
                        "bytes": len(data),
                        "epochs": epochs,
                    }
                )
            return blob_id

        async def fetch_blob(self, blob_id: str) -> bytes:
            data = await super().fetch_blob(blob_id)
            trace = _TRACE.get()
            if trace is not None:
                trace["fetched_bytes"] += len(data)
                trace["events"].append(
                    {
                        "type": "walrus.fetch",
                        "blob_id": blob_id,
                        "bytes": len(data),
                    }
                )
            return data

    class PlaygroundTracingSuiRegistry(original_sui_registry):
        async def register_blob(self, thread_id: str, blob_id: str) -> str:
            digest = await super().register_blob(thread_id, blob_id)
            trace = _TRACE.get()
            if trace is not None:
                trace["tx_digests"].append(digest)
                trace["events"].append(
                    {
                        "type": "sui.register",
                        "thread_id": thread_id,
                        "blob_id": blob_id,
                        "tx_digest": digest,
                    }
                )
            return digest

        async def lookup_blob(self, thread_id: str) -> Optional[str]:
            blob_id = await super().lookup_blob(thread_id)
            trace = _TRACE.get()
            if trace is not None:
                trace["lookups"].append(
                    {
                        "thread_id": thread_id,
                        "blob_id": blob_id,
                    }
                )
                trace["events"].append(
                    {
                        "type": "sui.lookup",
                        "thread_id": thread_id,
                        "blob_id": blob_id,
                    }
                )
            return blob_id

    checkpoint_module.WalrusClient = PlaygroundTracingWalrusClient
    checkpoint_module.SuiRegistry = PlaygroundTracingSuiRegistry
    checkpoint_module._memwal_playground_tracing_installed = True


_install_checkpointer_tracing()


def _new_trace() -> dict[str, Any]:
    return {
        "blob_ids": [],
        "tx_digests": [],
        "uploaded_bytes": 0,
        "fetched_bytes": 0,
        "lookups": [],
        "events": [],
    }


def _require_config() -> Config:
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    return _cfg


def _checkpointer_config(strategy: str = "snapshot") -> Config:
    cfg = _require_config()
    return replace(cfg, CHECKPOINT_STRATEGY=strategy)


def _message_record(role: str, content: str) -> dict[str, str]:
    return {
        "role": role,
        "content": content,
    }


def _coerce_message_records(data: Any) -> list[dict[str, str]]:
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        raw_messages = data["messages"]
    elif isinstance(data, list):
        raw_messages = data
    else:
        raw_messages = [{"role": "human", "content": str(data)}]

    records: list[dict[str, str]] = []
    for item in raw_messages:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("type") or "human")
            content = str(item.get("content", ""))
        else:
            role = "human"
            content = str(item)
        if role == "user":
            role = "human"
        if role == "assistant":
            role = "ai"
        records.append(_message_record(role, content))
    return records


def _checkpoint(
    thread_id: str,
    step: int,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "v": 1,
        "id": f"{thread_id}-checkpoint-{step}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {
            "messages": messages,
        },
        "channel_versions": {
            "messages": step,
        },
        "versions_seen": {},
        "pending_sends": [],
    }


def _metadata(source: str, step: int) -> dict[str, Any]:
    return {
        "source": source,
        "step": step,
        "writes": {},
    }


def _message_text(checkpoint: dict[str, Any]) -> str:
    messages = checkpoint.get("channel_values", {}).get("messages", [])
    return "\n".join(str(message.get("content", "")) for message in messages)


def _run_with_trace(fn):
    trace = _new_trace()
    token = _TRACE.set(trace)
    started = time.time()
    try:
        result = fn(trace)
        if isinstance(result, dict):
            result.setdefault("trace", trace)
            result.setdefault("elapsed_ms", int((time.time() - started) * 1000))
        return result
    finally:
        _TRACE.reset(token)


def _put_checkpoint(
    checkpointer: WalrusCheckpointer,
    thread_id: str,
    messages: list[dict[str, str]],
    *,
    step: int,
    source: str,
    force_snapshot: bool = False,
) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint = _checkpoint(thread_id, step, messages)
    checkpointer.put(config, checkpoint, _metadata(source, step), {})
    if force_snapshot and getattr(checkpointer, "_strategy", "") == "snapshot" and step != 1:
        checkpointer.force_snapshot(thread_id)
    return checkpoint


def _latest_blob_id(trace: dict[str, Any]) -> Optional[str]:
    if trace["blob_ids"]:
        return trace["blob_ids"][-1]
    if trace["lookups"]:
        return trace["lookups"][-1].get("blob_id")
    return None


def _latest_tx_digest(trace: dict[str, Any]) -> Optional[str]:
    if trace["tx_digests"]:
        return trace["tx_digests"][-1]
    return None


app = FastAPI(
    title="MemWal Playground",
    description="Developer playground for MemWal — Walrus-backed LangGraph checkpoint storage on Sui",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/svg", StaticFiles(directory=str(SVG_DIR)), name="svg")


class StoreBlobRequest(BaseModel):
    data: str
    epochs: int = 5


class RegisterRequest(BaseModel):
    thread_id: str
    blob_id: str


class CheckpointPutRequest(BaseModel):
    thread_id: str
    data: Any
    strategy: str = "snapshot"
    force_snapshot: bool = True


class ProofBenchmarkRequest(BaseModel):
    steps: int = 5
    strategy: str = "both"


@app.get("/", include_in_schema=False)
async def landing_page():
    
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/playground", include_in_schema=False)
async def playground_page():
    
    return FileResponse(str(STATIC_DIR / "playground.html"))


@app.get("/api/health")
async def health():
    
    return {
        "status": "ok",
        "config_loaded": _cfg is not None,
        "timestamp": time.time(),
    }


@app.get("/api/config")
async def get_config():
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    return {
        "SUI_RPC_URL": _cfg.SUI_RPC_URL,
        "WALRUS_PUBLISHER": _cfg.WALRUS_PUBLISHER,
        "WALRUS_AGGREGATOR": _cfg.WALRUS_AGGREGATOR,
        "STORAGE_EPOCHS": _cfg.STORAGE_EPOCHS,
        "REGISTRY_PACKAGE_ID": _cfg.REGISTRY_PACKAGE_ID,
        "REGISTRY_OBJECT_ID": _cfg.REGISTRY_OBJECT_ID,
        
    }


@app.post("/api/blob/store")
async def store_blob(req: StoreBlobRequest):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        data_bytes = req.data.encode("utf-8")
    except Exception:
        data_bytes = base64.b64decode(req.data)

    try:
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as client:
            blob_id = await client.store_blob(data_bytes, epochs=req.epochs)
        return {
            "blob_id": blob_id,
            "size": len(data_bytes),
            "epochs": req.epochs,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/blob/{blob_id}")
async def fetch_blob(blob_id: str):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as client:
            data = await client.fetch_blob(blob_id)

        
        try:
            parsed = json.loads(data)
            return {"blob_id": blob_id, "size": len(data), "format": "json", "data": parsed}
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        try:
            text = data.decode("utf-8")
            return {"blob_id": blob_id, "size": len(data), "format": "text", "data": text}
        except UnicodeDecodeError:
            pass

        return {
            "blob_id": blob_id,
            "size": len(data),
            "format": "base64",
            "data": base64.b64encode(data).decode("ascii"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/registry/register")
async def register_thread(req: RegisterRequest):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        async with SuiRegistry(_cfg) as registry:
            digest = await registry.register_blob(req.thread_id, req.blob_id)
        return {
            "thread_id": req.thread_id,
            "blob_id": req.blob_id,
            "digest": digest,
            "explorer_url": f"https://suiscan.xyz/testnet/tx/{digest}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/registry/lookup/{thread_id}")
async def lookup_thread(thread_id: str):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        async with SuiRegistry(_cfg) as registry:
            blob_id = await registry.lookup_blob(thread_id)
        return {
            "thread_id": thread_id,
            "blob_id": blob_id,
            "found": blob_id is not None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/checkpoint/put")
async def checkpoint_put(req: CheckpointPutRequest):
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            checkpointer = WalrusCheckpointer(_checkpointer_config(req.strategy))
            messages = _coerce_message_records(req.data)
            checkpoint = _put_checkpoint(
                checkpointer,
                req.thread_id,
                messages,
                step=1,
                source="playground",
                force_snapshot=req.force_snapshot,
            )
            return {
                "thread_id": req.thread_id,
                "checkpoint_id": checkpoint["id"],
                "strategy": req.strategy,
                "message_count": len(messages),
                "blob_id": _latest_blob_id(trace),
                "digest": _latest_tx_digest(trace),
                "size": trace["uploaded_bytes"],
                "explorer_url": (
                    f"https://suiscan.xyz/testnet/tx/{_latest_tx_digest(trace)}"
                    if _latest_tx_digest(trace)
                    else None
                ),
                "walrus_url": (
                    "https://aggregator.walrus-testnet.walrus.space/v1/blobs/"
                    f"{_latest_blob_id(trace)}"
                    if _latest_blob_id(trace)
                    else None
                ),
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/checkpoint/get/{thread_id}")
async def checkpoint_get(thread_id: str, strategy: str = "snapshot"):
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            checkpointer = WalrusCheckpointer(_checkpointer_config(strategy))
            checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})
            if checkpoint is None:
                return {
                    "thread_id": thread_id,
                    "found": False,
                    "blob_id": _latest_blob_id(trace),
                    "checkpoint": None,
                }
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            return {
                "thread_id": thread_id,
                "found": True,
                "strategy": strategy,
                "blob_id": _latest_blob_id(trace),
                "size": trace["fetched_bytes"],
                "message_count": len(messages),
                "checkpoint": checkpoint,
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/proof/basic")
async def proof_basic():
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            thread_id = f"playground-basic-{int(time.time())}"
            checkpointer = WalrusCheckpointer(_checkpointer_config("snapshot"))
            messages = [
                _message_record("human", "Hello, remember me!"),
                _message_record("ai", "Stored on Walrus and registered on Sui."),
            ]
            _put_checkpoint(
                checkpointer,
                thread_id,
                messages,
                step=1,
                source="playground-basic-proof",
                force_snapshot=True,
            )

            restored_checkpointer = WalrusCheckpointer(_checkpointer_config("snapshot"))
            restored = restored_checkpointer.get(
                {"configurable": {"thread_id": thread_id}}
            )
            restored_text = _message_text(restored) if restored else ""
            passed = restored is not None and "Hello, remember me!" in restored_text
            if not passed:
                raise AssertionError("Basic restore proof failed")

            return {
                "proof": "basic_restore",
                "status": "PASS",
                "thread_id": thread_id,
                "blob_id": _latest_blob_id(trace),
                "tx_digest": _latest_tx_digest(trace),
                "message_count": len(
                    restored.get("channel_values", {}).get("messages", [])
                ),
                "restored_text": restored_text,
                "checks": {
                    "sui_lookup": True,
                    "walrus_fetch": True,
                    "checkpoint_restored": True,
                },
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _benchmark_strategy(strategy: str, steps: int) -> dict[str, Any]:
    import msgpack

    if steps < 1:
        raise ValueError("steps must be >= 1")
    if strategy not in {"snapshot", "delta"}:
        raise ValueError("strategy must be 'snapshot' or 'delta'")

    thread_id = f"playground-{strategy}-{steps}-{int(time.time())}"
    checkpointer = WalrusCheckpointer(_checkpointer_config(strategy))
    messages: list[dict[str, str]] = []
    naive_total = 0

    for step in range(1, steps + 1):
        messages.extend(
            [
                _message_record("human", f"Playground benchmark human step {step}"),
                _message_record("ai", f"Playground benchmark assistant step {step}"),
            ]
        )
        checkpoint = _checkpoint(thread_id, step, messages)
        naive_total += len(
            msgpack.packb(
                {
                    "checkpoint": checkpoint,
                    "metadata": _metadata("playground-benchmark-naive", step),
                    "parent_checkpoint_id": None,
                    "checkpoint_ns": "",
                },
                use_bin_type=True,
                default=str,
            )
        )
        checkpointer.put(
            {"configurable": {"thread_id": thread_id}},
            checkpoint,
            _metadata("playground-benchmark", step),
            {},
        )

    restored_checkpointer = WalrusCheckpointer(_checkpointer_config(strategy))
    restored = restored_checkpointer.get({"configurable": {"thread_id": thread_id}})
    restored_messages = restored.get("channel_values", {}).get("messages", []) if restored else []
    expected_count = steps * 2
    integrity = (
        restored is not None
        and len(restored_messages) == expected_count
        and restored_messages[-1]["content"] == f"Playground benchmark assistant step {steps}"
    )

    return {
        "strategy": strategy,
        "thread_id": thread_id,
        "steps": steps,
        "uploaded": _TRACE.get()["uploaded_bytes"] if _TRACE.get() else 0,
        "naive": naive_total,
        "savings_pct": (
            ((naive_total - (_TRACE.get()["uploaded_bytes"] if _TRACE.get() else 0)) / naive_total) * 100
            if naive_total
            else 0.0
        ),
        "integrity": "PASS" if integrity else "FAIL",
        "message_count": len(restored_messages),
        "expected_message_count": expected_count,
        "delta_hops": getattr(restored_checkpointer, "_last_delta_hops", 0),
    }


@app.post("/api/proof/benchmark")
async def proof_benchmark(req: ProofBenchmarkRequest):
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            if req.strategy == "both":
                strategies = ["snapshot", "delta"]
            else:
                strategies = [req.strategy]

            results = []
            for strategy in strategies:
                before_upload = trace["uploaded_bytes"]
                before_blob_count = len(trace["blob_ids"])
                before_tx_count = len(trace["tx_digests"])
                result = _benchmark_strategy(strategy, req.steps)
                result["uploaded"] = trace["uploaded_bytes"] - before_upload
                result["blob_ids"] = trace["blob_ids"][before_blob_count:]
                result["tx_digests"] = trace["tx_digests"][before_tx_count:]
                if result["naive"]:
                    result["savings_pct"] = (
                        (result["naive"] - result["uploaded"]) / result["naive"]
                    ) * 100
                results.append(result)

            return {
                "proof": "strategy_benchmark",
                "status": (
                    "PASS"
                    if all(result["integrity"] == "PASS" for result in results)
                    else "FAIL"
                ),
                "steps": req.steps,
                "results": results,
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/proof/cross-machine")
async def proof_cross_machine():
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            thread_id = f"playground-cross-machine-{int(time.time())}"

            machine_a = WalrusCheckpointer(_checkpointer_config("snapshot"))
            machine_a_messages = [
                _message_record("human", "My name is Surojit."),
                _message_record("human", "I live in Assam."),
                _message_record("human", "My favourite language is Python."),
            ]
            _put_checkpoint(
                machine_a,
                thread_id,
                machine_a_messages,
                step=1,
                source="playground-cross-machine",
                force_snapshot=True,
            )
            del machine_a
            del machine_a_messages

            machine_b = WalrusCheckpointer(_checkpointer_config("snapshot"))
            restored = machine_b.get({"configurable": {"thread_id": thread_id}})
            if restored is None:
                raise AssertionError("Machine B could not restore checkpoint")

            restored_messages = restored["channel_values"]["messages"]
            restored_text = _message_text(restored)
            checks = {
                "name": "Surojit" in restored_text,
                "location": "Assam" in restored_text,
                "favorite_language": "Python" in restored_text,
                "history_restored": len(restored_messages) >= 3,
            }
            if not all(checks.values()):
                raise AssertionError(f"Cross-machine checks failed: {checks}")

            del machine_b

            return {
                "proof": "cross_machine",
                "status": "PASS",
                "thread_id": thread_id,
                "blob_id": _latest_blob_id(trace),
                "tx_digest": _latest_tx_digest(trace),
                "message_count": len(restored_messages),
                "restored_text": restored_text,
                "checks": checks,
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/proof/isolation")
async def proof_isolation():
    try:
        def work(trace: dict[str, Any]) -> dict[str, Any]:
            now = int(time.time())
            threads = {
                "A": {
                    "thread_id": f"playground-isolation-a-{now}",
                    "content": "I love pizza.",
                    "must_include": "pizza",
                    "must_exclude": ["football", "Python"],
                },
                "B": {
                    "thread_id": f"playground-isolation-b-{now}",
                    "content": "I love football.",
                    "must_include": "football",
                    "must_exclude": ["pizza", "Python"],
                },
                "C": {
                    "thread_id": f"playground-isolation-c-{now}",
                    "content": "I love Python.",
                    "must_include": "Python",
                    "must_exclude": ["pizza", "football"],
                },
            }

            ids = [item["thread_id"] for item in threads.values()]
            if len(set(ids)) != len(ids):
                raise AssertionError("Thread collision detected")

            for item in threads.values():
                checkpointer = WalrusCheckpointer(_checkpointer_config("snapshot"))
                _put_checkpoint(
                    checkpointer,
                    item["thread_id"],
                    [_message_record("human", item["content"])],
                    step=1,
                    source="playground-isolation",
                    force_snapshot=True,
                )
                del checkpointer

            results: dict[str, Any] = {}
            for label, item in threads.items():
                checkpointer = WalrusCheckpointer(_checkpointer_config("snapshot"))
                restored = checkpointer.get(
                    {"configurable": {"thread_id": item["thread_id"]}}
                )
                if restored is None:
                    raise AssertionError(f"Thread {label} did not restore")
                restored_text = _message_text(restored)
                isolated = (
                    item["must_include"] in restored_text
                    and all(
                        excluded not in restored_text
                        for excluded in item["must_exclude"]
                    )
                )
                if not isolated:
                    raise AssertionError(f"Thread {label} contamination detected")
                results[label] = {
                    "thread_id": item["thread_id"],
                    "restored_text": restored_text,
                    "contamination": "PASS",
                    "message_count": len(
                        restored.get("channel_values", {}).get("messages", [])
                    ),
                }
                del checkpointer

            return {
                "proof": "multi_thread_isolation",
                "status": "PASS",
                "checks": {
                    "no_thread_collisions": True,
                    "thread_a_contamination": "PASS",
                    "thread_b_contamination": "PASS",
                    "thread_c_contamination": "PASS",
                },
                "threads": results,
                "blob_ids": trace["blob_ids"],
                "tx_digests": trace["tx_digests"],
            }

        return _run_with_trace(work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/file/upload")
async def upload_file(file: UploadFile = File(...)):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        data = await file.read()
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as client:
            blob_id = await client.store_blob(data, epochs=_cfg.STORAGE_EPOCHS)
        return {
            "filename": file.filename,
            "blob_id": blob_id,
            "size": len(data),
            "content_type": file.content_type,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

if __name__ == "__main__":
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║      MemWal Developer Playground                     ║")
    print("  ║      http://localhost:{}                             ║".format(PORT))
    print("  ╚══════════════════════════════════════════════════════╝")
    print()

    uvicorn.run(
        "playground.server:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_level="info",
    )
