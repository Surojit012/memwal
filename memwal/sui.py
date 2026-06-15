"""
memwal.sui — Sui transaction layer for the MemWal checkpoint registry.

Uses direct Sui JSON-RPC calls via httpx for reliability across Sui SDK
versions, and PyNaCl for Ed25519 transaction signing.  This avoids the
fragile pysui async surface while remaining fully compatible with the
Sui testnet/mainnet JSON-RPC API.

Design note:  pysui's Python API has undergone multiple breaking changes
across minor versions.  For a production checkpoint backend that must not
silently break, raw RPC + deterministic Ed25519 signing is the safer path.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Optional

import httpx

from memwal.config import Config


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class SuiRegistryError(Exception):
    """Raised when a Sui registry operation fails.

    Attributes
    ----------
    thread_id : str | None
        The thread_id involved in the failed operation.
    rpc_error : Any
        The raw JSON-RPC error payload, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        thread_id: Optional[str] = None,
        rpc_error: Any = None,
    ) -> None:
        self.thread_id = thread_id
        self.rpc_error = rpc_error
        super().__init__(message)


# ---------------------------------------------------------------------------
# Key helpers (Ed25519 via PyNaCl)
# ---------------------------------------------------------------------------

def _load_keypair(raw_key: str):
    """Parse an Ed25519 private key from base64 or hex encoding.

    Supported formats
    -----------------
    * **Sui CLI base64** — flag byte (``0x00``) + 32-byte seed = 33 bytes
    * **Raw base64**     — 32-byte seed
    * **Full keypair**   — 64 bytes (seed ∥ pubkey)
    * **Hex**            — ``0x``-prefixed or plain, 32 or 33 bytes

    Returns
    -------
    tuple[nacl.signing.SigningKey, nacl.signing.VerifyKey]
    """
    from nacl.signing import SigningKey

    seed: Optional[bytes] = None

    # ---- Try base64 first ------------------------------------------------ #
    try:
        decoded = base64.b64decode(raw_key, validate=True)
        if len(decoded) == 33 and decoded[0] == 0x00:
            # Sui CLI export: flag(1) + ed25519_seed(32)
            seed = decoded[1:]
        elif len(decoded) == 32:
            seed = decoded
        elif len(decoded) == 64:
            # Full keypair: seed(32) + pubkey(32)
            seed = decoded[:32]
    except Exception:
        pass

    # ---- Fall back to hex ------------------------------------------------ #
    if seed is None:
        try:
            hex_str = raw_key.removeprefix("0x").removeprefix("0X")
            decoded = bytes.fromhex(hex_str)
            if len(decoded) == 32:
                seed = decoded
            elif len(decoded) == 33 and decoded[0] == 0x00:
                seed = decoded[1:]
        except (ValueError, TypeError):
            pass

    if seed is None:
        raise ValueError(
            "SUI_PRIVATE_KEY must be a base64- or hex-encoded Ed25519 key "
            "(32-byte seed, or 33-byte Sui CLI export with 0x00 flag prefix). "
            f"Got value of length {len(raw_key)} chars that could not be parsed."
        )

    sk = SigningKey(seed)
    return sk, sk.verify_key


def _derive_address(verify_key) -> str:
    """Derive the Sui address from an Ed25519 public key.

    ``address = hex(blake2b-256(0x00 ∥ pubkey_bytes))``
    """
    h = hashlib.blake2b(digest_size=32)
    h.update(bytes([0x00]))          # Ed25519 scheme flag
    h.update(bytes(verify_key))      # 32-byte public key
    return "0x" + h.hexdigest()


# ---------------------------------------------------------------------------
# SuiRegistry
# ---------------------------------------------------------------------------

class SuiRegistry:
    """Manages the on-chain thread→blob checkpoint registry via Sui Move calls.

    Parameters
    ----------
    config : Config
        A populated :class:`memwal.config.Config` instance.

    Usage
    -----
    >>> cfg = load_config()
    >>> async with SuiRegistry(cfg) as reg:
    ...     digest = await reg.register_blob("thread-1", "blob_abc")
    ...     bid    = await reg.lookup_blob("thread-1")
    """

    # Sui intent prefix for TransactionData signing.
    _INTENT_PREFIX: bytes = bytes([0, 0, 0])

    # Ed25519 scheme flag used in signature serialisation.
    _ED25519_FLAG: int = 0x00

    # Default gas budget (10 SUI in MIST).
    _GAS_BUDGET: str = "10000000"

    def __init__(self, config: Config) -> None:
        self._rpc_url: str = config.SUI_RPC_URL
        self._package_id: str = config.REGISTRY_PACKAGE_ID
        self._registry_id: str = config.REGISTRY_OBJECT_ID

        self._signing_key, self._verify_key = _load_keypair(
            config.SUI_PRIVATE_KEY,
        )
        self._address: str = _derive_address(self._verify_key)

        self._client: Optional[httpx.AsyncClient] = None

    # -- Context-manager protocol ------------------------------------------ #

    async def __aenter__(self) -> "SuiRegistry":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- Internal helpers -------------------------------------------------- #

    def _get_client(self) -> httpx.AsyncClient:
        """Return the active ``httpx.AsyncClient``, creating one lazily."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    @property
    def address(self) -> str:
        """The Sui address derived from the configured private key."""
        return self._address

    async def _rpc(self, method: str, params: list) -> dict:
        """Execute a single Sui JSON-RPC call.

        Returns the full JSON response body (always contains ``"jsonrpc"``).
        The caller is responsible for inspecting ``"error"`` / ``"result"``.
        """
        client = self._get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        resp = await client.post(self._rpc_url, json=payload)
        if resp.status_code != 200:
            raise SuiRegistryError(
                f"Sui RPC HTTP {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    def _sign_tx_bytes(self, tx_bytes_b64: str) -> str:
        """Sign base64-encoded ``TransactionData`` and return a base64 Sui signature.

        Signature wire format (97 bytes):
            ``flag(1) ∥ ed25519_sig(64) ∥ pubkey(32)``
        """
        tx_bytes = base64.b64decode(tx_bytes_b64)

        # Intent message = prefix ∥ raw tx bytes
        intent_msg = self._INTENT_PREFIX + tx_bytes

        # blake2b-256 digest of the intent message
        digest = hashlib.blake2b(intent_msg, digest_size=32).digest()

        # Ed25519 sign the 32-byte digest
        signed = self._signing_key.sign(digest)
        sig_bytes = signed.signature  # 64 bytes

        # Assemble: flag ∥ sig ∥ pubkey
        serialised = (
            bytes([self._ED25519_FLAG])
            + sig_bytes
            + bytes(self._verify_key)
        )
        return base64.b64encode(serialised).decode("ascii")

    # -- Public API -------------------------------------------------------- #

    async def register_blob(self, thread_id: str, blob_id: str) -> str:
        """Register (or update) a ``thread_id → blob_id`` mapping on-chain.

        Calls ``{PACKAGE}::registry::register(registry, thread_id, blob_id)``.

        Parameters
        ----------
        thread_id : str
            LangGraph thread identifier.
        blob_id : str
            Walrus blob identifier returned by :meth:`WalrusClient.store_blob`.

        Returns
        -------
        str
            The Sui transaction digest on success.

        Raises
        ------
        SuiRegistryError
            On transaction build, signing, or execution failure.
        """

        # ---- 1. Build unsigned transaction via unsafe_moveCall ----------- #
        build_resp = await self._rpc("unsafe_moveCall", [
            self._address,           # signer
            self._package_id,        # package
            "registry",              # module
            "register",              # function
            [],                      # type_arguments
            [                        # arguments
                self._registry_id,   #   &mut Registry
                thread_id,           #   thread_id: String
                blob_id,             #   blob_id: String
            ],
            None,                    # gas coin (auto-select)
            self._GAS_BUDGET,        # gas budget
        ])

        if "error" in build_resp:
            raise SuiRegistryError(
                f"Failed to build register transaction for thread "
                f"{thread_id!r}: {build_resp['error']}",
                thread_id=thread_id,
                rpc_error=build_resp["error"],
            )

        tx_bytes_b64: str = build_resp["result"]["txBytes"]

        # ---- 2. Sign ----------------------------------------------------- #
        signature = self._sign_tx_bytes(tx_bytes_b64)

        # ---- 3. Execute -------------------------------------------------- #
        exec_resp = await self._rpc("sui_executeTransactionBlock", [
            tx_bytes_b64,
            [signature],
            {
                "showEffects": True,
                "showEvents": False,
                "showInput": False,
                "showObjectChanges": False,
                "showBalanceChanges": False,
            },
            "WaitForLocalExecution",
        ])

        if "error" in exec_resp:
            raise SuiRegistryError(
                f"Failed to execute register transaction for thread "
                f"{thread_id!r}: {exec_resp['error']}",
                thread_id=thread_id,
                rpc_error=exec_resp["error"],
            )

        result = exec_resp["result"]
        digest: str = result["digest"]

        # Verify on-chain success.
        effects = result.get("effects", {})
        status_obj = effects.get("status", {})
        status = status_obj.get("status")

        if status != "success":
            on_chain_err = status_obj.get("error", "unknown error")
            raise SuiRegistryError(
                f"Register transaction reverted on-chain for thread "
                f"{thread_id!r} (digest {digest}): {on_chain_err}",
                thread_id=thread_id,
                rpc_error=on_chain_err,
            )

        return digest

    async def lookup_blob(self, thread_id: str) -> Optional[str]:
        """Look up the ``blob_id`` associated with *thread_id* in the on-chain registry.

        Uses the ``suix_getDynamicFieldObject`` RPC to query the registry's
        dynamic field table keyed by ``0x1::string::String``.

        Parameters
        ----------
        thread_id : str
            The LangGraph thread identifier to look up.

        Returns
        -------
        str | None
            The Walrus ``blob_id`` if found, or ``None`` if the thread has
            no checkpoint stored yet.

        Raises
        ------
        SuiRegistryError
            On unexpected RPC errors (not "field not found").
        """

        resp = await self._rpc("suix_getDynamicFieldObject", [
            self._registry_id,
            {
                "type": "0x1::string::String",
                "value": thread_id,
            },
        ])

        # ---- Handle RPC-level errors ------------------------------------- #
        if "error" in resp:
            error = resp["error"]
            # Dynamic field not found is a normal "miss" — return None.
            if isinstance(error, dict):
                code = error.get("code", 0)
                msg = str(error.get("message", ""))
                if (
                    code == -32000
                    or "DynamicFieldNotFound" in msg
                    or "Cannot find dynamic field" in msg
                    or "could not find the referenced object" in msg.lower()
                ):
                    return None
            raise SuiRegistryError(
                f"RPC error looking up thread {thread_id!r}: {error}",
                thread_id=thread_id,
                rpc_error=error,
            )

        # ---- Extract blob_id from the dynamic field object --------------- #
        data = resp.get("result", {}).get("data")
        if data is None:
            # The field doesn't exist (some RPC versions return null data
            # instead of an error).
            return None

        # Handle the case where result.error exists at the result level
        # (some Sui versions nest the "not found" inside result.error).
        result_error = resp.get("result", {}).get("error")
        if result_error is not None:
            error_obj = result_error if isinstance(result_error, dict) else {"message": str(result_error)}
            code = error_obj.get("code", "")
            msg = str(error_obj.get("message", error_obj))
            if "NotFound" in msg or "not found" in msg.lower() or code == "dynamicFieldNotFound":
                return None
            raise SuiRegistryError(
                f"Sui result-level error looking up thread {thread_id!r}: "
                f"{result_error}",
                thread_id=thread_id,
                rpc_error=result_error,
            )

        # Expected shape (for Field<String, String>):
        #   data.content.fields.value  →  blob_id string
        try:
            content = data["content"]
            fields = content["fields"]
            blob_id = fields["value"]
        except (KeyError, TypeError) as exc:
            raise SuiRegistryError(
                f"Unexpected dynamic field structure for thread "
                f"{thread_id!r}: {data}",
                thread_id=thread_id,
            ) from exc

        if not isinstance(blob_id, str):
            raise SuiRegistryError(
                f"Expected blob_id to be a string for thread {thread_id!r}, "
                f"got {type(blob_id).__name__}: {blob_id!r}",
                thread_id=thread_id,
            )

        return blob_id
