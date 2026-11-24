import json

import pytest

from sticky_todo.settings import PRESETS, SCHEMA, Settings, validate


def test_defaults():
    s = Settings()
    assert s["check_style"] == "square"
    assert s["font_family"] == "맑은 고딕"
    assert s["font_size"] == 12
    assert s.invalid_keys == []


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("font_size", 15, 15),
        ("font_size", 9, 12),
        ("font_size", 21, 12),
        ("font_size", True, 12),  # bool은 int로 취급하지 않음
        ("font_size", "14", 12),
        ("check_style", "circle", "circle"),
        ("check_style", "neon", "square"),
        ("bg_color", "#abcdef", "#ABCDEF"),
        ("bg_color", "red", SCHEMA["bg_color"].default),
        ("bg_color", "#abc", SCHEMA["bg_color"].default),
        ("shadow", 0, True),
        ("shadow", False, False),
        ("window_x", None, None),
        ("window_x", -1920, -1920),  # 왼쪽 보조 모니터
        ("window_x", 10**9, None),
        ("title", "  업무  ", "업무"),
        ("title", "   ", "오늘 할 일"),
        ("title", "가" * 51, "오늘 할 일"),
        ("done_style", "hide", "hide"),
        ("line_spacing", "huge", "normal"),
    ],
)
def test_validate(key, value, expected):
    assert validate(key, value) == expected


def test_load_partial_invalid(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"font_size": 99, "check_style": "circle", "unknown": 1}), encoding="utf-8"
    )
    s = Settings.load(p)
    assert s["font_size"] == 12
    assert s["check_style"] == "circle"
    assert s.invalid_keys == ["font_size"]
    assert "unknown" not in s.as_dict()


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", "null"])
def test_load_corrupt_gives_defaults(tmp_path, content):
    p = tmp_path / "settings.json"
    p.write_text(content, encoding="utf-8")
    s = Settings.load(p)
    assert s.as_dict() == Settings().as_dict()
    assert s.invalid_keys == ["*"]


def test_load_missing(tmp_path):
    s = Settings.load(tmp_path / "nope.json")
    assert s.invalid_keys == []


def test_save_roundtrip(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    s = Settings()
    s.set("font_size", 18)
    s.apply_preset("mint")
    s.save(p)
    s2 = Settings.load(p)
    assert s2.as_dict() == s.as_dict()
    assert s2["bg_color"] == PRESETS["mint"]["bg_color"].upper()


def test_set_returns_validated():
    s = Settings()
    assert s.set("corner_radius", 100) == 10
    with pytest.raises(KeyError):
        s.set("nope", 1)
