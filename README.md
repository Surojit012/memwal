# MemWal

MemWal is a Walrus-backed checkpoint backend for LangGraph agents, with Sui
testnet used as the on-chain registry for `thread_id -> blob_id` mappings.

```python
from memwal.checkpoint import WalrusCheckpointer

checkpointer = WalrusCheckpointer.from_env()
graph = builder.compile(checkpointer=checkpointer)
```

## Project Layout

```text
memwal/
  config.py       Environment loading and validation
  walrus.py       Walrus publisher/aggregator client
  sui.py          Sui JSON-RPC registry client
  checkpoint.py   LangGraph checkpoint saver
agents/
  demo.py         End-to-end demo agent
contracts/
  sources/        Sui Move registry contract
playground/
  server.py       FastAPI playground used by Vercel
api/
  index.py        Vercel ASGI entrypoint
tests/
  test_memwal.py  Live testnet integration tests
```

## Local Setup

```zsh
cd /Users/surojitpvt/Downloads/memwal-main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev,llm]"
```

Copy `.env.example` to `.env` and set:

```env
SUI_PRIVATE_KEY=...
REGISTRY_PACKAGE_ID=...
REGISTRY_OBJECT_ID=...
STRAICO_API_KEY=...
```

## Run the Demo

```zsh
source .venv/bin/activate
python agents/demo.py
```

## Run the Playground Locally

```zsh
source .venv/bin/activate
uvicorn playground.server:app --host 0.0.0.0 --port 8420 --reload
```

Open:

```text
http://localhost:8420
http://localhost:8420/playground
```

## Run Integration Tests

These tests use live Walrus and Sui testnet resources.

```zsh
source .venv/bin/activate
pytest tests/ -v -s
```

## Vercel Deployment

The Vercel entrypoint is `api/index.py`, which serves the FastAPI app from
`playground.server:app`.

Set these Vercel environment variables before deploying:

```text
SUI_PRIVATE_KEY
REGISTRY_PACKAGE_ID
REGISTRY_OBJECT_ID
SUI_RPC_URL
WALRUS_PUBLISHER
WALRUS_AGGREGATOR
STORAGE_EPOCHS
STRAICO_API_KEY
STRAICO_MODEL
```

Deploy with:

```zsh
vercel
```

Production deploy:

```zsh
vercel --prod
```

## Move Contract

```zsh
cd contracts
sui move build
sui move test
sui move publish --gas-budget 100000000
```
