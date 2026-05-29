#!/usr/bin/env python3
"""Initialize (or reset) the AiTrading database.

Usage:
    python setup_db.py                          # init default DB (data/trading.db)
    python setup_db.py --version v1             # init the per-account DB for v1..v4
    python setup_db.py --version v1 --reset     # delete the file first, then re-init
    python setup_db.py --init-all               # non-destructively init every DB (default + all accounts)
    python setup_db.py --reset-all              # reset every per-account DB (v1..v4)
"""

import os
import sys

from core.config import load_config
from core.database import Database


def _reset_one(version):
    """Delete and re-create schema for one account (or default if version=None)."""
    config = load_config(version=version)
    path = config.db_path
    if os.path.isfile(path):
        os.remove(path)
        print(f"Deleted {path}")
    db = Database(path)
    db.init_schema()
    label = f" [{version}]" if version else ""
    print(f"Database initialized at {path}{label}")
    db.close()


def _init_one(version):
    config = load_config(version=version)
    db = Database(config.db_path)
    db.init_schema()
    label = f" [{version}]" if version else ""
    print(f"Database initialized at {config.db_path}{label}")
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
    do_reset = "--reset" in args
    do_reset_all = "--reset-all" in args
    do_init_all = "--init-all" in args

    for i, a in enumerate(args):
        if a == "--version" and i + 1 < len(args):
            version = args[i + 1]
            break

    if do_init_all:
        _init_one(None)  # default trading.db
        for ver in _all_versions():
            _init_one(ver)
        return

    if do_reset_all:
        for ver in _all_versions():
            _reset_one(ver)
        return

    if do_reset:
        _reset_one(version)
    else:
        _init_one(version)


if __name__ == "__main__":
    main()
