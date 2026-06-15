"""
memwal.walrus — Async HTTP client for the Walrus blob store.

Provides store and fetch operations against the Walrus publisher /
aggregator endpoints with automatic retry (3 attempts, exponential
backoff) for transient failures.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class WalrusError(Exception):
    """Raised when a Walrus HTTP operation fails.

    Attributes
    ----------
    status_code : int | None
        HTTP status code returned by Walrus (None for non-HTTP errors).
    response_body : str | None
        Raw response body, useful for debugging.
    """

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


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

_MAX_RETRIES: int = 3
_BACKOFF_BASE: float = 0.5  # seconds — 0.5, 1.0, 2.0

# Status codes that are safe to retry on.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


async def _retry_async(coro_factory, *, max_retries: int = _MAX_RETRIES):
    """Execute an async callable with exponential-backoff retries.

    Parameters
    ----------
    coro_factory:
        A zero-argument callable that returns a *new* awaitable each call.
    max_retries:
        Total number of attempts (including the first).

    Returns
    -------
    The value returned by the awaitable on success.

    Raises
    ------
    WalrusError
        After all retries are exhausted.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except WalrusError as exc:
            last_exc = exc
            # Only retry on transient HTTP errors.
            if exc.status_code is not None and exc.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < max_retries:
                    delay = _BACKOFF_BASE * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                    continue
            # Non-retryable error — raise immediately.
            raise
        except httpx.TransportError as exc:
            # Network-level failures (DNS, connection reset, timeout, …).
            last_exc = exc
            if attempt < max_retries:
                delay = _BACKOFF_BASE * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                continue
            raise WalrusError(
                f"Walrus request failed after {max_retries} attempts: {exc}",
            ) from exc

    # Should never reach here, but satisfy the type checker.
    raise WalrusError(  # pragma: no cover
        f"Walrus request failed after {max_retries} attempts",
    ) from last_exc


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class WalrusClient:
    """Async HTTP client for Walrus blob storage.

    Parameters
    ----------
    publisher_url:
        Base URL of the Walrus publisher (store) endpoint.
    aggregator_url:
        Base URL of the Walrus aggregator (read) endpoint.
    timeout:
        Per-request timeout in seconds (default 30).

    Usage
    -----
    >>> from memwal.config import load_config
    >>> cfg = load_config()
    >>> async with WalrusClient(cfg.WALRUS_PUBLISHER, cfg.WALRUS_AGGREGATOR) as client:
    ...     blob_id = await client.store_blob(b"hello", epochs=5)
    ...     data   = await client.fetch_blob(blob_id)
    """

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

    # -- Context-manager protocol ------------------------------------------ #

    async def __aenter__(self) -> "WalrusClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- Internal helpers -------------------------------------------------- #

    def _get_client(self) -> httpx.AsyncClient:
        """Return the active ``httpx.AsyncClient``, creating one if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @staticmethod
    def _extract_blob_id(body: dict) -> str:
        """Extract ``blobId`` from either Walrus response shape.

        Shape 1 — newly created:
            { "newlyCreated": { "blobObject": { "blobId": "..." } } }

        Shape 2 — already certified:
            { "alreadyCertified": { "blobId": "..." } }
        """
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

    # -- Public API -------------------------------------------------------- #

    async def store_blob(self, data: bytes, epochs: int) -> str:
        """Store *data* as a Walrus blob and return its ``blob_id``.

        Parameters
        ----------
        data:
            Raw bytes to persist.
        epochs:
            Number of Walrus storage epochs to request.

        Returns
        -------
        str
            The Walrus ``blobId``.

        Raises
        ------
        WalrusError
            On HTTP or parsing failure (after retries).
        """

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
        """Fetch a previously stored blob by its *blob_id*.

        Parameters
        ----------
        blob_id:
            The Walrus ``blobId`` returned by :meth:`store_blob`.

        Returns
        -------
        bytes
            The raw blob content.

        Raises
        ------
        WalrusError
            On HTTP failure (after retries).
        """

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
