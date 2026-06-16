from __future__ import annotations
import asyncio
import base64
import json
import os
import sys
import time
import traceback
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
from memwal.sui import SuiRegistry
from memwal.walrus import WalrusClient


PORT = int(os.environ.get("PLAYGROUND_PORT", "8420"))
HOST = os.environ.get("PLAYGROUND_HOST", "0.0.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


try:
    _cfg: Optional[Config] = load_config()
    print(f"[config] Loaded config — RPC: {_cfg.SUI_RPC_URL}")
    print(f"[config] Registry: {_cfg.REGISTRY_OBJECT_ID[:16]}...")
except Exception as exc:
    print(f"[config] Warning: Could not load config: {exc}")
    print("[config] Running in demo mode — API calls will fail")
    _cfg = None


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


class StoreBlobRequest(BaseModel):
    data: str
    epochs: int = 5


class RegisterRequest(BaseModel):
    thread_id: str
    blob_id: str


class CheckpointPutRequest(BaseModel):
    thread_id: str
    data: Any


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
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        import msgpack

        payload = {
            "checkpoint": {
                "id": f"cp-{int(time.time() * 1000)}",
                "ts": time.time(),
                "channel_values": {},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            },
            "metadata": {"source": "playground", "step": 0},
            "parent_checkpoint_id": None,
            "checkpoint_ns": "",
            "user_data": req.data,
        }

        data = msgpack.packb(payload, use_bin_type=True, default=str)

        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as walrus:
            blob_id = await walrus.store_blob(data, epochs=_cfg.STORAGE_EPOCHS)

        async with SuiRegistry(_cfg) as sui:
            digest = await sui.register_blob(req.thread_id, blob_id)

        return {
            "thread_id": req.thread_id,
            "checkpoint_id": payload["checkpoint"]["id"],
            "blob_id": blob_id,
            "digest": digest,
            "size": len(data),
            "explorer_url": f"https://suiscan.xyz/testnet/tx/{digest}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/checkpoint/get/{thread_id}")
async def checkpoint_get(thread_id: str):
    
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        import msgpack
        async with SuiRegistry(_cfg) as sui:
            blob_id = await sui.lookup_blob(thread_id)

        if blob_id is None:
            return {
                "thread_id": thread_id,
                "found": False,
                "blob_id": None,
                "checkpoint": None,
            }

        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as walrus:
            raw = await walrus.fetch_blob(blob_id)

        try:
            payload = msgpack.unpackb(raw, raw=False)
        except Exception:
            
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw_base64": base64.b64encode(raw).decode("ascii")}

        return {
            "thread_id": thread_id,
            "found": True,
            "blob_id": blob_id,
            "size": len(raw),
            "checkpoint": payload,
        }
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