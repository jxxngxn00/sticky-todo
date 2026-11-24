"""윈도우 글래스(Acrylic) 효과.

Windows 11 22H2(빌드 22621) 이상에서 DwmSetWindowAttribute로 시스템 Acrylic을 켠다.
- 노트가 비활성(다른 창 사용 중)일 때는 윈도우가 단색으로 바꿔 그린다. (윈도우 동작)
- 그 외 환경이나 호출 실패 시 False를 돌려주고, 호출한 쪽은 블러 없는 반투명으로 대체한다.
- 어떤 경우에도 예외를 밖으로 던지지 않는다.
"""

from __future__ import annotations

import ctypes
import sys

MIN_BUILD = 22621

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMSBT_NONE = 1
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic

DWMWCP_DONOTROUND = 1
CORNERS = {"none": DWMWCP_DONOTROUND, "round": 2, "small": 3}
CORNER_RADIUS_PX = {"none": 0, "small": 4, "round": 8}  # 윈도우가 그리는 대략의 반지름


class _Margins(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("right", ctypes.c_int),
        ("top", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    return sys.getwindowsversion().build


def acrylic_supported(build: int | None = None) -> bool:
    return (windows_build() if build is None else build) >= MIN_BUILD


def _dwm():
    return ctypes.windll.dwmapi  # type: ignore[attr-defined]


def _set_int(hwnd: int, attr: int, value: int) -> int:
    v = ctypes.c_int(value)
    return _dwm().DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v)
    )


def _extend_frame(hwnd: int, amount: int) -> int:
    m = _Margins(amount, amount, amount, amount)
    return _dwm().DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(m))


def enable_glass(hwnd: int, *, dark: bool, corner: str = "round") -> bool:
    """Acrylic을 켠다. 성공하면 True."""
    if not hwnd or not acrylic_supported():
        return False
    try:
        if _extend_frame(hwnd, -1) != 0:
            return False
        _set_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, CORNERS.get(corner, 2))
        return _set_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW) == 0
    except (OSError, AttributeError, ValueError):
        return False


def disable_glass(hwnd: int) -> None:
    """Acrylic을 끄고 스티키 노트 모드(직접 그리는 모서리·그림자)로 돌린다."""
    if not hwnd or sys.platform != "win32":
        return
    try:
        _set_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
        _extend_frame(hwnd, 0)
        _set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)
    except (OSError, AttributeError, ValueError):
        pass
