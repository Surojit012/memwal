#!/usr/bin/env python3
"""
playground/server.py — FastAPI bridge server for the MemWal Developer Playground.

Wraps the existing memwal Python package and exposes REST endpoints for
the frontend. Also serves the static playground files.

Usage:
    python playground/server.py
    # or
    uvicorn playground.server:app --host 0.0.0.0 --port 8420 --reload

Endpoints:
    GET  /                          → Landing page
    GET  /playground                → Playground dashboard
    GET  /api/health                → Health check
    GET  /api/config                → Sanitised config (no private keys)
    POST /api/blob/store            → Store a blob on Walrus
    GET  /api/blob/{blob_id}        → Fetch a blob from Walrus
    POST /api/registry/register     → Register thread→blob on Sui
    GET  /api/registry/lookup/{id}  → Lookup blob for thread from Sui
    POST /api/checkpoint/put        → Create a full checkpoint
    GET  /api/checkpoint/get/{id}   → Get the latest checkpoint
    POST /api/file/upload           → Upload a file as a blob
"""

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

# Ensure the project root is on sys.path so `import memwal` works.
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("PLAYGROUND_PORT", "8420"))
HOST = os.environ.get("PLAYGROUND_HOST", "0.0.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Load memwal config (with .env)
try:
    _cfg: Optional[Config] = load_config()
    print(f"[config] Loaded config — RPC: {_cfg.SUI_RPC_URL}")
    print(f"[config] Registry: {_cfg.REGISTRY_OBJECT_ID[:16]}...")
except Exception as exc:
    print(f"[config] Warning: Could not load config: {exc}")
    print("[config] Running in demo mode — API calls will fail")
    _cfg = None

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

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

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class StoreBlobRequest(BaseModel):
    data: str
    epochs: int = 5


class RegisterRequest(BaseModel):
    thread_id: str
    blob_id: str


class CheckpointPutRequest(BaseModel):
    thread_id: str
    data: Any


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def landing_page():
    """Serve the landing page."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/playground", include_in_schema=False)
async def playground_page():
    """Serve the playground dashboard."""
    return FileResponse(str(STATIC_DIR / "playground.html"))


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    """Health check — returns server status and config availability."""
    return {
        "status": "ok",
        "config_loaded": _cfg is not None,
        "timestamp": time.time(),
    }


@app.get("/api/config")
async def get_config():
    """Return sanitised configuration (no private keys)."""
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    return {
        "SUI_RPC_URL": _cfg.SUI_RPC_URL,
        "WALRUS_PUBLISHER": _cfg.WALRUS_PUBLISHER,
        "WALRUS_AGGREGATOR": _cfg.WALRUS_AGGREGATOR,
        "STORAGE_EPOCHS": _cfg.STORAGE_EPOCHS,
        "REGISTRY_PACKAGE_ID": _cfg.REGISTRY_PACKAGE_ID,
        "REGISTRY_OBJECT_ID": _cfg.REGISTRY_OBJECT_ID,
        # Private key is NEVER exposed
    }


@app.post("/api/blob/store")
async def store_blob(req: StoreBlobRequest):
    """Store data as a Walrus blob."""
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
    """Fetch a blob from Walrus."""
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as client:
            data = await client.fetch_blob(blob_id)

        # Try to parse as JSON, fall back to text, fall back to base64
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
    """Register a thread→blob mapping on-chain."""
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
    """Lookup the blob_id for a thread from the on-chain registry."""
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
    """Create a full checkpoint — serialize, store on Walrus, register on Sui."""
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        import msgpack

        # Serialize the data with msgpack
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

        # Store on Walrus
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as walrus:
            blob_id = await walrus.store_blob(data, epochs=_cfg.STORAGE_EPOCHS)

        # Register on Sui
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
    """Retrieve the latest checkpoint for a thread."""
    if _cfg is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    try:
        import msgpack

        # Lookup blob_id on-chain
        async with SuiRegistry(_cfg) as sui:
            blob_id = await sui.lookup_blob(thread_id)

        if blob_id is None:
            return {
                "thread_id": thread_id,
                "found": False,
                "blob_id": None,
                "checkpoint": None,
            }

        # Fetch from Walrus
        async with WalrusClient(_cfg.WALRUS_PUBLISHER, _cfg.WALRUS_AGGREGATOR) as walrus:
            raw = await walrus.fetch_blob(blob_id)

        # Deserialize
        try:
            payload = msgpack.unpackb(raw, raw=False)
        except Exception:
            # If it's not msgpack, try JSON
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
    """Upload a file as a Walrus blob."""
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║      MemWal Developer Playground                    ║")
    print("  ║      http://localhost:{}                          ║".format(PORT))
    print("  ╚══════════════════════════════════════════════════════╝")
    print()

    uvicorn.run(
        "playground.server:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_level="info",
    )
