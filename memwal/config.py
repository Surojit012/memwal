from __future__ import annotations
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    SUI_PRIVATE_KEY: str = ""
    REGISTRY_PACKAGE_ID: str = ""
    REGISTRY_OBJECT_ID: str = ""

    SUI_RPC_URL: str = "https://fullnode.testnet.sui.io:443"
    WALRUS_PUBLISHER: str = "https://publisher.walrus-testnet.walrus.space"
    WALRUS_AGGREGATOR: str = "https://aggregator.walrus-testnet.walrus.space"
    STORAGE_EPOCHS: int = 5

_REQUIRED_FIELDS: tuple[str, ...] = (
    "SUI_PRIVATE_KEY",
    "REGISTRY_PACKAGE_ID",
    "REGISTRY_OBJECT_ID",
)


def load_config(dotenv_path: Optional[str | Path] = None) -> Config:
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    kwargs: dict[str, object] = {}
    for f in fields(Config):
        env_val = os.environ.get(f.name)
        if env_val is not None and env_val != "":
            
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

    missing = [name for name in _REQUIRED_FIELDS if not kwargs.get(name)]
    if missing:
        raise ValueError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in your .env file or export them in your shell."
        )

    return Config(**kwargs)
