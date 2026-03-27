"""
logger.py
Centralised logging configuration for the Russian → English Live Translator.

Log layout inside  logs/ :
  sessions/
    session_2026-03-27_10-04-07.log   ← one file per Start→Stop cycle (DEBUG+)
    session_2026-03-27_10-07-20.log
    ...                               ← oldest auto-deleted, keeps last 20
  errors.log                          ← WARNING+ from every session, appended

Lifecycle:
  setup_logging()       — call once at process start (sets up errors.log + console)
  open_session_log()    — call when the user clicks Start (creates new session file)
  close_session_log()   — call when the user clicks Stop  (flushes and closes it)

Usage in any module:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("Something happened")
    log.error("Something broke", exc_info=True)
"""

import glob
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_SESSION_DIR = os.path.join(_LOG_DIR, "sessions")
_ERROR_LOG = os.path.join(_LOG_DIR, "errors.log")
_MAX_SESSION_FILES = 20
_INITIALISED = False
_SESSION_LOG_FILE: str = ""
_session_handler: logging.FileHandler | None = None


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Call once from main.py before any other import that uses get_logger().
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _INITIALISED, _SESSION_LOG_FILE
    if _INITIALISED:
        return
    _INITIALISED = True

    os.makedirs(_SESSION_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Silence noisy third-party HTTP debug loggers — they flood the log on
    # every model version check against huggingface.co.
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── persistent errors log (WARNING+, appended across sessions) ───────────
    efh = RotatingFileHandler(
        _ERROR_LOG,
        maxBytes=2 * 1024 * 1024,  # 2 MB max, keeps last 3 rotations
        backupCount=3,
        encoding="utf-8",
    )
    efh.setLevel(logging.WARNING)
    efh.setFormatter(fmt)
    root.addHandler(efh)

    # ── console handler (WARNING+ only) ──────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    root.info("Logging initialised — errors log: %s", _ERROR_LOG)


def _prune_session_files() -> None:
    """Delete oldest session log files beyond _MAX_SESSION_FILES."""
    pattern = os.path.join(_SESSION_DIR, "app_*.log")
    files = sorted(glob.glob(pattern))  # lexicographic = chronological for ISO stamps
    excess = len(files) - _MAX_SESSION_FILES
    if excess > 0:
        for old in files[:excess]:
            try:
                os.remove(old)
            except OSError:
                pass


def open_session_log() -> str:
    """
    Create a new timestamped session log file and attach it to the root logger.
    Called by the GUI when the user clicks Start.
    Returns the path of the new log file.
    """
    global _SESSION_LOG_FILE, _session_handler

    # Close any previously open session handler first
    close_session_log()

    os.makedirs(_SESSION_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _SESSION_LOG_FILE = os.path.join(_SESSION_DIR, f"session_{stamp}.log")

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.FileHandler(_SESSION_LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)

    logging.getLogger().addHandler(handler)
    _session_handler = handler

    _prune_session_files()
    logging.getLogger().info("Session log opened: %s", _SESSION_LOG_FILE)
    return _SESSION_LOG_FILE


def close_session_log() -> None:
    """
    Flush and detach the current session log handler.
    Called by the GUI when the user clicks Stop.
    """
    global _session_handler, _SESSION_LOG_FILE
    if _session_handler is None:
        return
    logging.getLogger().info("Session log closing: %s", _SESSION_LOG_FILE)
    logging.getLogger().removeHandler(_session_handler)
    _session_handler.flush()
    _session_handler.close()
    _session_handler = None


def get_session_log_path() -> str:
    """Return the path of the current session log file (empty if none open)."""
    return _SESSION_LOG_FILE


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. setup_logging() must have been called first."""
    return logging.getLogger(name)

    return logging.getLogger(name)
