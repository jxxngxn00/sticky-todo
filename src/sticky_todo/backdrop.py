"""윈도우 글래스(Acrylic 블러) 효과.

Windows 11에서 user32의 SetWindowCompositionAttribute로 창 뒤를 흐리게 한다.
- 원래 계획은 공식 API인 DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)였지만,
  실측해보니 테두리 없는 Qt 창에서는 활성 상태에서도 단색으로만 그려져서 바꿨다.
- SetWindowCompositionAttribute는 문서화되지 않은 윈도우 내장 함수다.
  (네트워크·과금과 무관) 윈도우 업데이트로 동작이 바뀔 수 있으므로 실패하면 False를
  돌려주고, 호출한 쪽은 블러 없는 반투명으로 대체한다.
- 어떤 경우에도 예외를 밖으로 던지지 않는다.
"""

from __future__ import annotations

import ctypes
import sys

MIN_BUILD = 22000  # Windows 11 (윈도우 10은 창을 끌 때 버벅임이 있어 반투명으로 대체)

# SetWindowCompositionAttribute
WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

# DwmSetWindowAttribute (모서리 모양만 사용)
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
CORNERS = {"none": DWMWCP_DONOTROUND, "round": 2, "small": 3}
CORNER_RADIUS_PX = {"none": 0, "small": 4, "round": 8}  # 윈도우가 그리는 대략의 반지름


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),  # AABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class _CompositionData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    return sys.getwindowsversion().build


def acrylic_supported(build: int | None = None) -> bool:
    return (windows_build() if build is None else build) >= MIN_BUILD


def _user32():
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _dwm():
    return ctypes.windll.dwmapi  # type: ignore[attr-defined]


def _set_accent(hwnd: int, state: int, gradient: int = 0) -> bool:
    accent = _AccentPolicy(state, 2 if state else 0, gradient, 0)
    data = _CompositionData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.byref(accent), ctypes.c_void_p),
        ctypes.sizeof(accent),
    )
    fn = _user32().SetWindowCompositionAttribute
    return bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))


def _set_corner(hwnd: int, corner: str) -> None:
    v = ctypes.c_int(CORNERS.get(corner, 2))
    _dwm().DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(v), ctypes.sizeof(v)
    )


def enable_glass(hwnd: int, *, corner: str = "round") -> bool:
    """창 뒤 블러를 켠다. 성공하면 True.

    색조는 노트가 그 위에 반투명하게 직접 칠한다 (블러를 못 켤 때와 같은 방식).
    """
    if not hwnd or not acrylic_supported():
        return False
    try:
        if not _set_accent(hwnd, ACCENT_ENABLE_ACRYLICBLURBEHIND, 0x01FFFFFF):
            return False
        _set_corner(hwnd, corner)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def disable_glass(hwnd: int) -> None:
    """블러를 끄고 스티키 노트 모드(직접 그리는 모서리·그림자)로 돌린다."""
    if not hwnd or sys.platform != "win32":
        return
    try:
        _set_accent(hwnd, ACCENT_DISABLED)
        _set_corner(hwnd, "none")
    except (OSError, AttributeError, ValueError):
        pass
