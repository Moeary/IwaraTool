"""Application-wide logging: rotating file under data/logs plus crash hooks.

The packaged build runs without a console, so everything that used to be
swallowed by ``except Exception`` ends up here instead.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

LOG_FILE_NAME = "iwaratool.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"

_configured = False


def log_dir() -> str:
    from .config import app_config

    return os.path.join(app_config.app_data_dir, "logs")


def log_file_path() -> str:
    return os.path.join(log_dir(), LOG_FILE_NAME)


def setup_logging(level: int = logging.INFO, directory: str | None = None) -> logging.Logger:
    """Configure the ``iwaratool`` logger once and install crash hooks."""
    global _configured
    root = logging.getLogger("iwaratool")
    if _configured:
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)
    try:
        target_dir = directory or log_dir()
        os.makedirs(target_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(target_dir, LOG_FILE_NAME),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass  # read-only install dir: fall back to stderr only

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    _install_crash_hooks(root)
    _configured = True
    return root


def _install_crash_hooks(logger: logging.Logger) -> None:
    previous_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc, tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        logger.error(
            "Uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook

    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }

        def _qt_handler(msg_type, _context, message):
            logger.log(levels.get(msg_type, logging.WARNING), "Qt: %s", message)

        qInstallMessageHandler(_qt_handler)
    except Exception:  # pragma: no cover - Qt unavailable
        logger.debug("Qt message handler not installed", exc_info=True)


def get_logger(name: str) -> logging.Logger:
    """Child logger under the ``iwaratool`` namespace."""
    short = name[4:] if name.startswith("app.") else name
    return logging.getLogger(f"iwaratool.{short}")
