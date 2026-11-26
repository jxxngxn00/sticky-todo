"""설정 로드/저장과 값 검증. 잘못된 값은 기본값으로 바꾼다."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text

DEFAULT_FONT = "Pretendard"  # 없으면 맑은 고딕으로 대체 (note_widget.resolve_font_family)

# 노트 색 프리셋 (iOS 시스템 색 기반의 부드러운 톤)
PRESETS: dict[str, dict[str, str]] = {
    "yellow": {
        "bg_color": "#FFF8DC",
        "title_color": "#FFF8DC",
        "text_color": "#1C1C1E",
        "accent_color": "#FF9F0A",
    },
    "pink": {
        "bg_color": "#FFEDF2",
        "title_color": "#FFEDF2",
        "text_color": "#1C1C1E",
        "accent_color": "#FF375F",
    },
    "sky": {
        "bg_color": "#EAF4FF",
        "title_color": "#EAF4FF",
        "text_color": "#1C1C1E",
        "accent_color": "#0A84FF",
    },
    "mint": {
        "bg_color": "#E6F8EE",
        "title_color": "#E6F8EE",
        "text_color": "#1C1C1E",
        "accent_color": "#30B860",
    },
    # iOS 기본 앱 같은 무채색 회색
    "ios": {
        "bg_color": "#EBEBEB",
        "title_color": "#EBEBEB",
        "text_color": "#2F2F32",
        "accent_color": "#FF9F0A",
    },
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


def _short_str(max_len: int) -> Callable[[Any], bool]:
    """빈 문자열도 되는 짧은 글자."""
    return lambda v: isinstance(v, str) and len(v) <= max_len


_yellow = PRESETS["yellow"]

SCHEMA: dict[str, Field] = {
    # 외형
    "font_family": Field(DEFAULT_FONT, _text(100), str.strip),
    "font_size": Field(12, _int_range(10, 20)),
    "bg_color": Field(_yellow["bg_color"], _color, str.upper),
    "title_color": Field(_yellow["title_color"], _color, str.upper),
    "text_color": Field(_yellow["text_color"], _color, str.upper),
    "accent_color": Field(_yellow["accent_color"], _color, str.upper),  # 체크·추가 버튼
    "corner_radius": Field(14, _int_range(0, 24)),
    "shadow": Field(True, _bool),
    "title_bar_mode": Field("always", _choice("always", "hover")),
    # 목록 동작
    "check_style": Field("circle", _choice("square", "circle", "bullet")),
    "done_style": Field("strike", _choice("strike", "dim", "bottom", "hide")),
    "line_spacing": Field("normal", _choice("compact", "normal", "relaxed")),
    "auto_archive": Field(True, _bool),  # 날짜가 바뀌면 지난 날 완료한 항목 보관
    "global_hotkey": Field(True, _bool),  # Ctrl+Alt+T로 어디서든 노트 불러오기
    # 새 버전 알림 (하루 한 번 GitHub에 최신 버전 번호만 물어봄)
    "update_check": Field(True, _bool),
    "update_last_check": Field(0, _int_range(0, 10**11)),  # 마지막으로 확인한 시각 (초)
    "update_notified": Field("", _short_str(20)),  # 풍선 알림을 띄운 버전 (한 번만)
    # 처음 한 번만 보여주는 안내
    "tray_hint_seen": Field(False, _bool),
    "archive_hint_seen": Field(False, _bool),
    # 노트 창
    "title": Field("오늘 할 일", _text(50), str.strip),
    "always_on_top": Field(False, _bool),
    "window_x": Field(None, _opt_int),
    "window_y": Field(None, _opt_int),
    "window_w": Field(280, _int_range(160, 2000)),  # 하루 보기 크기
    "window_h": Field(360, _int_range(120, 3000)),
    "view_mode": Field("day", _choice("day", "week", "month")),
    "week_w": Field(320, _int_range(160, 4000)),  # 주간 보기 크기 (따로 기억)
    "week_h": Field(600, _int_range(120, 3000)),
    "month_w": Field(360, _int_range(160, 4000)),  # 월간 보기 크기
    "month_h": Field(700, _int_range(120, 3000)),
    "month_list": Field(True, _bool),  # 월간: 달력 아래 목록 보이기 (끄면 달력만)
    # 전체 할 일 관리 창
    "manager_x": Field(None, _opt_int),
    "manager_y": Field(None, _opt_int),
    "manager_w": Field(640, _int_range(480, 4000)),
    "manager_h": Field(480, _int_range(320, 3000)),
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
    "font_family",
    "font_size",
    "bg_color",
    "title_color",
    "text_color",
    "accent_color",
    "corner_radius",
    "shadow",
    "title_bar_mode",
]
LIST_KEYS = ["check_style", "done_style", "line_spacing", "auto_archive", "global_hotkey"]
