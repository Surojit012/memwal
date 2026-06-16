from __future__ import annotations
import base64
import hashlib
from typing import Any, Optional
import httpx
from memwal.config import Config


class SuiRegistryError(Exception):
    
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


def _load_keypair(raw_key: str):
    
    from nacl.signing import SigningKey

    seed: Optional[bytes] = None

    
    try:
        decoded = base64.b64decode(raw_key, validate=True)
        if len(decoded) == 33 and decoded[0] == 0x00:
            
            seed = decoded[1:]
        elif len(decoded) == 32:
            seed = decoded
        elif len(decoded) == 64:
            
            seed = decoded[:32]
    except Exception:
        pass

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
    
    h = hashlib.blake2b(digest_size=32)
    h.update(bytes([0x00]))          
    h.update(bytes(verify_key))      
    return "0x" + h.hexdigest()


class SuiRegistry:
    _INTENT_PREFIX: bytes = bytes([0, 0, 0])

    _ED25519_FLAG: int = 0x00
    
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

    async def __aenter__(self) -> "SuiRegistry":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


    def _get_client(self) -> httpx.AsyncClient:
        
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    @property
    def address(self) -> str:
        
        return self._address

    async def _rpc(self, method: str, params: list) -> dict:
        
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
        
        tx_bytes = base64.b64decode(tx_bytes_b64)

        intent_msg = self._INTENT_PREFIX + tx_bytes

        digest = hashlib.blake2b(intent_msg, digest_size=32).digest()

        signed = self._signing_key.sign(digest)
        sig_bytes = signed.signature  

        serialised = (
            bytes([self._ED25519_FLAG])
            + sig_bytes
            + bytes(self._verify_key)
        )
        return base64.b64encode(serialised).decode("ascii")


    async def register_blob(self, thread_id: str, blob_id: str) -> str:
        
        build_resp = await self._rpc("unsafe_moveCall", [
            self._address,           
            self._package_id,        
            "registry",              
            "register",              
            [],                      
            [                        
                self._registry_id,   
                thread_id,           
                blob_id,             
            ],
            None,                    
            self._GAS_BUDGET,        
        ])

        if "error" in build_resp:
            raise SuiRegistryError(
                f"Failed to build register transaction for thread "
                f"{thread_id!r}: {build_resp['error']}",
                thread_id=thread_id,
                rpc_error=build_resp["error"],
            )

        tx_bytes_b64: str = build_resp["result"]["txBytes"]

        
        signature = self._sign_tx_bytes(tx_bytes_b64)

        
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
        
        resp = await self._rpc("suix_getDynamicFieldObject", [
            self._registry_id,
            {
                "type": "0x1::string::String",
                "value": thread_id,
            },
        ])

        
        if "error" in resp:
            error = resp["error"]
            
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

        data = resp.get("result", {}).get("data")
        if data is None:
            return None

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