"""Configuration loader from YAML + environment variables."""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from core.exceptions import ConfigError


class Config:
    """Hierarchical configuration backed by YAML file and env vars."""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        """Get a dot-separated key like 'trading.max_positions'."""
        parts = key.split(".")
        val = self._data
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
                if val is None:
                    return default
            else:
                return default
        return val

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise ConfigError(f"Missing config key: {key}")
        return val

    @property
    def trading(self) -> dict:
        return self._data.get("trading", {})

    @property
    def scoring(self) -> dict:
        return self._data.get("scoring", {})

    @property
    def screener(self) -> dict:
        return self._data.get("screener", {})

    @property
    def schedule(self) -> dict:
        return self._data.get("schedule", {})

    @property
    def alpaca_api_key(self) -> str:
        return os.environ["ALPACA_API_KEY"]

    @property
    def alpaca_secret_key(self) -> str:
        return os.environ["ALPACA_SECRET_KEY"]

    @property
    def alpaca_base_url(self) -> str:
        return os.environ.get(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )

    @property
    def db_path(self) -> str:
        return self.get("database.path", "data/trading.db")

    @property
    def log_file(self) -> str:
        return self.get("logging.file", "data/logs/trading.log")

    @property
    def log_level(self) -> str:
        return self.get("logging.level", "INFO")


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from YAML file and .env."""
    project_root = Path(__file__).parent.parent
    load_dotenv(project_root / ".env")

    config_file = project_root / config_path
    if not config_file.exists():
        raise ConfigError(f"Config file not found: {config_file}")

    with open(config_file) as f:
        data = yaml.safe_load(f)

    return Config(data)
