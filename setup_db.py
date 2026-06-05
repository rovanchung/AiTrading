#!/usr/bin/env python3
"""Initialize (or reset) the AiTrading database.

Usage:
    python setup_db.py --version v3                 # init the per-account DB for v3 or v4
    python setup_db.py --version v3 --reset         # delete the file first, then re-init
    python setup_db.py --version v3 --account real  # init data/trading_v3_real.db
    python setup_db.py --init-all                   # non-destructively init every per-account DB (v3, v4)
    python setup_db.py --reset-all                  # reset every per-account DB (v3, v4)
"""

import os
import sys

from core.config import load_config
from core.database import Database


def _label(version, account):
    if version and account:
        return f" [{version}:{account}]"
    return f" [{version}]" if version else ""


def _reset_one(version, account=None):
    """Delete and re-create schema for one (version, account) DB."""
    config = load_config(version=version, account=account)
    path = config.db_path
    if os.path.isfile(path):
        os.remove(path)
        print(f"Deleted {path}")
    db = Database(path)
    db.init_schema()
    print(f"Database initialized at {path}{_label(version, account)}")
    db.close()


def _init_one(version, account=None):
    config = load_config(version=version, account=account)
    db = Database(config.db_path)
    db.init_schema()
    print(f"Database initialized at {config.db_path}{_label(version, account)}")
    db.close()


def _all_versions():
    import yaml
    from pathlib import Path

    cfg_path = Path(__file__).parent / "config.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    return list((cfg.get("accounts") or {}).keys())


def main():
    args = sys.argv[1:]
    version = None
    account = None
    do_reset = "--reset" in args
    do_reset_all = "--reset-all" in args
    do_init_all = "--init-all" in args

    for i, a in enumerate(args):
        if a == "--version" and i + 1 < len(args):
            version = args[i + 1]
        elif a == "--account" and i + 1 < len(args):
            account = args[i + 1]

    if do_init_all:
        for ver in _all_versions():
            _init_one(ver)
        return

    if do_reset_all:
        for ver in _all_versions():
            _reset_one(ver)
        return

    if not version:
        print(
            "Error: --version is required (choices: v3, v4), "
            "or use --init-all / --reset-all."
        )
        sys.exit(1)

    if do_reset:
        _reset_one(version, account)
    else:
        _init_one(version, account)


if __name__ == "__main__":
    main()
