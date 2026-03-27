"""Structured logging setup for AiTrading."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import Config


def setup_logging(config: Config) -> logging.Logger:
    """Configure root logger with file rotation and console output."""
    log_file = Path(config.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("aitrading")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation
    max_bytes = config.get("logging.max_size_mb", 50) * 1024 * 1024
    backup_count = config.get("logging.backup_count", 5)
    fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def setup_transaction_logger(config: Config) -> logging.Logger:
    """Configure a dedicated transaction logger that writes to a separate file."""
    log_dir = Path(config.log_file).parent
    txn_file = log_dir / "transactions.log"
    txn_file.parent.mkdir(parents=True, exist_ok=True)

    txn_logger = logging.getLogger("aitrading.transactions")
    txn_logger.propagate = True  # also appears in main log

    fmt = logging.Formatter(
        "%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    max_bytes = config.get("logging.max_size_mb", 50) * 1024 * 1024
    backup_count = config.get("logging.backup_count", 5)
    fh = RotatingFileHandler(txn_file, maxBytes=max_bytes, backupCount=backup_count)
    fh.setFormatter(fmt)
    txn_logger.addHandler(fh)

    return txn_logger
