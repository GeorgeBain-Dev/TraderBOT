from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def setup_logging(log_dir: str = "logs", level: int = logging.INFO, run_id: Optional[str] = None) -> logging.Logger:
    ensure_dir(log_dir)
    logger = logging.getLogger("bot")
    logger.setLevel(level)

    if logger.handlers and run_id is None:
        return logger

    # Reconfigure the logger if a specific run_id is provided.
    if logger.handlers and run_id is not None:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    # Generate unique run ID if not provided
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    
    # Python's default uses localtime; force UTC timestamps
    import time as _time

    def time_gmtime(*args):  # noqa: ANN001
        return _time.gmtime(*args)

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03dZ | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    class _UTCFormatter(logging.Formatter):
        converter = staticmethod(time_gmtime)

    fmt_utc = _UTCFormatter(fmt=fmt._fmt, datefmt=fmt.datefmt)

    # Console
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt_utc)
    logger.addHandler(sh)

    # File - unique per run
    logfile = os.path.join(log_dir, f"bot_{run_id}.log")
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt_utc)
    logger.addHandler(fh)

    logger.propagate = False
    rel_logfile = os.path.relpath(logfile)
    logger.info(f"Started new run with log file: {rel_logfile}")
    return logger


@dataclass(frozen=True)
class BotEvent:
    ts: datetime
    level: str
    message: str

