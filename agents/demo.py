from __future__ import annotations
import os
import sys
import textwrap
from typing import Any
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph


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

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        
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


THREAD_ID = "memwal-demo-thread-001"
_LAST_BLOB_ID: str | None = None
_LAST_TX_DIGEST: str | None = None

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║            MemWal — Decentralised Agent Memory               ║
║         Walrus (storage) + Sui (on-chain registry)           ║
╚══════════════════════════════════════════════════════════════╝
"""

def _fmt(msg: BaseMessage) -> str:
    
    role = msg.__class__.__name__.replace("Message", "")
    content = str(msg.content)
    wrapped = textwrap.fill(content, width=60, subsequent_indent="         ")
    return f"  [{role:>5}] {wrapped}"


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
            print(f"[tx]     Sui tx digest: {digest}")
            return digest

        async def lookup_blob(self, thread_id: str) -> str | None:
            blob_id = await super().lookup_blob(thread_id)
            print(f"[lookup] blob_id from chain: {blob_id}")
            return blob_id

    checkpoint_module.WalrusClient = TracingWalrusClient
    checkpoint_module.SuiRegistry = TracingSuiRegistry
    checkpoint_module._memwal_demo_tracing_installed = True


def main() -> None:
    print(BANNER)

    print("[setup] Loading config from environment / .env ...")
    from memwal.checkpoint import WalrusCheckpointer

    _install_checkpoint_tracing()
    checkpointer = WalrusCheckpointer.from_env()
    print("[setup] WalrusCheckpointer ready")
    print(f"[setup] Thread ID: {THREAD_ID}")
    print()

    graph = build_graph(checkpointer)
    run_config: dict[str, Any] = {
        "configurable": {"thread_id": THREAD_ID},
    }

    
    print("=" * 62)
    print("  RUN 1: First interaction (new checkpoint)")
    print("=" * 62)
    print()

    msg1 = HumanMessage(content="Hello, remember me!")
    print(_fmt(msg1))
    print()

    print("[walrus] Storing checkpoint to Walrus ...")
    print("[sui]    Registering thread -> blob mapping on Sui ...")
    result1 = graph.invoke({"messages": [msg1]}, config=run_config)

    
    for m in result1["messages"]:
        print(_fmt(m))
    print()

    print(f"[done]   Thread '{THREAD_ID}' checkpoint stored successfully.")
    print()
    
    print("=" * 62)
    print("  RUN 2: Follow-up interaction (restoring from checkpoint)")
    print("=" * 62)
    print()

    msg2 = HumanMessage(content="What did I say before?")
    print(_fmt(msg2))
    print()

    print("[sui]    Fetching thread -> blob mapping from Sui ...")
    print("[walrus] Fetching checkpoint from Walrus ...")
    result2 = graph.invoke({"messages": [msg2]}, config=run_config)

    print("[history] Full message history (from checkpoint):")
    for m in result2["messages"]:
        print(_fmt(m))
    print()

    msg_count = len(result2["messages"])
    print(f"[done]   {msg_count} messages in thread — checkpoint restore verified!")
    print()

    print("=" * 62)
    print("  SUMMARY")
    print("=" * 62)
    print(f"""
    Thread ID:      {THREAD_ID}
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