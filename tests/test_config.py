"""Config coverage: anti_risk params, override file, taobao_config get/set backend."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import _coerce, _toml_literal, apply_override, known_keys, load_config

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


def test_toml_literal():
    assert _toml_literal(True) == "true"
    assert _toml_literal(False) == "false"
    assert _toml_literal(42) == "42"
    assert _toml_literal(1.5) == "1.5"
    assert _toml_literal("a b") == '"a b"'
