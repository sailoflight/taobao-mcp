"""Config coverage: anti_risk params, override file, taobao_config get/set backend."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import _BOUNDED_KEYS, _coerce, _toml_literal, apply_override, known_keys, load_config

OV = Path("output") / ".config_overrides.toml"


@pytest.fixture(autouse=True)
def _clean_override():
    if OV.exists():
        OV.unlink()
    yield
    if OV.exists():
        OV.unlink()


def test_known_keys_include_anti_risk():
    keys = known_keys()
    assert "anti_risk.captcha_timeout_s" in keys
    assert "anti_risk.track_cache" in keys
    assert "anti_risk.fav_flow" in keys
    assert "anti_risk.review_sample_per_rating" in keys
    assert "pacing.min_delay_s" in keys
    assert "limits.fav_flow_per_day" in keys
    assert "detail.mi_id" in keys


def test_unknown_key_rejected():
    r = apply_override("nope.key", "1")
    assert r["ok"] is False
    assert "未知配置键" in r["message"]


def test_bad_key_format_rejected():
    r = apply_override("no_dot_here", "1")
    assert r["ok"] is False


def test_preview_does_not_write():
    r = apply_override("anti_risk.track_cache", "false")
    assert r["ok"] is False
    assert "confirm=true" in r["message"]
    assert not OV.exists()


def test_confirm_write_and_roundtrip():
    r = apply_override("anti_risk.track_cache", "false", confirm=True)
    assert r["ok"] is True
    assert OV.exists()
    assert load_config().anti_risk.track_cache is False


def test_confirm_bool_and_int():
    assert apply_override("anti_risk.fav_flow", "true", confirm=True)["ok"] is True
    assert load_config().anti_risk.fav_flow is True
    assert apply_override("anti_risk.captcha_timeout_s", "600", confirm=True)["ok"] is True
    assert load_config().anti_risk.captcha_timeout_s == 600


def test_bad_value_type_rejected():
    r = apply_override("anti_risk.captcha_timeout_s", "not-a-number", confirm=True)
    assert r["ok"] is False


def test_coerce_types():
    assert _coerce("true", bool) is True
    assert _coerce("0", bool) is False
    assert _coerce("42", int) == 42
    assert _coerce("1.5", float) == 1.5
    assert _coerce("x", str) == "x"


def test_coerce_bool_rejects_unknown_strings():
    """Typo'd bool must be REJECTED, never silently coerced to False."""
    with pytest.raises(ValueError):
        _coerce("ture", bool)
    with pytest.raises(ValueError):
        _coerce("truthy", bool)
    with pytest.raises(ValueError):
        _coerce("", bool)
    assert _coerce("false", bool) is False
    assert _coerce("off", bool) is False
    assert _coerce("no", bool) is False
    assert _coerce("1", bool) is True


def test_override_rejects_headless_true():
    r = apply_override("browser.headless", "true", confirm=True)
    assert r["ok"] is False
    assert "headless" in r["message"]
    assert not OV.exists()  # nothing written


def test_override_allows_headless_false():
    r = apply_override("browser.headless", "false", confirm=True)
    assert r["ok"] is True
    assert load_config().browser.headless is False


def test_override_rejects_non_positive_rate_caps():
    caps = (
        "pacing.max_products_per_minute",
        "limits.max_reviews",
        "limits.review_pages",
        "limits.fav_flow_per_day",
        "limits.search_per_day",
        "anti_risk.review_sample_per_rating",
    )
    for key in caps:
        for bad in ("0", "-1"):
            r = apply_override(key, bad, confirm=True)
            assert r["ok"] is False, (key, bad)
            assert not OV.exists(), (key, bad)
    assert apply_override("limits.search_per_day", "5", confirm=True)["ok"] is True


def test_override_rejects_negative_delays():
    delays = (
        "pacing.min_delay_s",
        "pacing.max_delay_s",
        "click.move_pause_min",
        "click.move_pause_max",
        "click.hover_pause_min",
        "click.hover_pause_max",
        "click.hold_min",
        "click.hold_max",
        "anti_risk.captcha_poll_s",
        "anti_risk.search_cooldown_s",
    )
    for key in delays:
        r = apply_override(key, "-1", confirm=True)
        assert r["ok"] is False, key
        assert not OV.exists(), key
    # zero delay is legal (e.g. test speedup); only negatives are rejected
    assert apply_override("pacing.min_delay_s", "0", confirm=True)["ok"] is True


def test_override_rejects_invalid_timeouts_polls():
    for key in ("anti_risk.captcha_timeout_s", "anti_risk.login_timeout_s",
                "anti_risk.captcha_poll_s"):
        for bad in ("0", "-10"):
            r = apply_override(key, bad, confirm=True)
            assert r["ok"] is False, (key, bad)
            assert not OV.exists(), (key, bad)
    assert apply_override("anti_risk.captcha_timeout_s", "300", confirm=True)["ok"] is True


def test_review_sample_rating_key_lives_under_anti_risk():
    """Regression: the review sampler is an anti_risk key, NOT a limits key."""
    assert "anti_risk.review_sample_per_rating" in _BOUNDED_KEYS
    assert "limits.review_sample_per_rating" not in _BOUNDED_KEYS
    assert "anti_risk.review_sample_per_rating" in known_keys()
    assert "limits.review_sample_per_rating" not in known_keys()


def test_override_rejects_above_safe_ceiling():
    # documented safe ceiling for max_products_per_minute is 6 (§7.2 / config.toml)
    for bad in ("7", "100"):
        r = apply_override("pacing.max_products_per_minute", bad, confirm=True)
        assert r["ok"] is False, bad
        assert not OV.exists(), bad
    assert apply_override("pacing.max_products_per_minute", "6", confirm=True)["ok"] is True
    assert apply_override("pacing.max_products_per_minute", "1", confirm=True)["ok"] is True


def test_override_rejects_zero_short_search_cooldown():
    # conservative minimum 30s — 0 or a too-short cooldown must be rejected
    for bad in ("0", "5", "29"):
        r = apply_override("anti_risk.search_cooldown_s", bad, confirm=True)
        assert r["ok"] is False, bad
        assert not OV.exists(), bad
    assert apply_override("anti_risk.search_cooldown_s", "30", confirm=True)["ok"] is True
    assert apply_override("anti_risk.search_cooldown_s", "150", confirm=True)["ok"] is True


def test_override_rejects_above_upper_bounds():
    cases = {
        "limits.search_per_day": "101",
        "limits.fav_flow_per_day": "101",
        "limits.max_reviews": "201",
        "limits.review_pages": "21",
        "anti_risk.review_sample_per_rating": "21",
        "anti_risk.captcha_timeout_s": "3601",
        "anti_risk.captcha_poll_s": "0.4",
    }
    for key, bad in cases.items():
        r = apply_override(key, bad, confirm=True)
        assert r["ok"] is False, (key, bad)
        assert not OV.exists(), (key, bad)
    # boundary values are accepted
    assert apply_override("limits.search_per_day", "100", confirm=True)["ok"] is True
    assert apply_override("anti_risk.review_sample_per_rating", "20", confirm=True)["ok"] is True
    assert apply_override("anti_risk.captcha_poll_s", "0.5", confirm=True)["ok"] is True


def test_load_config_rejects_out_of_bounds(tmp_path):
    """Direct config.toml edits fail closed (ValueError), never bypass the ceilings."""
    p = tmp_path / "config.toml"
    p.write_text(
        "[output]\n"
        f'dir = "{tmp_path}"\n'
        "[pacing]\nmax_products_per_minute = 50\n"
        "[limits]\nmax_reviews = 999\n"
        "[anti_risk]\nsearch_cooldown_s = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_products_per_minute"):
        load_config(str(p))
    p.write_text(
        "[output]\n"
        f'dir = "{tmp_path}"\n'
        "[pacing]\nmax_products_per_minute = 3\n"
        "[limits]\nmax_reviews = 999\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_reviews"):
        load_config(str(p))
    p.write_text(
        "[output]\n"
        f'dir = "{tmp_path}"\n'
        "[pacing]\nmax_products_per_minute = 3\n"
        "[anti_risk]\nsearch_cooldown_s = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="search_cooldown_s"):
        load_config(str(p))


def test_load_config_rejects_local_and_override_sources(tmp_path):
    """config.local.toml AND a hand-edited overrides file also fail closed."""
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text("[output]\n" f'dir = "{tmp_path}"\n'
                    "[pacing]\nmax_products_per_minute = 3\n", encoding="utf-8")
    local.write_text("[pacing]\nmax_products_per_minute = 50\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_products_per_minute"):
        load_config(base)

    # clean local; a hand-edited overrides file (under output.dir) is caught too
    local.write_text("", encoding="utf-8")
    (tmp_path / ".config_overrides.toml").write_text(
        "[pacing]\nmax_products_per_minute = 50\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_products_per_minute"):
        load_config(base)


def test_load_config_accepts_in_bounds_values(tmp_path):
    """Exact boundary values (and in-bounds values) load without raising."""
    p = tmp_path / "config.toml"
    p.write_text(
        "[output]\n"
        f'dir = "{tmp_path}"\n'
        "[pacing]\nmax_products_per_minute = 6\n"
        "[limits]\nmax_reviews = 200\nreview_pages = 20\nsearch_per_day = 100\n"
        "[anti_risk]\nsearch_cooldown_s = 30\nreview_sample_per_rating = 20\n"
        "captcha_timeout_s = 3600\ncaptcha_poll_s = 0.5\n",
        encoding="utf-8",
    )
    cfg = load_config(str(p))
    assert cfg.pacing.max_products_per_minute == 6
    assert cfg.limits.max_reviews == 200
    assert cfg.anti_risk.search_cooldown_s == 30
    assert cfg.anti_risk.review_sample_per_rating == 20
    assert cfg.anti_risk.captcha_poll_s == 0.5


def test_toml_literal():
    assert _toml_literal(True) == "true"
    assert _toml_literal(False) == "false"
    assert _toml_literal(42) == "42"
    assert _toml_literal(1.5) == "1.5"
    assert _toml_literal("a b") == '"a b"'
