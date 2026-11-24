"""저장 경로 결정과 포터블 모드 판별."""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "StickyTodo"
PORTABLE_MARKER = "portable"


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    portable: bool

    @property
    def todos_file(self) -> Path:
        return self.data_dir / "todos.json"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def themes_dir(self) -> Path:
        return self.data_dir / "themes"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)


def app_dir() -> Path:
    """exe(빌드본) 또는 프로젝트 루트(개발 실행) 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # src/sticky_todo/paths.py -> 프로젝트 루트
    return Path(__file__).resolve().parents[2]


def is_portable(base_dir: Path) -> bool:
    return (base_dir / PORTABLE_MARKER).is_file()


def resolve_paths(base_dir: Path | None = None, appdata: str | None = None) -> AppPaths:
    """포터블 표식이 있으면 base_dir\\data, 없으면 %APPDATA%\\StickyTodo."""
    base = base_dir if base_dir is not None else app_dir()
    if is_portable(base):
        return AppPaths(data_dir=base / "data", portable=True)
    root = appdata if appdata is not None else os.environ.get("APPDATA")
    if not root:
        root = str(Path.home() / "AppData" / "Roaming")
    return AppPaths(data_dir=Path(root) / APP_NAME, portable=False)


# FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
_FOLDERID_DOCUMENTS = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")


def documents_dir() -> Path:
    """사용자 '문서' 폴더. OneDrive 등으로 옮겨진 경우도 따라간다."""
    if sys.platform == "win32":
        try:
            guid = (ctypes.c_byte * 16).from_buffer_copy(_FOLDERID_DOCUMENTS.bytes_le)
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                try:
                    if out.value:
                        return Path(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents"


def default_export_dir() -> Path:
    return documents_dir() / APP_NAME
