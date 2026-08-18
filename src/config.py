"""Typed loader for config.toml (CLAUDE.md §6).

Shared infrastructure (owned by the orchestrator). Uses stdlib ``tomllib``
(Python 3.11+). Missing file or keys fall back to the spec's defaults, so the
server still runs if config.toml is absent.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserCfg:
    channel: str = "chrome"
    executable_path: str = ""     # if set, pin this exact browser binary (overrides channel)
    user_data_dir: str = "./user_data/chrome_profile"
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    headless: bool = False


@dataclass(frozen=True)
class PacingCfg:
    min_delay_s: float = 2.0
    max_delay_s: float = 6.0
    scroll_steps: int = 4
    max_products_per_minute: int = 6


@dataclass(frozen=True)
class LimitsCfg:
    max_reviews: int = 60
    review_pages: int = 4


@dataclass(frozen=True)
class OutputCfg:
    dir: str = "./output"


@dataclass(frozen=True)
class DetailCfg:
    # Entry trick for the 详情 strip: the SSR renders the full 详情 (图文详情 / .desc-root)
    # only when the request carries the account's marketing mi_id. Discovered live
    # (2026-08-18); stable across products for this account. Configurable in config.toml
    # in case Taobao rotates/revokes it.
    mi_id: str = ""


@dataclass(frozen=True)
class Config:
    browser: BrowserCfg
    pacing: PacingCfg
    limits: LimitsCfg
    output: OutputCfg
    detail: DetailCfg


_CACHE: dict = {}


def _persisted_miid() -> str:
    """Runtime mi_id override from output/.miid.json (written by taobao_get_miid).

    Highest priority: a freshly captured mi_id (human-clicked, low-risk) beats the
    static config value. File is gitignored + machine-local.
    """
    try:
        p = Path("output") / ".miid.json"
        if p.exists():
            import json as _json

            d = _json.loads(p.read_text(encoding="utf-8"))
            return (d.get("mi_id") or "").strip()
    except Exception:
        pass
    return ""


def load_config(path: str | Path = "config.toml") -> Config:
    """Parse config.toml into a typed Config. Cached, but RE-READ when the file's mtime
    changes, so a long-running server picks up runtime edits. A sibling
    ``config.local.toml`` (or ``TAOBAO_CONFIG_LOCAL``) overrides machine-specific
    values and is intentionally ignored by Git. Unknown keys are ignored."""
    p = Path(path)
    local_override = os.environ.get("TAOBAO_CONFIG_LOCAL", "").strip()
    local_p = Path(local_override).expanduser() if local_override else p.with_name("config.local.toml")
    mtime = p.stat().st_mtime if p.exists() else 0.0
    local_mtime = local_p.stat().st_mtime if local_p.exists() else 0.0
    miid_file = Path("output") / ".miid.json"
    miid_mtime = miid_file.stat().st_mtime if miid_file.exists() else 0.0
    key = (str(p), mtime, str(local_p), local_mtime, miid_mtime)
    if key in _CACHE:
        return _CACHE[key]

    data: dict = {}
    if p.exists():
        with p.open("rb") as f:
            data = tomllib.load(f)

    if local_p.exists():
        with local_p.open("rb") as f:
            local_data = tomllib.load(f)
        for section, values in local_data.items():
            if isinstance(values, dict):
                data.setdefault(section, {}).update(values)

    def _filter(cls, section: str) -> dict:
        allowed = cls.__dataclass_fields__.keys()
        return {k: v for k, v in data.get(section, {}).items() if k in allowed}

    cfg = Config(
        browser=BrowserCfg(**_filter(BrowserCfg, "browser")),
        pacing=PacingCfg(**_filter(PacingCfg, "pacing")),
        limits=LimitsCfg(**_filter(LimitsCfg, "limits")),
        output=OutputCfg(**_filter(OutputCfg, "output")),
        detail=DetailCfg(mi_id=_persisted_miid() or _filter(DetailCfg, "detail").get("mi_id", "")),
    )
    _CACHE.clear()      # keep only the latest base + local override pair
    _CACHE[key] = cfg
    return cfg
