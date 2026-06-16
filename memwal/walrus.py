from __future__ import annotations
import asyncio
from typing import Optional
import httpx


class WalrusError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


_MAX_RETRIES: int = 3
_BACKOFF_BASE: float = 0.5  
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


async def _retry_async(coro_factory, *, max_retries: int = _MAX_RETRIES):
    
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except WalrusError as exc:
            last_exc = exc
            
            if exc.status_code is not None and exc.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < max_retries:
                    delay = _BACKOFF_BASE * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                    continue
            
            raise
        except httpx.TransportError as exc:
            
            last_exc = exc
            if attempt < max_retries:
                delay = _BACKOFF_BASE * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                continue
            raise WalrusError(
                f"Walrus request failed after {max_retries} attempts: {exc}",
            ) from exc

    
    raise WalrusError(  
        f"Walrus request failed after {max_retries} attempts",
    ) from last_exc


class WalrusClient:
    def __init__(
        self,
        publisher_url: str,
        aggregator_url: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._publisher_url = publisher_url.rstrip("/")
        self._aggregator_url = aggregator_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "WalrusClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @staticmethod
    def _extract_blob_id(body: dict) -> str:
        if "newlyCreated" in body:
            try:
                return body["newlyCreated"]["blobObject"]["blobId"]
            except (KeyError, TypeError) as exc:
                raise WalrusError(
                    f"Unexpected 'newlyCreated' response structure: {body}"
                ) from exc

        if "alreadyCertified" in body:
            try:
                return body["alreadyCertified"]["blobId"]
            except (KeyError, TypeError) as exc:
                raise WalrusError(
                    f"Unexpected 'alreadyCertified' response structure: {body}"
                ) from exc

        raise WalrusError(
            f"Walrus response contains neither 'newlyCreated' nor "
            f"'alreadyCertified': {body}"
        )

    
    async def store_blob(self, data: bytes, epochs: int) -> str:
        url = f"{self._publisher_url}/v1/blobs?epochs={epochs}"
        client = self._get_client()

        async def _do_store():
            resp = await client.put(
                url,
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
            if resp.status_code not in (200, 201):
                raise WalrusError(
                    f"Walrus store failed — HTTP {resp.status_code}: "
                    f"{resp.text[:500]}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            try:
                body = resp.json()
            except Exception as exc:
                raise WalrusError(
                    f"Walrus returned non-JSON response: {resp.text[:500]}"
                ) from exc
            return self._extract_blob_id(body)

        return await _retry_async(_do_store)

    async def fetch_blob(self, blob_id: str) -> bytes:
        url = f"{self._aggregator_url}/v1/blobs/{blob_id}"
        client = self._get_client()

        async def _do_fetch():
            resp = await client.get(url)
            if resp.status_code != 200:
                raise WalrusError(
                    f"Walrus fetch failed for blob {blob_id!r} — "
                    f"HTTP {resp.status_code}: {resp.text[:500]}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.content

        return await _retry_async(_do_fetch)