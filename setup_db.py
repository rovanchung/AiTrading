#!/usr/bin/env python3
"""Initialize the AiTrading database."""

from core.config import load_config
from core.database import Database


def main():
    config = load_config()
    db = Database(config.db_path)
    db.init_schema()
    print(f"Database initialized at {config.db_path}")
    db.close()


if __name__ == "__main__":
    main()
