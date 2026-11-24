"""설정 로드/저장과 값 검증. 잘못된 값은 기본값으로 바꾼다."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text

DEFAULT_FONT = "맑은 고딕"

# 스티키 노트 프리셋: (배경, 제목 바, 글자)
PRESETS: dict[str, dict[str, str]] = {
    "yellow": {"bg_color": "#FFF59D", "title_color": "#FFEB3B", "text_color": "#3E2723"},
    "pink": {"bg_color": "#F8BBD0", "title_color": "#F48FB1", "text_color": "#4A1C2C"},
    "sky": {"bg_color": "#B3E5FC", "title_color": "#81D4FA", "text_color": "#0D2B3E"},
    "mint": {"bg_color": "#C8F7DC", "title_color": "#9EEBC0", "text_color": "#123524"},
}
GLASS_TINTS: dict[str, str] = {
    "light": "#FFFFFF",
    "dark": "#1E1E1E",
    "sky": "#9FD3FF",
    "pink": "#FFB6CF",
}

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class Field:
    default: Any
    check: Callable[[Any], bool]
    normalize: Callable[[Any], Any] = lambda v: v


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_range(lo: int, hi: int) -> Callable[[Any], bool]:
    return lambda v: _is_int(v) and lo <= v <= hi


def _choice(*options: str) -> Callable[[Any], bool]:
    return lambda v: v in options


def _color(v: Any) -> bool:
    return isinstance(v, str) and bool(_COLOR.match(v))


def _text(max_len: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and 0 < len(v.strip()) <= max_len


def _opt_int(v: Any) -> bool:
    return v is None or (_is_int(v) and -100_000 <= v <= 100_000)


def _bool(v: Any) -> bool:
    return isinstance(v, bool)


_yellow = PRESETS["yellow"]

SCHEMA: dict[str, Field] = {
    # 외형
    "style": Field("sticky", _choice("sticky", "glass")),
    "font_family": Field(DEFAULT_FONT, _text(100), str.strip),
    "font_size": Field(12, _int_range(10, 20)),
    "bg_color": Field(_yellow["bg_color"], _color, str.upper),
    "title_color": Field(_yellow["title_color"], _color, str.upper),
    "text_color": Field(_yellow["text_color"], _color, str.upper),
    "glass_transparency": Field(40, _int_range(0, 100)),  # 0=색조 불투명, 100=유리만
    "glass_tint": Field(GLASS_TINTS["light"], _color, str.upper),
    "glass_corner": Field("round", _choice("none", "small", "round")),
    "corner_radius": Field(10, _int_range(0, 24)),
    "shadow": Field(True, _bool),
    "title_bar_mode": Field("always", _choice("always", "hover")),
    # 목록 동작
    "check_style": Field("square", _choice("square", "circle", "bullet")),
    "done_style": Field("strike", _choice("strike", "dim", "bottom", "hide")),
    "line_spacing": Field("normal", _choice("compact", "normal", "relaxed")),
    # 노트 창
    "title": Field("오늘 할 일", _text(50), str.strip),
    "always_on_top": Field(False, _bool),
    "window_x": Field(None, _opt_int),
    "window_y": Field(None, _opt_int),
    "window_w": Field(280, _int_range(160, 2000)),
    "window_h": Field(360, _int_range(120, 3000)),
}


def validate(key: str, value: Any) -> Any:
    """검증된 값을 돌려준다. 틀리면 기본값. 모르는 키면 KeyError."""
    f = SCHEMA[key]
    if f.check(value):
        return f.normalize(value)
    return f.default


class Settings:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = {k: f.default for k, f in SCHEMA.items()}
        self.invalid_keys: list[str] = []  # 로드할 때 기본값으로 바뀐 키
        for k, v in (values or {}).items():
            if k not in SCHEMA:
                continue
            if not SCHEMA[k].check(v):
                self.invalid_keys.append(k)
            self._values[k] = validate(k, v)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def get(self, key: str) -> Any:
        return self._values[key]

    def set(self, key: str, value: Any) -> Any:
        """검증 후 저장하고, 실제로 저장된 값을 돌려준다."""
        self._values[key] = validate(key, value)
        return self._values[key]

    def apply_preset(self, name: str) -> None:
        for k, v in PRESETS[name].items():
            self.set(k, v)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def restore(self, values: dict[str, Any]) -> None:
        """as_dict()로 받아둔 값으로 되돌린다 (설정 창 취소용)."""
        for k, v in values.items():
            if k in SCHEMA:
                self.set(k, v)

    def reset(self, keys: list[str]) -> None:
        for k in keys:
            self._values[k] = SCHEMA[k].default

    # ------------------------------------------------------------ 파일

    @classmethod
    def load(cls, path: Path) -> Settings:
        """파일이 없거나 깨졌으면 기본값. 일부 값만 틀리면 그 값만 기본값."""
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return cls()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            s = cls()
            s.invalid_keys = ["*"]
            return s
        if not isinstance(data, dict):
            s = cls()
            s.invalid_keys = ["*"]
            return s
        return cls(data)

    def save(self, path: Path) -> None:
        atomic_write_text(Path(path), json.dumps(self._values, ensure_ascii=False, indent=2))


# 설정 창의 "기본값으로"가 되돌리는 키 (창 위치·제목·고정 여부는 유지)
APPEARANCE_KEYS = [
    "style",
    "font_family",
    "font_size",
    "bg_color",
    "title_color",
    "text_color",
    "glass_transparency",
    "glass_tint",
    "glass_corner",
    "corner_radius",
    "shadow",
    "title_bar_mode",
]
LIST_KEYS = ["check_style", "done_style", "line_spacing"]


def luminance(color: str) -> float:
    """상대 휘도 (0=검정, 1=흰색). WCAG 공식."""
    c = color.lstrip("#")
    rgb = [int(c[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_text(background: str) -> str:
    """배경 위에서 잘 읽히는 글자색 (흰색 또는 거의 검정)."""
    lum = luminance(background)
    # 흰 글자와 검정 글자 중 대비(WCAG contrast ratio)가 더 큰 쪽
    white = 1.05 / (lum + 0.05)
    black = (lum + 0.05) / 0.05
    return "#FFFFFF" if white > black else "#1A1A1A"
