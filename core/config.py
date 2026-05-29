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
        return os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

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


def activate_version(
    version: str, config_path: str = "config.yaml", account: str = None
) -> None:
    """Set environment variables for a trading version (v3 or v4).

    Picks the Alpaca credential pair to use for this version and copies it
    into ALPACA_API_KEY / ALPACA_SECRET_KEY so downstream modules (e.g.
    alpaca_data.py, which reads os.environ directly) use the correct account.

    Key selection (in order of precedence):
      1. `account` argument (from --account CLI flag) — wins when set.
         Enables running the same version against different Alpaca accounts
         concurrently in separate processes.
      2. ALPACA_ACCOUNT_{VERSION} — .env pointer to a suffix. Used when no
         CLI override is given. Lets the user freely name credential pairs
         (e.g. MAIN, WIFE, V4_2) and map any pair to a version without
         renaming the keys themselves.
      3. ALPACA_API_KEY_{VERSION} / ALPACA_SECRET_KEY_{VERSION} — legacy
         naming convention (still supported for backward compatibility).
      4. ALPACA_API_KEY / ALPACA_SECRET_KEY — bare names; final fallback.

    Must be called BEFORE any Alpaca client is initialized.
    """
    project_root = Path(__file__).parent.parent
    load_dotenv(project_root / ".env")

    version_upper = version.upper()
    if account:
        suffix = account.strip().upper()
    else:
        pointer = os.environ.get(f"ALPACA_ACCOUNT_{version_upper}", "").strip()
        suffix = pointer or version_upper

    api_key = os.environ.get(f"ALPACA_API_KEY_{suffix}", "")
    secret_key = os.environ.get(f"ALPACA_SECRET_KEY_{suffix}", "")

    if not account and (not api_key or api_key == "CHANGE_ME"):
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key or api_key == "CHANGE_ME":
        raise ConfigError(
            f"Alpaca credentials not configured for {version} "
            f"(account={account or 'default'}). "
            f"Set ALPACA_API_KEY_{suffix} / ALPACA_SECRET_KEY_{suffix} in .env, "
            f"or pass --account <suffix> / set ALPACA_ACCOUNT_{version_upper} "
            f"to point at a differently-named key pair."
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


def load_config(
    config_path: str = "config.yaml", version: str = None, account: str = None
) -> Config:
    """Load configuration from YAML file and .env.

    If `version` is specified (v3 or v4), applies account-specific overrides
    for database path and strategy parameters from config.yaml.

    If `account` is also specified, overrides the database path to
    `data/trading_{version}_{account_lower}.db` so concurrent processes
    running the same version against different Alpaca accounts don't
    collide on the same SQLite file.
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
        # Merge all other account keys into trading section. Reserved keys
        # (database_path, account_overrides) are structural, not trading params.
        trading = data.setdefault("trading", {})
        reserved = {"database_path", "account_overrides"}
        for key, val in acct.items():
            if key not in reserved:
                trading[key] = val

        # Per-account override block (e.g. accounts.v4.account_overrides.v4_2)
        # is merged on top of the version defaults when --account is set.
        if account:
            per_account = acct.get("account_overrides", {}) or {}
            override = per_account.get(account.lower(), {}) or {}
            for key, val in override.items():
                trading[key] = val

    # Per-account DB override — must be unique per (version, account) so
    # simultaneous processes don't fight over the same SQLite file.
    if version and account:
        db_path = f"data/trading_{version}_{account.lower()}.db"
        data.setdefault("database", {})["path"] = db_path

    # Per-version log file so concurrent versions/accounts write to separate
    # files instead of interleaving into one shared main.log.
    if version:
        suffix = f"{version}_{account.lower()}" if account else version
        data.setdefault("logging", {})["file"] = f"data/logs/main_{suffix}.log"

    return Config(data)
