"""Typed loader for config.toml (CLAUDE.md §6).

Shared infrastructure (owned by the orchestrator). Uses stdlib ``tomllib``
(Python 3.11+). Missing file or keys fall back to the spec's defaults, so the
server still runs if config.toml is absent.
"""

from __future__ import annotations

import json
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
class ClickCfg:
    """Human-like simulated click tuning (pacing.human_click).

    Keep cursor speed a real person's reach (~0.3-0.8s total). Over the remote bridge
    each mouse.move STEP round-trips, so many steps make a click take SECONDS — and
    artificial slowness looks MORE robotic, not less. Tune here without touching code.
    """
    enabled: bool = True
    path_steps_min: int = 4        # animated mouse-path steps during the approach
    path_steps_max: int = 9
    move_pause_min: float = 0.01   # s, pause between approach segments
    move_pause_max: float = 0.05
    hover_pause_min: float = 0.04  # s, pause at the element before pressing
    hover_pause_max: float = 0.12
    hold_min: float = 0.04         # s, press-and-hold before release
    hold_max: float = 0.12
    jitter_px: float = 2.5         # micro-jitter radius at the target (px)
    off_center: float = 0.15       # aim drift from center (fraction of the box dim)


@dataclass(frozen=True)
class LimitsCfg:
    max_reviews: int = 60
    review_pages: int = 4
    # Daily cap on the 收藏链路 (favorite + click + unfavorite) — the riskiest flow
    # (repeated favorite/unfavorite actions are a flag risk). fetch_detail with
    # miid_source="favorite" checks this before touching favorites.
    fav_flow_per_day: int = 30
    # Daily cap on taobao_search — 搜索列表页是滑块/风控第一触发源(2026-08-20 实测
    # 每次搜索都弹轻滑块), 超限直接拒绝并提示休息, 不让账号被反复标记。
    search_per_day: int = 30


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
class AntiRiskCfg:
    """Anti-block parameters, all surfaced in config.toml [anti_risk].

    Every runtime protection maps to a key here; a long-running server re-reads on
    mtime change (load_config), so taobao_config runtime edits take effect live.
    Behavioural invariants (single-tab reuse, network-interception-first, human
    pacing/click, rate cap, daily fav quota) are documented in config.toml comments.
    """
    captcha_timeout_s: int = 300   # bounded wait for the human to clear a captcha; then CaptchaError
    captcha_poll_s: float = 3.0    # captcha polling interval
    login_timeout_s: int = 180     # QR-login wait for the human scan
    search_cooldown_s: float = 150  # min interval between successive taobao_search calls (global,跨调用) — 直接URL连搜是滑块首因
    track_cache: bool = True       # once-per-day track/inventory cache (zero same-day traffic)
    fav_flow: bool = True          # master switch for the 收藏链路 (miid fine-detail)
    miid_channel: str = "auto"     # miid 获取渠道: auto=足迹→收藏 双机制 | footmark 仅足迹 | favorite 仅收藏 | config 静态
    review_sample_per_rating: int = 3  # reviews: take N good/neutral/bad each (anti good-only injection)


@dataclass(frozen=True)
class CompareCfg:
    """比价口径(2026-08-19): taobao_compare 默认用什么价来比.

    source = ask | cart | cart_atomic | coarse
      ask        (默认): 每次调用提示 LLM 询问用户用哪种口径, 不自动决定。
      cart       : 购物车到手价优先(购物车没有的商品退回粗查原价)。
      cart_atomic: 购物车没有的商品 → 原子加购指定型号→读到手价→退回(加多少退多少)。
      coarse     : 纯粗查原价。
    每次 taobao_compare 返回头部都会按此提示当前口径(配置为 cart/cart_atomic 时
    明示"即将使用购物车到手价"), 让用户感知。运行时用 taobao_config set compare.source。
    """
    source: str = "ask"


@dataclass(frozen=True)
class Config:
    browser: BrowserCfg
    pacing: PacingCfg
    click: ClickCfg
    limits: LimitsCfg
    output: OutputCfg
    detail: DetailCfg
    anti_risk: AntiRiskCfg
    compare: CompareCfg


_SECTIONS: tuple[tuple[str, type], ...] = (
    ("browser", BrowserCfg),
    ("pacing", PacingCfg),
    ("click", ClickCfg),
    ("limits", LimitsCfg),
    ("output", OutputCfg),
    ("detail", DetailCfg),
    ("anti_risk", AntiRiskCfg),
    ("compare", CompareCfg),
)

# ---------------------------------------------------------------------------
# Hard-safety bounds (config.toml / config.local.toml / taobao_config overrides).
# The project is fail-closed: a typo or hostile file edit must NOT be able to
# disable an anti-risk gate or balloon daily Taobao traffic.
#
# _BOUNDED_KEYS: numeric keys whose value is CLAMPED on load (file edits) and
# REJECTED on taobao_config set. Ceilings are deliberately LOW — rates are kept
# far under anything that could flag the account (§7.2, §8). NOTE: the review
# sampler lives under [anti_risk] — review_sample_per_rating, NOT limits.
# ---------------------------------------------------------------------------
_BOUNDED_KEYS: dict[str, tuple[float, float]] = {
    "pacing.max_products_per_minute": (1, 6),   # documented safe ceiling is 6/min
    "limits.max_reviews": (1, 200),
    "limits.review_pages": (1, 20),
    "limits.fav_flow_per_day": (1, 100),
    "limits.search_per_day": (1, 100),
    "anti_risk.review_sample_per_rating": (1, 20),
    "anti_risk.search_cooldown_s": (30, 3600),  # conservative min 30s between searches
    "anti_risk.captcha_timeout_s": (1, 3600),
    "anti_risk.login_timeout_s": (1, 3600),
    "anti_risk.captcha_poll_s": (0.5, 120),
}
# Delay/pause parameters: negative is nonsense (a negative wait would busy-loop
# the pacing layer). No upper bound — a larger delay is merely slower, never
# unsafe for the account.
_NONNEGATIVE_DELAYS = frozenset({
    "pacing.min_delay_s",
    "pacing.max_delay_s",
    "click.move_pause_min",
    "click.move_pause_max",
    "click.hover_pause_min",
    "click.hover_pause_max",
    "click.hold_min",
    "click.hold_max",
})


def _validate_runtime_override(section: str, name: str, value) -> str | None:
    """Return a human-facing rejection message if the override is unsafe, else None."""
    key = f"{section}.{name}"
    if key == "browser.headless" and value is True:
        return (
            "browser.headless 必须为 false: 本项目必须**有头**运行(人工观看窗口、"
            "人工解验证码, §7), 禁止运行时开启 headless。"
        )
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None  # non-numeric keys carry no numeric rule
    if key in _BOUNDED_KEYS:
        lo, hi = _BOUNDED_KEYS[key]
        if not lo <= num <= hi:
            return f"{key} 必须在 [{lo}, {hi}] 区间内(防风控硬安全边界), 不能为 {value}。"
    elif key in _NONNEGATIVE_DELAYS and num < 0:
        return f"{key} 是延迟参数, 不能为负(不能为 {value})。"
    return None


def _validate_merged(path, section: str, values: dict) -> None:
    """Fail closed: raise ValueError if any MERGED bounded key is out of bounds.

    Called during load_config on the merged config.toml + config.local.toml +
    overrides data, so a direct file edit cannot bypass the hard-safety bounds
    the way a taobao_config override is rejected. Non-dict section data is
    ignored (the same way unknown keys are ignored).
    """
    if not isinstance(values, dict):
        return
    prefix = section + "."
    for key, (lo, hi) in _BOUNDED_KEYS.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):]
        if name not in values or isinstance(values[name], bool):
            continue
        try:
            v = float(values[name])
        except (TypeError, ValueError):
            raise ValueError(
                f"配置 {key} 必须是数字, 得到 {values[name]!r}(源 {path})。"
            ) from None
        if not lo <= v <= hi:
            raise ValueError(
                f"配置 {key} 超出安全边界 [{lo}, {hi}]: 得到 {values[name]}(源 {path})。"
                "直接编辑配置文件不能绕过防风控上限, 请修正后重试。"
            )


def known_keys() -> list[str]:
    """All known config keys as 'section.key', for taobao_config key validation."""
    return [f"{sec}.{name}" for sec, cls in _SECTIONS for name in cls.__dataclass_fields__]


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
    changes, so a long-running server picks up runtime edits. Merge priority (low→high):
    config.toml < config.local.toml (gitignored, per-machine) < output/.config_overrides.toml
    (written by taobao_config set; gitignored, machine-local). Unknown keys are ignored.
    Hard-safety bounds (_BOUNDED_KEYS) are VALIDATED here on the merged values; an
    out-of-bounds value raises ValueError (fail closed), so a direct file edit
    cannot bypass the ceilings the way a taobao_config override is rejected."""
    p = Path(path)
    local_override = os.environ.get("TAOBAO_CONFIG_LOCAL", "").strip()
    local_p = Path(local_override).expanduser() if local_override else p.with_name("config.local.toml")
    mtime = p.stat().st_mtime if p.exists() else 0.0
    local_mtime = local_p.stat().st_mtime if local_p.exists() else 0.0
    miid_file = Path("output") / ".miid.json"
    miid_mtime = miid_file.stat().st_mtime if miid_file.exists() else 0.0

    # overrides file lives under output.dir (read from base config; default "./output")
    base_data: dict = {}
    if p.exists():
        with p.open("rb") as f:
            base_data = tomllib.load(f)
    out_dir = base_data.get("output", {}).get("dir", "./output") if isinstance(base_data.get("output"), dict) else "./output"
    ov_p = Path(out_dir) / ".config_overrides.toml"
    ov_mtime = ov_p.stat().st_mtime if ov_p.exists() else 0.0

    key = (str(p), mtime, str(local_p), local_mtime, miid_mtime, ov_mtime)
    if key in _CACHE:
        return _CACHE[key]

    data: dict = dict(base_data)

    if local_p.exists():
        with local_p.open("rb") as f:
            local_data = tomllib.load(f)
        for section, values in local_data.items():
            if isinstance(values, dict):
                data.setdefault(section, {}).update(values)

    if ov_p.exists():
        with ov_p.open("rb") as f:
            ov_data = tomllib.load(f)
        for section, values in ov_data.items():
            if isinstance(values, dict):
                data.setdefault(section, {}).update(values)

    # Fail closed on the MERGED values (base + local + overrides): an out-of-bounds
    # hard-safety key raises ValueError here, before any dataclass is built.
    for section, _ in _SECTIONS:
        _validate_merged(str(p), section, data.get(section, {}))

    def _filter(cls, section: str) -> dict:
        allowed = cls.__dataclass_fields__.keys()
        return {k: v for k, v in data.get(section, {}).items() if k in allowed}

    cfg = Config(
        browser=BrowserCfg(**_filter(BrowserCfg, "browser")),
        pacing=PacingCfg(**_filter(PacingCfg, "pacing")),
        click=ClickCfg(**_filter(ClickCfg, "click")),
        limits=LimitsCfg(**_filter(LimitsCfg, "limits")),
        output=OutputCfg(**_filter(OutputCfg, "output")),
        detail=DetailCfg(mi_id=_persisted_miid() or _filter(DetailCfg, "detail").get("mi_id", "")),
        anti_risk=AntiRiskCfg(**_filter(AntiRiskCfg, "anti_risk")),
        compare=CompareCfg(**_filter(CompareCfg, "compare")),
    )
    _CACHE.clear()      # keep only the latest base + local override pair
    _CACHE[key] = cfg
    return cfg


def _toml_literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v)  # str quoted


def safe_filename(name: str | None, default: str) -> str:
    """Containment: reduce any user-supplied filename to its basename so exports
    never escape the output dir (a '../x' must land inside output/, not above it)."""
    if not name:
        return default
    base = Path(name).name or default
    return base


def _write_toml(data: dict, path: Path) -> None:
    """Write a flat section/key TOML (our config has no nesting beyond [section])."""
    lines: list[str] = []
    for section, values in data.items():
        if isinstance(values, dict) and values:
            lines.append(f"[{section}]")
            for k, v in values.items():
                lines.append(f"{k} = {_toml_literal(v)}")
            lines.append("")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError as e:
        raise ValueError(f"无法写入配置覆盖文件 {path}: {e}") from e


def _coerce(raw: str, hint):
    if hint is bool:
        v = str(raw).strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
        # Unknown string must be REJECTED, not silently coerced to False — a typo
        # like "fales" must never disable a safety flag by accident.
        raise ValueError(f"invalid boolean value: {raw!r}")
    if hint is int:
        return int(str(raw).strip())
    if hint is float:
        return float(str(raw).strip())
    return str(raw)


def apply_override(key: str, value: str = "", confirm: bool = False) -> dict:
    """taobao_config set backend. Writes to gitignored output/.config_overrides.toml.

    confirm=False → validate + return a preview with the human reminder, NO write.
    confirm=True → write the override; load_config picks it up on next read.
    """
    import json as _json  # noqa: F401 (kept for future typed overrides)

    if "." not in key:
        return {"ok": False, "message": f"key 格式应为 section.key(如 pacing.min_delay_s)。已知键: {', '.join(known_keys())}"}
    section, name = key.split(".", 1)
    cls = dict(_SECTIONS).get(section)
    if cls is None or name not in cls.__dataclass_fields__:
        return {"ok": False, "message": f"未知配置键 {key}。已知键: {', '.join(known_keys())}"}

    # NOTE: `from __future__ import annotations` turns field .type into a string, so we must
    # resolve real types via typing.get_type_hints before coercing the raw value.
    try:
        import typing
        hint = typing.get_type_hints(cls).get(name, str)
    except Exception:
        hint = str
    try:
        coerced = _coerce(value, hint)
    except (TypeError, ValueError):
        return {"ok": False, "message": f"值 '{value}' 无法转换为 {getattr(hint, '__name__', hint)}"}

    bad = _validate_runtime_override(section, name, coerced)
    if bad:
        return {"ok": False, "message": bad}

    from src.config import load_config as _lc  # fresh, to keep cache key current
    _ = _lc()

    ov_p = Path(load_config().output.dir) / ".config_overrides.toml"
    data: dict = {}
    if ov_p.exists():
        import tomllib as _tl
        with ov_p.open("rb") as f:
            data = _tl.load(f)
    data.setdefault(section, {})[name] = coerced

    reminder = ("⚠️ 防风控参数直接影响账号安全。请人工在场并再次确认后才应设置 confirm=true 使生效; "
                "生效后 load_config 会在下次读取时自动应用(mtime 检测)。")
    if not confirm:
        return {"ok": False, "message": f"预览(未写入): {key} → {value}。\n{reminder}\n如确认请以 confirm=true 再次调用。"}
    try:
        _write_toml(data, ov_p)
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": f"已生效: {key} → {coerced}(写入 {ov_p}, gitignored)。\n{reminder}"}
