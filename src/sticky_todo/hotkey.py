"""어디서든 노트를 불러오는 전역 단축키 (Ctrl+Alt+T).

윈도우 내장 RegisterHotKey를 쓴다. 등록된 키 조합은 윈도우가 이 앱으로만 보내고,
다른 앱이 이미 쓰고 있으면 등록이 실패한다 (그때는 False를 돌려준다).

단축키는 화면에 보이지 않는 전용 창에 묶는다. 창 없이 등록하면 메시지가 스레드 큐로 와서
Qt가 전달해주지 않고, 노트 창에 묶으면 '항상 위' 전환 때 창이 다시 만들어져 끊긴다.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtWidgets import QWidget

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_T = 0x54
HOTKEY_ID = 0x5354  # 앱 안에서만 쓰는 번호
HOTKEY_TEXT = "Ctrl+Alt+T"


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def is_hotkey_message(event_type, message) -> bool:
    # message는 shiboken VoidPtr라 `not message`로 검사하면 항상 비어 보인다 → 정수 주소로 확인
    address = int(message) if message is not None else 0
    if bytes(event_type) != b"windows_generic_MSG" or address == 0:
        return False
    msg = _MSG.from_address(address)
    return msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID


class _Receiver(QWidget):
    """보이지 않는 창. WM_HOTKEY를 받는다."""

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback

    def nativeEvent(self, event_type, message):  # noqa: N802
        if is_hotkey_message(event_type, message):
            self.callback()
            return True, 0
        return super().nativeEvent(event_type, message)


class GlobalHotkey:
    def __init__(self, callback: Callable[[], None]) -> None:
        self._receiver = _Receiver(callback)
        self.registered = False

    def register(self) -> bool:
        if self.registered:
            return True
        if sys.platform != "win32":
            return False
        hwnd = int(self._receiver.winId())  # 보이지 않는 네이티브 창을 만든다 (show 안 함)
        self.registered = bool(
            ctypes.windll.user32.RegisterHotKey(  # type: ignore[attr-defined]
                ctypes.c_void_p(hwnd), HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_T
            )
        )
        return self.registered

    def unregister(self) -> None:
        if not self.registered:
            return
        hwnd = int(self._receiver.winId())
        ctypes.windll.user32.UnregisterHotKey(ctypes.c_void_p(hwnd), HOTKEY_ID)  # type: ignore[attr-defined]
        self.registered = False
