from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from signal_score.constants import HUBSPOT_API_BASE, HUBSPOT_APP_URL


class ConfigError(Exception):
    """Missing or invalid local configuration."""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class Config:
    token: str = field(repr=False)
    logs_dir: Path
    hubspot_base_url: str = HUBSPOT_API_BASE
    hubspot_app_url: str = HUBSPOT_APP_URL


def load_config(env_path: Path | None = None) -> Config:
    root = project_root()
    dotenv_path = env_path or (root / ".env.local")
    load_dotenv(dotenv_path)

    token = os.environ.get("HUBSPOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("HUBSPOT_TOKEN is not set in .env.local")

    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return Config(token=token, logs_dir=logs_dir)
