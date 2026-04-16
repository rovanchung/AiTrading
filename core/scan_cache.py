"""Shared scan cache — lets multiple version processes share one scan.

Only one process scans at a time (file lock). Others wait or read the
cached result if it's fresh enough.
"""

import fcntl
import logging
import os
import pickle
import time
from pathlib import Path

logger = logging.getLogger("aitrading.core.scan_cache")

# Default: cache lives in data/ alongside the DBs
_CACHE_DIR = Path(__file__).parent.parent / "data" / "scan_cache"
_LOCK_PATH = _CACHE_DIR / "scan.lock"
_SCORED_PATH = _CACHE_DIR / "scored.pkl"
_DATA_PATH = _CACHE_DIR / "data.pkl"
_SHORTLIST_PATH = _CACHE_DIR / "shortlist.pkl"


def _ensure_dir():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_age_seconds() -> float:
    """How old the cached scored results are, in seconds. Returns inf if no cache."""
    try:
        return time.time() - os.path.getmtime(_SCORED_PATH)
    except FileNotFoundError:
        return float("inf")


def is_fresh(max_age_seconds: float = 120) -> bool:
    """True if the cache exists and is younger than max_age_seconds."""
    return cache_age_seconds() < max_age_seconds


def save(scored, data, shortlist=None):
    """Write scan results to disk."""
    _ensure_dir()
    with open(_SCORED_PATH, "wb") as f:
        pickle.dump(scored, f)
    with open(_DATA_PATH, "wb") as f:
        pickle.dump(data, f)
    if shortlist is not None:
        with open(_SHORTLIST_PATH, "wb") as f:
            pickle.dump(shortlist, f)
    logger.info(f"Scan cache saved: {len(scored)} scored, {len(data)} data frames")


def load():
    """Read cached scan results. Returns (scored, data) or (None, None)."""
    try:
        with open(_SCORED_PATH, "rb") as f:
            scored = pickle.load(f)
        with open(_DATA_PATH, "rb") as f:
            data = pickle.load(f)
        age = cache_age_seconds()
        logger.info(f"Scan cache loaded: {len(scored)} scored, {len(data)} data frames ({age:.0f}s old)")
        return scored, data
    except (FileNotFoundError, pickle.UnpicklingError, EOFError) as e:
        logger.warning(f"Failed to load scan cache: {e}")
        return None, None


def load_shortlist():
    """Read cached shortlist. Returns list or None."""
    try:
        with open(_SHORTLIST_PATH, "rb") as f:
            return pickle.load(f)
    except (FileNotFoundError, pickle.UnpicklingError, EOFError):
        return None


def acquire_scan_lock(timeout: float = 5.0):
    """Try to acquire the scan lock (non-blocking with short retry).

    Returns the lock file descriptor if acquired, None if another process holds it.
    """
    _ensure_dir()
    fd = open(_LOCK_PATH, "w")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (IOError, OSError):
            if time.time() >= deadline:
                fd.close()
                return None
            time.sleep(0.2)


def release_scan_lock(fd):
    """Release the scan lock."""
    if fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except (IOError, OSError):
            pass
