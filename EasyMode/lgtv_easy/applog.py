"""Small rotating logger shared by the daemon and CLI.

Writes to the per-user log file (and optionally the console). Kept tiny on
purpose; the launcher scripts keep their own separate crash log.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .config import log_path

_LOGGER = None


def get_logger(to_console: bool = False) -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("lgtv_easy")
    # _LOGGER is a module global; the logger it caches is process-global. Import
    # this module twice under different names - which a PYTHONPATH'd install plus
    # a source tree manages easily - and the second import sees _LOGGER as None
    # and attaches a *second* file handler to the same logger. Nothing breaks;
    # every line just appears in the log twice, for ever, which makes the log
    # look like the app is doing everything twice. Ask the logger itself.
    if logger.handlers:
        _LOGGER = logger
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(path, maxBytes=512_000, backupCount=2,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if to_console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    _LOGGER = logger
    return logger
