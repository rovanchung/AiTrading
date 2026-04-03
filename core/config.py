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

    def set(self, key: str, value: Any) -> None:
        """Set a dot-separated key like 'macro.enabled'."""
        parts = key.split(".")
        d = self._data
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value

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
    def finnhub_api_key(self) -> str:
        return os.environ.get("FINNHUB_API_KEY", "")

    @property
    def db_path(self) -> str:
        return self.get("database.path", "data/trading.db")

    @property
    def log_file(self) -> str:
        return self.get("logging.file", "data/logs/main.log")

    @property
    def log_level(self) -> str:
        return self.get("logging.level", "INFO")


def activate_version(version: str, config_path: str = "config.yaml") -> None:
    """Set environment variables for a trading version (v1 or v2).

    Swaps ALPACA_API_KEY / ALPACA_SECRET_KEY to the version-specific
    credentials so all modules (including alpaca_data.py which reads
    os.environ directly) use the correct account.

    Must be called BEFORE any Alpaca client is initialized.
    """
    project_root = Path(__file__).parent.parent
    load_dotenv(project_root / ".env")

    suffix = f"_{version.upper()}"  # _V1 or _V2
    api_key = os.environ.get(f"ALPACA_API_KEY{suffix}", "")
    secret_key = os.environ.get(f"ALPACA_SECRET_KEY{suffix}", "")

    if not api_key or not secret_key or api_key == "CHANGE_ME":
        raise ConfigError(
            f"Alpaca credentials not configured for {version}. "
            f"Set ALPACA_API_KEY{suffix} and ALPACA_SECRET_KEY{suffix} in .env"
        )

    os.environ["ALPACA_API_KEY"] = api_key
    os.environ["ALPACA_SECRET_KEY"] = secret_key

    # Reset lazy-initialized alpaca data clients so they pick up new creds
    try:
        from core import alpaca_data
        alpaca_data._stock_client = None
        alpaca_data._news_client = None
    except ImportError:
        pass


def load_config(config_path: str = "config.yaml", version: str = None) -> Config:
    """Load configuration from YAML file and .env.

    If version is specified (v1/v2), applies account-specific overrides
    for database path and strategy version.
    """
    project_root = Path(__file__).parent.parent
    load_dotenv(project_root / ".env")

    config_file = project_root / config_path
    if not config_file.exists():
        raise ConfigError(f"Config file not found: {config_file}")

    with open(config_file) as f:
        data = yaml.safe_load(f)

    # Apply version-specific overrides
    if version:
        accounts = data.get("accounts", {})
        acct = accounts.get(version, {})
        if acct.get("database_path"):
            data.setdefault("database", {})["path"] = acct["database_path"]
        if acct.get("strategy_version"):
            data.setdefault("trading", {})["strategy_version"] = acct["strategy_version"]

    return Config(data)
