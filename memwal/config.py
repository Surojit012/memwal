"""
memwal.config — Environment-driven configuration for MemWal.

Loads settings from a .env file (via python-dotenv) and validates
that every required field is present before returning a frozen Config
dataclass.  Missing fields raise ValueError with an explicit message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Immutable configuration for the MemWal package.

    Required fields (must be set in the environment or .env):
        SUI_PRIVATE_KEY      – base64- or hex-encoded Ed25519 private key
        REGISTRY_PACKAGE_ID  – Sui Move package ID (set after contract deploy)
        REGISTRY_OBJECT_ID   – Shared registry object ID (set after deploy)

    Optional fields (have sensible defaults for Sui testnet / Walrus testnet):
        SUI_RPC_URL          – Full-node JSON-RPC endpoint
        WALRUS_PUBLISHER     – Walrus publisher (store) endpoint
        WALRUS_AGGREGATOR    – Walrus aggregator (read) endpoint
        STORAGE_EPOCHS       – Number of Walrus storage epochs to request
    """

    # -- Required ---------------------------------------------------------- #
    SUI_PRIVATE_KEY: str = ""
    REGISTRY_PACKAGE_ID: str = ""
    REGISTRY_OBJECT_ID: str = ""

    # -- Optional (defaults target testnet) -------------------------------- #
    SUI_RPC_URL: str = "https://fullnode.testnet.sui.io:443"
    WALRUS_PUBLISHER: str = "https://publisher.walrus-testnet.walrus.space"
    WALRUS_AGGREGATOR: str = "https://aggregator.walrus-testnet.walrus.space"
    STORAGE_EPOCHS: int = 5


# ---------------------------------------------------------------------------
# Required-field names (validated at load time)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS: tuple[str, ...] = (
    "SUI_PRIVATE_KEY",
    "REGISTRY_PACKAGE_ID",
    "REGISTRY_OBJECT_ID",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(dotenv_path: Optional[str | Path] = None) -> Config:
    """Read configuration from environment variables (and an optional .env file).

    Parameters
    ----------
    dotenv_path:
        Explicit path to a ``.env`` file.  When *None*, ``python-dotenv``
        searches upward from the working directory.

    Returns
    -------
    Config
        A frozen dataclass with every setting populated.

    Raises
    ------
    ValueError
        If any required environment variable is missing or empty.
    """

    # Load .env into os.environ (existing vars are NOT overwritten).
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    # Collect values for every Config field from the environment.
    kwargs: dict[str, object] = {}
    for f in fields(Config):
        env_val = os.environ.get(f.name)
        if env_val is not None and env_val != "":
            # Coerce to the declared type (handles int for STORAGE_EPOCHS).
            if f.type == "int":
                try:
                    kwargs[f.name] = int(env_val)
                except ValueError:
                    raise ValueError(
                        f"Environment variable {f.name} must be an integer, "
                        f"got {env_val!r}"
                    )
            else:
                kwargs[f.name] = env_val

    # Validate required fields.
    missing = [name for name in _REQUIRED_FIELDS if not kwargs.get(name)]
    if missing:
        raise ValueError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in your .env file or export them in your shell."
        )

    return Config(**kwargs)
