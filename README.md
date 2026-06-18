# 🧠 MemWal

![PyPI](https://img.shields.io/pypi/v/memwal-checkpoint)

![Python](https://img.shields.io/badge/Python-3.10+-blue)

![LangGraph](https://img.shields.io/badge/LangGraph-Supported-green)

![Sui + Walrus](https://img.shields.io/badge/Sui+Walrus-Testnet-purple)

> Portable memory for AI agents.

A decentralized LangGraph checkpoint backend powered by Walrus and Sui.

**Kill the machine. Start another one. The agent continues.**

## 🚀 TL;DR

```text
Today:

AI Agent

↓

Local RAM

↓

Process dies

↓

Memory lost


With MemWal:

AI Agent

↓

Walrus

↓

Sui

↓

Resume anywhere
```

MemWal lets LangGraph agents survive machine restarts without relying on SQLite, Redis, or local files.

## Why MemWal?

Most AI agent memory is tied to the machine where the agent is running. If the process exits, the container restarts, or the developer switches machines, the agent's state often disappears with it unless a local database or custom persistence layer has been wired in.

Without MemWal:

```text
AI Agent

↓

Local RAM

↓

Machine dies

↓

Memory lost
```

With MemWal:

```text
AI Agent

↓

Walrus

↓

Sui

↓

Memory survives
```

MemWal replaces local-only checkpoint storage with decentralized persistence while keeping the LangGraph developer experience familiar.

## 30-second Quickstart

Install:

```bash
pip install memwal-checkpoint
```

Use:

```python
from memwal import WalrusCheckpointer

checkpointer = WalrusCheckpointer.from_env()

graph = builder.compile(
    checkpointer=checkpointer
)
```

MemWal replaces local-only LangGraph checkpoint storage with decentralized persistence powered by Walrus and Sui.

## Architecture

```text
LangGraph Agent

↓

WalrusCheckpointer

↓

Walrus (blob storage)

↓

Sui (thread_id → blob_id registry)

↓

Resume anywhere
```

At a high level, MemWal stores checkpoint bytes in Walrus and records the latest blob ID for each `thread_id` in a Sui registry object.

## ✅ What We Verified

| Capability             | Status |
| ---------------------- | ------ |
| Snapshot checkpoints   | ✅      |
| Delta checkpoints      | ✅      |
| Cross-machine recovery | ✅      |
| Multi-thread isolation | ✅      |
| Storage benchmarks     | ✅      |
| Walrus integration     | ✅      |
| Sui integration        | ✅      |
| GitHub CI              | ✅      |

## 🌍 Why this matters

Today's agent memory is usually tied to:

* RAM
* SQLite
* Redis
* Local files

If the machine disappears, the memory disappears.

MemWal decouples memory from compute.

An agent can stop running on one machine and continue running on another by restoring its state from Walrus and Sui.

## Features

* Snapshot checkpoints
* Delta checkpoints
* Cross-machine recovery
* Multi-thread isolation
* On-chain thread registry
* Storage benchmarks
* GitHub CI

## Installation

```bash
pip install memwal-checkpoint
```

> **Note**
>
> MemWal is the product name.
>
> `memwal-checkpoint` is the Python package distribution name.
>
> Imports remain:
>
> ```python
> from memwal import WalrusCheckpointer
> ```

## 🛠️ Development

Install local development dependencies:

```bash
pip install -r requirements.txt
```

Install the package:

```bash
pip install -e .
```

## ⚡ One-line integration

Replace:

```python
checkpointer = MemorySaver()
```

with:

```python
checkpointer = WalrusCheckpointer.from_env()
```

That's it.

Your LangGraph agent now stores memory on Walrus and uses Sui as a decentralized thread registry.

## Quickstart

```python
from memwal import WalrusCheckpointer

checkpointer = WalrusCheckpointer.from_env()

graph = builder.compile(
    checkpointer=checkpointer
)
```

## Production Verification

MemWal has been verified end-to-end.

✅ Live Walrus testnet writes

✅ Live Sui registry transactions

✅ Cross-machine recovery

✅ Multi-thread isolation

✅ Snapshot strategy

✅ Delta strategy

✅ GitHub CI

✅ PyPI publish

✅ Fresh install from PyPI

## Live Demo Results

The demo runner verifies MemWal against live Walrus and Sui testnet infrastructure.

5 steps:

| strategy | savings |
| -------- | ------- |
| snapshot | 60.87%  |
| delta    | 53.75%  |

20 steps:

| strategy | savings |
| -------- | ------- |
| snapshot | 75.66%  |
| delta    | 81.01%  |

Delta mode becomes more efficient as conversations grow because it stores incremental changes instead of repeatedly uploading the full growing checkpoint.

## Cross-machine verification

MemWal has been verified with a cross-machine recovery flow:

```text
Machine A

↓

Store memory

↓

Destroy machine

↓

Machine B

↓

Restore memory
```

The restored agent state comes from Walrus and Sui, not from local files, local RAM, or a local database.

## Multi-thread isolation

MemWal has also been verified with independent thread recovery:

```text
Thread A -> Pizza

Thread B -> Football

Thread C -> Python
```

Each thread restores only its own memory. No cross-thread contamination was observed.

## How it works

1. Serialize the LangGraph checkpoint.
2. Upload the checkpoint bytes to Walrus.
3. Register the returned Walrus blob ID on Sui under the LangGraph `thread_id`.
4. Restore by looking up the `thread_id` on Sui, fetching the blob from Walrus, and reconstructing the checkpoint.

This gives each LangGraph thread a decentralized memory pointer:

```text
thread_id -> Sui registry -> Walrus blob -> LangGraph checkpoint
```

## 🎯 Demo Proof

MemWal has been validated end-to-end on live Walrus and Sui testnet infrastructure.

Verified scenarios:

- ✅ Agent persistence
- ✅ Cross-machine recovery
- ✅ Multi-thread isolation
- ✅ Snapshot checkpoints
- ✅ Delta checkpoints
- ✅ GitHub CI

No local database was used.

No local files were used.

No in-memory state was reused.

## Roadmap

* Delta compaction
* Monitoring dashboard

## License

MIT

---

Built for portable AI memory.

Kill the machine. Start another one. The agent continues.
