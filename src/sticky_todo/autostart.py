"""윈도우 시작 시 자동 실행 (HKCU Run 키).

- 관리자 권한이 필요 없는 현재 사용자 키만 쓴다.
- 사용자가 켤 때만 등록하고, 끄면 지운다.
- 실행 경로는 반드시 따옴표로 감싼다. (따옴표가 없으면 'C:\\Program Files\\…' 같은
  경로에서 윈도우가 'C:\\Program.exe'를 먼저 찾아 실행하는 취약점이 생긴다)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "StickyTodo"


class RunKey(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WinRegRunKey:
    """실제 레지스트리 (HKCU\\...\\Run)."""

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return value if isinstance(value, str) else None
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def launch_command(frozen: bool | None = None, executable: str | None = None) -> str:
    """윈도우가 실행할 명령. exe면 exe 자체, 개발 실행이면 콘솔 없는 pythonw로 모듈 실행."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = Path(executable or sys.executable)
    if frozen:
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m sticky_todo'


class Autostart:
    def __init__(self, key: RunKey | None = None, command: str | None = None) -> None:
        self.key = key or WinRegRunKey()
        self.command = command or launch_command()

    def is_enabled(self) -> bool:
        return self.key.get(VALUE_NAME) is not None

    def is_current(self) -> bool:
        """등록된 경로가 지금 실행 중인 프로그램과 같은지 (exe를 옮겼으면 False)."""
        return self.key.get(VALUE_NAME) == self.command

    def set_enabled(self, on: bool) -> None:
        if on:
            self.key.set(VALUE_NAME, self.command)
        else:
            self.key.delete(VALUE_NAME)

    def refresh(self) -> None:
        """켜져 있는데 경로가 바뀌었으면 (exe를 다른 폴더로 옮긴 경우) 새 경로로 고친다."""
        if self.is_enabled() and not self.is_current():
            self.set_enabled(True)
