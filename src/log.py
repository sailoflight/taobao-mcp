"""Lightweight run logger → output/run.log (CLAUDE.md Phase 6).

One shared file logger for session/captcha/backoff events so a flagged run leaves
a trail. Console stays quiet (the human watches the browser, not the terminal).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config import load_config

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    out = Path(load_config().output.dir)
    out.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("taobao_sourcing")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        fh = logging.FileHandler(out / "run.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(fh)
    _logger = lg
    return lg
