# MemWal — Skills & Tech Stack

## What This Project Is
MemWal is a Python package that makes Walrus (on Sui blockchain) a drop-in
checkpoint backend for LangGraph agents. Developers replace SqliteSaver with
WalrusCheckpointer in one line.

## Core Tech Stack
- Python 3.11+
- LangGraph (checkpoint backend protocol)
- pysui (Sui blockchain interaction)
- httpx (async HTTP for Walrus API)
- msgpack (state serialization)
- python-dotenv (config management)
- Sui Move (smart contract for thread→blob registry)

## Network Config
- Sui: testnet (https://fullnode.testnet.sui.io:443)
- Walrus Publisher: https://publisher.walrus-testnet.walrus.space
- Walrus Aggregator: https://aggregator.walrus-testnet.walrus.space

## File Structure
memwal-langgraph/
├── memwal/
│   ├── __init__.py
│   ├── checkpoint.py      ← WalrusCheckpointer (LangGraph drop-in)
│   ├── walrus.py          ← Walrus HTTP client
│   ├── sui.py             ← Sui tx signing + registry lookup
│   └── config.py          ← env var loader
├── agents/
│   └── demo.py            ← end-to-end demo agent
├── contracts/
│   └── registry.move      ← Move contract: thread_id → blob_id
├── .env.example
├── pyproject.toml
└── README.md

## Data Flow
PUT:  state → msgpack serialize → POST Walrus → blob_id → Sui Move tx (thread_id → blob_id)
GET:  thread_id → Sui lookup → blob_id → GET Walrus → msgpack deserialize → state

## Coding Rules
- All Walrus/Sui calls are async (asyncio)
- Use bridge (asyncio.run or nest_asyncio) for sync LangGraph interface
- Custom exceptions: WalrusError, SuiRegistryError
- No placeholders — every method must be fully implemented
- Config loaded once via load_config() from environment