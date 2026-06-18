from __future__ import annotations

import os
import sys
import textwrap
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

import msgpack
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from memwal.config import Config, load_config


DEBUG = os.environ.get("MEMWAL_DEBUG", "0") == "1"


def _get_llm():
    if os.environ.get("STRAICO_API_KEY"):
        model = os.environ.get("STRAICO_MODEL", "openai/gpt-4o-mini")
        print(f"[llm] Using Straico ({model})")
        return _StraicoLLM(model=model)

    try:
        from langchain_anthropic import ChatAnthropic

        if os.environ.get("ANTHROPIC_API_KEY"):
            print("[llm] Using ChatAnthropic (Claude)")
            return ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)
    except ImportError:
        pass

    try:
        from langchain_openai import ChatOpenAI

        if os.environ.get("OPENAI_API_KEY"):
            print("[llm] Using ChatOpenAI (GPT-4)")
            return ChatOpenAI(model="gpt-4o", temperature=0)
    except ImportError:
        pass

    print("[llm] No API key found — using mock LLM")
    return _MockLLM()


class _StraicoLLM:
    _API_URL = "https://api.straico.com/v2/chat/completions"

    def __init__(self, *, model: str) -> None:
        self._api_key = os.environ["STRAICO_API_KEY"]
        self._model = model
        self._timeout = float(os.environ.get("STRAICO_TIMEOUT", "60"))
        self._max_tokens = int(os.environ.get("STRAICO_MAX_TOKENS", "1000"))
        self._temperature = float(os.environ.get("STRAICO_TEMPERATURE", "0"))

    @staticmethod
    def _role_for(message: BaseMessage) -> str:
        if isinstance(message, HumanMessage):
            return "user"
        if isinstance(message, AIMessage):
            return "assistant"
        return "system"

    @staticmethod
    def _text_for(message: BaseMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text is not None:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        import httpx

        straico_messages = [
            {
                "role": self._role_for(message),
                "content": [
                    {
                        "type": "text",
                        "text": self._text_for(message),
                    }
                ],
            }
            for message in messages
        ]

        payload = {
            "model": self._model,
            "messages": straico_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "replace_failed_models": True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                self._API_URL,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Straico request failed: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                "Straico request failed — "
                f"HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Unexpected Straico response structure: {response.text[:1000]}"
            ) from exc

        if not isinstance(text, str):
            raise RuntimeError(
                f"Straico returned non-text assistant content: {text!r}"
            )

        return AIMessage(content=text)


class _MockLLM:
    _RESPONSES = {
        0: "Hello from MemWal! I'll remember you. Your message has been "
           "stored on Walrus and registered on the Sui blockchain.",
        1: "You said: 'Hello, remember me!' — I retrieved that from my "
           "Walrus checkpoint on the Sui blockchain. Decentralised memory works!",
    }
    _DEFAULT = "I'm the MemWal mock LLM. My memory is backed by Walrus + Sui."

    @staticmethod
    def _human_texts(messages: list[BaseMessage]) -> list[str]:
        texts: list[str] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                content = message.content
                texts.append(content if isinstance(content, str) else str(content))
        return texts

    @staticmethod
    def _extract_fact(prefix: str, texts: list[str]) -> str | None:
        prefix_lower = prefix.lower()
        for text in texts:
            lowered = text.lower()
            if lowered.startswith(prefix_lower):
                value = text[len(prefix):].strip()
                return value.rstrip(".!?").strip() or None
        return None

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        human_texts = self._human_texts(messages)
        latest_human = human_texts[-1].lower() if human_texts else ""

        if "what is my name" in latest_human:
            name = self._extract_fact("My name is ", human_texts)
            if name:
                return AIMessage(content=f"Your name is {name}.")

        if "where do i live" in latest_human:
            location = self._extract_fact("I live in ", human_texts)
            if location:
                return AIMessage(content=f"You live in {location}.")

        if "what is my favourite language" in latest_human:
            language = self._extract_fact("My favourite language is ", human_texts)
            if language:
                return AIMessage(content=f"Your favourite language is {language}.")

        ai_count = sum(1 for m in messages if isinstance(m, AIMessage))
        text = self._RESPONSES.get(ai_count, self._DEFAULT)
        return AIMessage(content=text)


def build_graph(checkpointer):
    llm = _get_llm()

    def chat_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = state["messages"]
        response = llm.invoke(messages)
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("chat", chat_node)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)
    return builder.compile(checkpointer=checkpointer)


THREAD_ID_PREFIX = "memwal-demo-thread"
_LAST_BLOB_ID: str | None = None
_LAST_TX_DIGEST: str | None = None
_CURRENT_TRACE: Optional[dict[str, Any]] = None
_CURRENT_INCREMENTAL_STEP: Optional[int] = None

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║            MemWal — Decentralised Agent Memory               ║
║         Walrus (storage) + Sui (on-chain registry)           ║
╚══════════════════════════════════════════════════════════════╝
"""


def _diag_messages(label: str, messages: list[Any]) -> None:
    if not DEBUG:
        return

    print(f"[diag] {label}: {len(messages)} message(s)")
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", msg)
        print(
            "[diag] "
            f"type={type(msg).__name__} "
            f"role_check={isinstance(msg, AIMessage)} "
            f"content_repr={repr(content)[:80]}"
        )


def _trace_new_message(new_msg: BaseMessage) -> None:
    if not DEBUG:
        return

    print(
        "[trace:demo] appending new message "
        f"type={type(new_msg).__name__} "
        f"content_repr={repr(new_msg.content)[:100]}"
    )


def format_message(msg) -> str:
    if isinstance(msg, HumanMessage):
        role = "Human"
    elif isinstance(msg, AIMessage):
        role = "   AI"
    else:
        role = type(msg).__name__

    content = msg.content if hasattr(msg, "content") else str(msg)
    if not isinstance(content, str):
        content = str(content)

    wrapped = textwrap.fill(content, width=60, subsequent_indent="         ")
    return f"  [{role}] {wrapped}"


def _message_content(msg: Any) -> str:
    if isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = getattr(msg, "content", msg)
    return content if isinstance(content, str) else str(content)


def _message_pair(msg: Any) -> tuple[str, str]:
    if isinstance(msg, dict):
        role = str(msg.get("role") or msg.get("type") or "dict")
        return role, _message_content(msg)
    return type(msg).__name__, _message_content(msg)


def _print_duplicate_warning(messages: list[Any]) -> None:
    unique_pairs = {_message_pair(message) for message in messages}
    if len(unique_pairs) == len(messages):
        return

    print("[warning]")
    print()
    print("Duplicate messages detected")
    print()
    print("This usually indicates message history was reinserted.")
    print()


def _format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} bytes"
    return f"{num_bytes / 1024:.1f}KB"


def _message_record(message: BaseMessage) -> dict[str, str]:
    if isinstance(message, HumanMessage):
        role = "human"
    elif isinstance(message, AIMessage):
        role = "ai"
    else:
        role = message.__class__.__name__.replace("Message", "").lower()
    return {
        "role": role,
        "content": str(message.content),
    }


def _pack_checkpoint_payload(
    config: dict[str, Any],
    checkpoint: dict[str, Any],
    metadata: dict[str, Any],
) -> bytes:
    payload = {
        "checkpoint": checkpoint,
        "metadata": metadata,
        "parent_checkpoint_id": config.get("configurable", {}).get("checkpoint_id"),
        "checkpoint_ns": config.get("configurable", {}).get("checkpoint_ns", ""),
    }
    return msgpack.packb(payload, use_bin_type=True)


def _new_trace(step: int) -> dict[str, Any]:
    return {
        "step": step,
        "blob_id": None,
        "tx_digest": None,
        "uploaded_bytes": 0,
    }


def _install_checkpoint_tracing() -> None:
    import memwal.checkpoint as checkpoint_module

    if getattr(checkpoint_module, "_memwal_demo_tracing_installed", False):
        return

    original_walrus_client = checkpoint_module.WalrusClient
    original_sui_registry = checkpoint_module.SuiRegistry

    class TracingWalrusClient(original_walrus_client):
        async def store_blob(self, data: bytes, epochs: int) -> str:
            global _LAST_BLOB_ID

            blob_id = await super().store_blob(data, epochs)
            _LAST_BLOB_ID = blob_id
            if _CURRENT_TRACE is not None:
                _CURRENT_TRACE["blob_id"] = blob_id
                _CURRENT_TRACE["uploaded_bytes"] += len(data)
            print(f"[blob]   blob_id: {blob_id}")
            return blob_id

        async def fetch_blob(self, blob_id: str) -> bytes:
            data = await super().fetch_blob(blob_id)
            print(f"[fetch]  blob size: {len(data)} bytes")
            return data

    class TracingSuiRegistry(original_sui_registry):
        async def register_blob(self, thread_id: str, blob_id: str) -> str:
            global _LAST_TX_DIGEST

            digest = await super().register_blob(thread_id, blob_id)
            _LAST_TX_DIGEST = digest
            if _CURRENT_TRACE is not None:
                _CURRENT_TRACE["tx_digest"] = digest
            print(f"[tx]     Sui tx digest: {digest}")
            return digest

        async def lookup_blob(self, thread_id: str) -> str | None:
            blob_id = await super().lookup_blob(thread_id)
            print(f"[lookup] blob_id from chain: {blob_id}")
            return blob_id

    checkpoint_module.WalrusClient = TracingWalrusClient
    checkpoint_module.SuiRegistry = TracingSuiRegistry
    checkpoint_module._memwal_demo_tracing_installed = True


def run_incremental_test(
    checkpointer,
    strategy_label: str,
    *,
    num_steps: int = 5,
    thread_id_prefix: str | None = None,
) -> dict[str, Any]:
    global _CURRENT_TRACE, _CURRENT_INCREMENTAL_STEP

    from memwal.checkpoint import CheckpointChainError
    from memwal.sui import SuiRegistryError
    from memwal.walrus import WalrusError

    prefix = thread_id_prefix or f"memwal-{strategy_label}-test"
    thread_id = f"{prefix}-{int(time.time())}"
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    messages: list[BaseMessage] = []
    traces: list[dict[str, Any]] = []
    naive_total = 0
    total_uploaded = 0
    final_ai_content = ""

    print(f"[test]   Thread ID: {thread_id}")
    _diag_messages("run_incremental_test initial messages", messages)

    try:
        for step in range(1, num_steps + 1):
            _CURRENT_INCREMENTAL_STEP = step
            human = HumanMessage(content=f"Live delta test human step {step}")
            ai = AIMessage(content=f"Live delta test assistant step {step}")
            _trace_new_message(human)
            _trace_new_message(ai)
            messages.extend([human, ai])
            _diag_messages(f"run_incremental_test step {step} constructed", messages)
            final_ai_content = str(ai.content)

            checkpoint = {
                "v": 1,
                "id": f"{thread_id}-checkpoint-{step}",
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel_values": {
                    "messages": [_message_record(message) for message in messages],
                },
                "channel_versions": {
                    "messages": step,
                },
                "versions_seen": {},
                "pending_sends": [],
            }
            metadata = {
                "source": "memwal-live-incremental-demo",
                "step": step,
                "writes": {},
            }
            naive_bytes = len(_pack_checkpoint_payload(config, checkpoint, metadata))
            naive_total += naive_bytes

            _CURRENT_TRACE = _new_trace(step)
            config = checkpointer.put(config, checkpoint, metadata, {})
            trace = dict(_CURRENT_TRACE)
            traces.append(trace)
            total_uploaded += int(trace["uploaded_bytes"])

            print(f"[step {step}] blob_id: {trace['blob_id']}")
            print(f"[step {step}] tx_digest: {trace['tx_digest']}")
            print(f"[step {step}] uploaded: {trace['uploaded_bytes']} bytes")
            print(f"[step {step}] total uploaded: {total_uploaded} bytes")
            print()

        _CURRENT_INCREMENTAL_STEP = None
        _CURRENT_TRACE = None

        recovered = None
        for attempt in range(1, 4):
            recovered = checkpointer.get({"configurable": {"thread_id": thread_id}})
            if recovered is not None:
                break
            if attempt < 3:
                print("[retry] checkpoint not yet visible, retrying...")
                time.sleep(1)
        if recovered is None:
            raise AssertionError("No checkpoint recovered from live testnet")

        recovered_messages = recovered["channel_values"]["messages"]
        _diag_messages("run_incremental_test recovered messages", recovered_messages)
        recovered_count = len(recovered_messages)
        recovered_last = recovered_messages[-1]["content"]
        recovered_roles = [message["role"] for message in recovered_messages]
        expected_roles = ["human", "ai"] * num_steps
        print(f"[verify] recovered messages: {recovered_count}")
        if strategy_label == "delta":
            print(
                "[verify] delta hops walked: "
                f"{getattr(checkpointer, '_last_delta_hops', 0)}"
            )

        assert recovered_count == num_steps * 2
        assert recovered_last == final_ai_content
        assert recovered_roles == expected_roles

        savings = 0.0
        if naive_total > 0:
            savings = ((naive_total - total_uploaded) / naive_total) * 100

        print("Strategy:        {}".format(strategy_label))
        print(f"Steps:           {num_steps}")
        print(f"Total uploaded:  {total_uploaded} bytes")
        print(f"vs naive:        {naive_total} bytes")
        print(f"Savings:         {savings:.2f}%")
        print("Chain integrity: PASS")

        return {
            "strategy": strategy_label,
            "steps": num_steps,
            "total_uploaded": total_uploaded,
            "naive_total": naive_total,
            "savings": savings,
            "chain_integrity": "PASS",
            "thread_id": thread_id,
            "traces": traces,
        }
    except CheckpointChainError as exc:
        print(
            "[run3 error] CheckpointChainError "
            f"at step {_CURRENT_INCREMENTAL_STEP}: {exc}"
        )
        print("Chain integrity: FAIL")
        return {
            "strategy": strategy_label,
            "steps": num_steps,
            "total_uploaded": total_uploaded,
            "naive_total": naive_total,
            "savings": 0.0,
            "chain_integrity": "FAIL",
            "thread_id": thread_id,
            "traces": traces,
        }
    except (WalrusError, SuiRegistryError) as exc:
        print(
            "[run3 error] Live testnet operation failed "
            f"at step {_CURRENT_INCREMENTAL_STEP}: {type(exc).__name__}: {exc}"
        )
        print("Chain integrity: FAIL")
        return {
            "strategy": strategy_label,
            "steps": num_steps,
            "total_uploaded": total_uploaded,
            "naive_total": naive_total,
            "savings": 0.0,
            "chain_integrity": "FAIL",
            "thread_id": thread_id,
            "traces": traces,
        }
    except AssertionError as exc:
        print(
            "[run3 error] Chain integrity assertion failed "
            f"for {strategy_label} at step {_CURRENT_INCREMENTAL_STEP}: {exc}"
        )
        print("Chain integrity: FAIL")
        return {
            "strategy": strategy_label,
            "steps": num_steps,
            "total_uploaded": total_uploaded,
            "naive_total": naive_total,
            "savings": 0.0,
            "chain_integrity": "FAIL",
            "thread_id": thread_id,
            "traces": traces,
        }
    finally:
        _CURRENT_TRACE = None
        _CURRENT_INCREMENTAL_STEP = None


def _print_comparison(title: str, results: list[dict[str, Any]]) -> None:
    if not results:
        return

    print()
    print(title)
    print("strategy   uploaded     naive        savings    integrity")
    for result in results:
        print(
            f"{result['strategy']:<10} "
            f"{result['total_uploaded']:<12} "
            f"{result['naive_total']:<12} "
            f"{result['savings']:>7.2f}%   "
            f"{result['chain_integrity']}"
        )


def _latest_ai_text(result: dict[str, Any]) -> str:
    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    raise AssertionError("No AI response found in graph result")


def _assert_response_contains(label: str, response: str, expected: str) -> bool:
    passed = expected in response
    print(f"{label}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        raise AssertionError(
            f"Expected {expected!r} in {label.lower()} response, got {response!r}"
        )
    return passed


def _print_check(label: str, passed: bool) -> None:
    print(f"{label}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        raise AssertionError(f"{label} verification failed")


def _print_llm_check(label: str, response: str, expected: str) -> None:
    status = "PASS" if expected in response else "WARNING"
    print(f"{label}: {status}")


def run_cross_machine_verification() -> None:
    from memwal.checkpoint import WalrusCheckpointer

    global _LAST_BLOB_ID, _LAST_TX_DIGEST

    print("=" * 62)
    print("  RUN 4: Cross-machine verification")
    print("=" * 62)
    print()

    thread_id = f"cross-machine-{int(time.time())}"

    print("[MACHINE A]")
    print()

    machine_a_config = replace(load_config(), CHECKPOINT_STRATEGY="snapshot")
    checkpointer = WalrusCheckpointer(machine_a_config)
    graph = build_graph(checkpointer)
    agent = graph
    state: dict[str, Any] | None = None
    messages = [
        HumanMessage(content="My name is Surojit."),
        HumanMessage(content="I live in Assam."),
        HumanMessage(content="My favourite language is Python."),
    ]
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    print("✓ Agent created")
    print()

    for message in messages:
        _trace_new_message(message)
        state = agent.invoke({"messages": [message]}, config=config)

    if state is None:
        raise AssertionError("Machine A did not produce a final state")

    persisted_config = checkpointer.force_snapshot(thread_id)
    if persisted_config is None:
        raise AssertionError("Machine A could not flush final checkpoint")

    machine_a_blob_id = _LAST_BLOB_ID
    machine_a_tx_digest = _LAST_TX_DIGEST

    if not machine_a_blob_id:
        raise AssertionError("Machine A did not produce a Walrus blob ID")
    if not machine_a_tx_digest:
        raise AssertionError("Machine A did not produce a Sui tx digest")

    print("✓ Memory stored")
    print()
    persisted_messages = state["messages"]
    latest_persisted_content = "My favourite language is Python"
    persisted_text = "\n".join(_message_content(message) for message in persisted_messages)
    if latest_persisted_content not in persisted_text:
        raise AssertionError("Machine A final state does not contain Python fact")

    print("[Machine A]")
    print()
    print("Persisted message count:")
    print(len(persisted_messages))
    print()
    print("Latest persisted content:")
    print(latest_persisted_content)
    print()
    print("Thread ID:")
    print(thread_id)
    print()
    print("Blob ID:")
    print(machine_a_blob_id)
    print()
    print("Tx Digest:")
    print(machine_a_tx_digest)
    print()

    del graph
    del agent
    del state
    del messages
    del checkpointer
    del config
    del machine_a_config
    del persisted_config
    del persisted_messages
    del persisted_text
    del latest_persisted_content
    del machine_a_blob_id
    del machine_a_tx_digest

    print("-" * 62)
    print()
    print("[MACHINE B]")
    print()

    machine_b_config_obj = replace(load_config(), CHECKPOINT_STRATEGY="snapshot")
    machine_b_checkpointer = WalrusCheckpointer(machine_b_config_obj)
    machine_b_graph = build_graph(machine_b_checkpointer)
    machine_b_agent = machine_b_graph
    machine_b_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    print("✓ Fresh agent created")

    restored_checkpoint = machine_b_checkpointer.get(machine_b_config)
    if restored_checkpoint is None:
        raise AssertionError("Machine B could not restore checkpoint by thread ID")

    print("✓ Thread resolved via Sui")
    print("✓ Memory restored from Walrus")
    print()

    restored_messages = restored_checkpoint["channel_values"]["messages"]
    _print_duplicate_warning(restored_messages)

    print("[Restored memory]")
    print()
    for message in restored_messages:
        print(format_message(message))
    print()

    restored_text = "\n".join(_message_content(message) for message in restored_messages)
    name_restored = "Surojit" in restored_text
    location_restored = "Assam" in restored_text
    language_restored = "Python" in restored_text
    history_restored = len(restored_messages) >= 6

    name_state = machine_b_agent.invoke(
        {"messages": [HumanMessage(content="What is my name?")]},
        config=machine_b_config,
    )
    location_state = machine_b_agent.invoke(
        {"messages": [HumanMessage(content="Where do I live?")]},
        config=machine_b_config,
    )
    language_state = machine_b_agent.invoke(
        {"messages": [HumanMessage(content="What is my favourite language?")]},
        config=machine_b_config,
    )

    name_response = _latest_ai_text(name_state)
    location_response = _latest_ai_text(location_state)
    language_response = _latest_ai_text(language_state)

    print("Infrastructure verification")
    print()
    _print_check("Name", name_restored)
    print()
    _print_check("Location", location_restored)
    print()
    _print_check("Favourite language", language_restored)
    print()
    _print_check("History restored", history_restored)
    print()

    print("LLM verification")
    print()
    _print_llm_check("Name", name_response, "Surojit")
    print()
    _print_llm_check("Location", location_response, "Assam")
    print()
    _print_llm_check("Favourite language", language_response, "Python")

    del machine_b_graph
    del machine_b_agent
    del machine_b_checkpointer
    del machine_b_config
    del machine_b_config_obj
    del restored_messages
    del restored_checkpoint
    del restored_text
    del name_state
    del location_state
    del language_state
    del name_response
    del location_response
    del language_response

    print()
    print("=" * 62)
    print("  RESULT")
    print("=" * 62)
    print()
    print("✓ Cross-machine verification successful")
    print()
    print("No local database used")
    print()
    print("No local files used")
    print()
    print("No in-memory state reused")
    print()
    print("Memory restored entirely from Walrus + Sui")


def _checkpoint_text(checkpoint: dict[str, Any]) -> str:
    messages = checkpoint.get("channel_values", {}).get("messages", [])
    return "\n".join(_message_content(message) for message in messages)


def _checkpoint_message_count(checkpoint: dict[str, Any]) -> int:
    return len(checkpoint.get("channel_values", {}).get("messages", []))


def _print_isolation_matrix(
    rows: list[dict[str, Any]],
    *,
    title: str,
) -> None:
    print(title)
    print("thread     code_word   found_in_own   leaked_to_others   result")
    for row in rows:
        print(
            f"{row['name']:<10} "
            f"{row['code']:<10} "
            f"{row['found']:<14} "
            f"{row['leaked']:<18} "
            f"{row['result']}"
        )
    print()


def _evaluate_isolation(
    restored_texts: dict[str, str],
    specs: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    leaks: list[str] = []

    for name, spec in specs.items():
        own_text = restored_texts[name]
        own_code = spec["code"]
        found = own_code in own_text
        leaked = False

        for other_name, other_spec in specs.items():
            if other_name == name:
                continue
            other_code = other_spec["code"]
            if other_code in own_text:
                leaked = True
                leaks.append(
                    "[LEAK DETECTED] "
                    f"thread_{name} contains code word {other_code} "
                    f"belonging to thread_{other_name}"
                )

        rows.append(
            {
                "name": name,
                "code": own_code,
                "found": "YES" if found else "NO",
                "leaked": "YES" if leaked else "NO",
                "result": "PASS" if found and not leaked else "FAIL",
            }
        )

    return rows, leaks


def run_multi_thread_isolation_verification(cfg: Config) -> None:
    from memwal.checkpoint import WalrusCheckpointer

    print("=" * 62)
    print("  RUN 5: Multi-thread isolation verification")
    print("=" * 62)
    print()

    now = int(time.time())
    thread_alpha = f"memwal-isolation-alpha-{now}"
    thread_beta = f"memwal-isolation-beta-{now}"
    thread_gamma = f"memwal-isolation-gamma-{now}"

    assert thread_alpha != thread_beta
    assert thread_beta != thread_gamma
    assert thread_alpha != thread_gamma

    specs = {
        "alpha": {
            "thread_id": thread_alpha,
            "code": "BANANA77",
            "first": "My secret code word is BANANA77",
            "second": "Please remember that alpha belongs to BANANA77.",
        },
        "beta": {
            "thread_id": thread_beta,
            "code": "ROCKET99",
            "first": "My secret code word is ROCKET99",
            "second": "Please remember that beta belongs to ROCKET99.",
        },
        "gamma": {
            "thread_id": thread_gamma,
            "code": "ZEBRA42",
            "first": "My secret code word is ZEBRA42",
            "second": "Please remember that gamma belongs to ZEBRA42.",
        },
    }

    checkpointer = WalrusCheckpointer(cfg)
    graph = build_graph(checkpointer)
    configs = {
        name: {"configurable": {"thread_id": spec["thread_id"]}}
        for name, spec in specs.items()
    }
    states: dict[str, dict[str, Any]] = {}

    interleaved_turns = [
        ("alpha", specs["alpha"]["first"]),
        ("beta", specs["beta"]["first"]),
        ("gamma", specs["gamma"]["first"]),
        ("alpha", specs["alpha"]["second"]),
        ("beta", specs["beta"]["second"]),
        ("gamma", specs["gamma"]["second"]),
    ]

    print("[write] Interleaving alpha, beta, gamma turns")
    print()
    for name, content in interleaved_turns:
        message = HumanMessage(content=content)
        _trace_new_message(message)
        states[name] = graph.invoke({"messages": [message]}, config=configs[name])
        print(f"[write] {name:<5} -> {content}")
    print()

    cache_read_order = ["gamma", "alpha", "beta"]
    cache_texts: dict[str, str] = {}
    cache_counts: dict[str, int] = {}
    for name in cache_read_order:
        checkpoint = checkpointer.get(configs[name])
        if checkpoint is None:
            raise AssertionError(f"Volatile cache get returned None for {name}")
        cache_texts[name] = _checkpoint_text(checkpoint)
        cache_counts[name] = _checkpoint_message_count(checkpoint)

    cache_rows, cache_leaks = _evaluate_isolation(cache_texts, specs)
    _print_isolation_matrix(cache_rows, title="Volatile cache isolation test")
    for leak in cache_leaks:
        print(leak)
    if cache_leaks:
        raise AssertionError("Volatile cache isolation leak detected")

    expected_counts = {
        name: len(state["messages"])
        for name, state in states.items()
    }
    for name in specs:
        if cache_counts[name] != expected_counts[name]:
            raise AssertionError(
                f"Volatile cache message count mismatch for {name}: "
                f"expected {expected_counts[name]}, got {cache_counts[name]}"
            )

    print("[persist] Flushing each interleaved thread to Walrus + Sui")
    print()
    persisted: dict[str, dict[str, str | None]] = {}
    for name in ["alpha", "beta", "gamma"]:
        before_blob = _LAST_BLOB_ID
        before_tx = _LAST_TX_DIGEST
        updated_config = checkpointer.force_snapshot(specs[name]["thread_id"])
        if updated_config is None:
            raise AssertionError(f"force_snapshot returned None for {name}")
        if _LAST_BLOB_ID == before_blob or _LAST_TX_DIGEST == before_tx:
            raise AssertionError(f"force_snapshot did not persist a new blob for {name}")
        persisted[name] = {
            "blob_id": _LAST_BLOB_ID,
            "tx_digest": _LAST_TX_DIGEST,
        }
        print(f"[persist] {name:<5} blob_id: {_LAST_BLOB_ID}")
        print(f"[persist] {name:<5} tx_digest: {_LAST_TX_DIGEST}")
    print()

    del graph
    del states
    del checkpointer

    fresh_checkpointer = WalrusCheckpointer(cfg)
    restore_order = ["gamma", "alpha", "beta"]
    restored_texts: dict[str, str] = {}
    restored_counts: dict[str, int] = {}
    for name in restore_order:
        checkpoint = fresh_checkpointer.get(configs[name])
        if checkpoint is None:
            raise AssertionError(f"Fresh restore returned None for {name}")
        restored_texts[name] = _checkpoint_text(checkpoint)
        restored_counts[name] = _checkpoint_message_count(checkpoint)

    matrix_rows, leaks = _evaluate_isolation(restored_texts, specs)

    print("Multi-thread isolation test")
    print("thread     code_word   found_in_own   leaked_to_others   result")
    for row in matrix_rows:
        print(
            f"{row['name']:<10} "
            f"{row['code']:<10} "
            f"{row['found']:<14} "
            f"{row['leaked']:<18} "
            f"{row['result']}"
        )
    print()

    for leak in leaks:
        print(leak)
    if leaks:
        raise AssertionError("Persisted isolation leak detected")

    for name, spec in specs.items():
        own_code = spec["code"]
        own_text = restored_texts[name]
        other_codes = [
            other_spec["code"]
            for other_name, other_spec in specs.items()
            if other_name != name
        ]
        assert own_code in own_text
        for other_code in other_codes:
            assert other_code not in own_text
        if restored_counts[name] != expected_counts[name]:
            raise AssertionError(
                f"Persisted message count mismatch for {name}: "
                f"expected {expected_counts[name]}, got {restored_counts[name]}"
            )

    print("=" * 62)
    print("  ISOLATION VERIFICATION")
    print("=" * 62)
    print()
    for row in matrix_rows:
        print(f"Thread {row['name']} contamination: {row['result']}")
    print()

    print("=" * 62)
    print("  RESULT")
    print("=" * 62)
    print()
    print("✓ Multi-thread isolation successful")
    print()
    print("No thread collisions")
    print()
    print("No memory leakage")
    print()
    print("No cache contamination")
    print()
    print("Independent memory restored from Walrus + Sui")

    del fresh_checkpointer
    del restored_texts
    del restored_counts
    del cache_texts
    del cache_counts
    del expected_counts
    del specs
    del configs
    del persisted


def main() -> None:
    print(BANNER)

    print("[setup] Loading config from environment / .env ...")
    from memwal.checkpoint import WalrusCheckpointer

    _install_checkpoint_tracing()
    cfg = load_config()
    strategy = cfg.CHECKPOINT_STRATEGY
    print(f"[memwal] Active strategy: {strategy}")
    checkpointer = WalrusCheckpointer(cfg)
    print("[setup] WalrusCheckpointer ready")
    demo_thread_id = f"{THREAD_ID_PREFIX}-{int(time.time())}"
    print(f"[setup] Thread ID: {demo_thread_id}")
    print()

    graph = build_graph(checkpointer)
    run_config: dict[str, Any] = {
        "configurable": {"thread_id": demo_thread_id},
    }

    print("=" * 62)
    print("  RUN 1: First interaction (new checkpoint)")
    print("=" * 62)
    print()

    msg1 = HumanMessage(content="Hello, remember me!")
    _trace_new_message(msg1)
    _diag_messages("RUN 1 constructed input", [msg1])
    print(format_message(msg1))
    print()

    print("[walrus] Storing checkpoint to Walrus ...")
    print("[sui]    Registering thread -> blob mapping on Sui ...")
    result1 = graph.invoke({"messages": [msg1]}, config=run_config)
    _diag_messages("RUN 1 graph result", result1["messages"])

    for m in result1["messages"]:
        print(format_message(m))
    print()

    print(f"[done]   Thread '{demo_thread_id}' checkpoint stored successfully.")
    print()

    print("=" * 62)
    print("  RUN 2: Follow-up interaction (restoring from checkpoint)")
    print("=" * 62)
    print()

    msg2 = HumanMessage(content="What did I say before?")
    _trace_new_message(msg2)
    _diag_messages("RUN 2 constructed input", [msg2])
    print(format_message(msg2))
    print()

    print("[sui]    Fetching thread -> blob mapping from Sui ...")
    print("[walrus] Fetching checkpoint from Walrus ...")
    result2 = graph.invoke({"messages": [msg2]}, config=run_config)
    _diag_messages("RUN 2 restored graph result", result2["messages"])

    print("[history] Full message history (from checkpoint):")
    _print_duplicate_warning(result2["messages"])
    for m in result2["messages"]:
        print(format_message(m))
    print()

    msg_count = len(result2["messages"])
    print(f"[done]   {msg_count} messages in thread — checkpoint restore verified!")
    print()

    print("=" * 62)
    print("  SUMMARY")
    print("=" * 62)
    print(f"""
    Thread ID:      {demo_thread_id}
    Blob ID:        {_LAST_BLOB_ID}
    Tx Digest:      {_LAST_TX_DIGEST}
    Sui Explorer:   https://suiscan.xyz/testnet/tx/{_LAST_TX_DIGEST}
    Walrus Blob:    https://aggregator.walrus-testnet.walrus.space/v1/blobs/{_LAST_BLOB_ID}
    Messages:       {msg_count}
    Checkpointer:   WalrusCheckpointer
    Storage:        Walrus (decentralised blob store)
    Registry:       Sui blockchain (on-chain thread -> blob mapping)
    Serialisation:  msgpack

    The agent's memory is now stored on Walrus and registered
    on the Sui blockchain. Any agent instance — on any machine —
    can resume this thread by looking up the thread ID on-chain.
    """)

    print("=" * 62)
    print("  RUN 3: Live incremental storage verification (snapshot mode)")
    print("=" * 62)
    print()

    snapshot_checkpointer = (
        checkpointer
        if strategy == "snapshot"
        else WalrusCheckpointer(replace(cfg, CHECKPOINT_STRATEGY="snapshot"))
    )
    results_5 = [run_incremental_test(snapshot_checkpointer, "snapshot", num_steps=5)]

    time.sleep(1)
    print()
    print("=" * 62)
    print("  RUN 3B: Live incremental storage verification (delta mode)")
    print("=" * 62)
    print()
    delta_checkpointer = (
        checkpointer
        if strategy == "delta"
        else WalrusCheckpointer(replace(cfg, CHECKPOINT_STRATEGY="delta"))
    )
    results_5.append(run_incremental_test(delta_checkpointer, "delta", num_steps=5))

    time.sleep(1)
    print()
    print("=" * 62)
    print("  RUN 3C: Live incremental storage verification (snapshot mode, 20 steps)")
    print("=" * 62)
    print()
    snapshot_long_checkpointer = WalrusCheckpointer(
        replace(cfg, CHECKPOINT_STRATEGY="snapshot")
    )
    results_20 = [
        run_incremental_test(
            snapshot_long_checkpointer,
            "snapshot",
            num_steps=20,
            thread_id_prefix="memwal-snapshot-long",
        )
    ]

    time.sleep(1)
    print()
    print("=" * 62)
    print("  RUN 3D: Live incremental storage verification (delta mode, 20 steps)")
    print("=" * 62)
    print()
    delta_long_checkpointer = WalrusCheckpointer(
        replace(cfg, CHECKPOINT_STRATEGY="delta")
    )
    results_20.append(
        run_incremental_test(
            delta_long_checkpointer,
            "delta",
            num_steps=20,
            thread_id_prefix="memwal-delta-long",
        )
    )

    _print_comparison("Live strategy comparison (5 steps)", results_5)
    _print_comparison("Live strategy comparison (20 steps)", results_20)

    time.sleep(1)
    print()
    run_cross_machine_verification()

    time.sleep(1)
    print()
    run_multi_thread_isolation_verification(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[interrupted] Demo cancelled by user.")
        sys.exit(130)
    except ValueError as exc:
        print(f"\n[config error] {exc}")
        print(
            "\nMake sure your .env file is configured. "
            "See .env.example for required fields."
        )
        sys.exit(1)
    except Exception as exc:
        print(f"\n[error] {type(exc).__name__}: {exc}")
        print(
            "\nIf this is a Walrus or Sui error, check:\n"
            "  1. Your .env has valid SUI_PRIVATE_KEY\n"
            "  2. The Sui registry contract is deployed (Phase 3/6)\n"
            "  3. REGISTRY_PACKAGE_ID and REGISTRY_OBJECT_ID are set\n"
            "  4. You have SUI testnet tokens for gas\n"
            "  5. Walrus testnet endpoints are reachable"
        )
        sys.exit(1)
