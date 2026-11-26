"""진입점: 데이터·설정을 읽고 노트 위젯을 띄운다."""

from __future__ import annotations

import contextlib
import sys

from PySide6.QtCore import QLockFile, QTimer, qInstallMessageHandler
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import icons, resources
from .autostart import Autostart
from .hotkey import GlobalHotkey
from .note_widget import NoteWidget, resolve_font_family
from .paths import resolve_paths
from .settings import Settings
from .storage import LoadStatus, TodoStore, UnsupportedVersionError
from .tray import Tray
from .updater import UpdateChecker

_previous_handler = None


def _qt_message_filter(mode, context, message: str) -> None:
    # 투명 배경 창에서 Qt가 가끔 내는 무해한 경고. 화면·데이터에 영향 없음.
    if message.startswith("UpdateLayeredWindowIndirect failed"):
        return
    if _previous_handler is not None:
        _previous_handler(mode, context, message)
    else:
        print(message, file=sys.stderr)


def main() -> int:
    global _previous_handler
    _previous_handler = qInstallMessageHandler(_qt_message_filter)
    app = QApplication(sys.argv)
    app.setApplicationName("Sticky Todo")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icons.app_icon())
    resources.load_bundled_fonts()  # Pretendard (설치 안 된 PC에서도)
    resources.install_korean_translation(app)  # Qt 기본 창 한국어

    paths = resolve_paths()
    try:
        paths.ensure_dirs()
    except OSError as e:
        QMessageBox.critical(None, "Sticky Todo", f"데이터 폴더를 만들 수 없어요.\n{e}")
        return 1

    # 두 개가 동시에 떠서 같은 파일을 덮어쓰는 걸 막는다
    lock = QLockFile(str(paths.data_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None,
            "Sticky Todo",
            "Sticky Todo가 이미 실행 중이에요.\n"
            "노트가 안 보이면 작업표시줄 오른쪽 트레이 아이콘을 눌러 주세요.",
        )
        return 0

    settings = Settings.load(paths.settings_file)
    app.setFont(QFont(resolve_font_family(settings["font_family"]), 10))
    store = TodoStore(paths.todos_file, paths.backups_dir, parent=app)
    try:
        result = store.load()
    except UnsupportedVersionError as e:
        QMessageBox.critical(None, "Sticky Todo", str(e))
        return 1

    autostart = Autostart()
    with contextlib.suppress(OSError):
        autostart.refresh()  # exe를 다른 폴더로 옮겼으면 자동 실행 경로도 고친다

    note = NoteWidget(store, settings, paths.settings_file, autostart=autostart)
    note.hotkey = GlobalHotkey(note.summon)
    app.aboutToQuit.connect(note.hotkey.unregister)
    app.aboutToQuit.connect(store.flush)
    app.aboutToQuit.connect(note.save_settings)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(note)
        tray.show()
        note.hide_to_tray = True  # ✕는 트레이로 숨기기

    # 새 버전 알림: 하루 한 번 GitHub에 최신 버전 번호만 물어본다 (설정에서 끌 수 있음)
    updater = UpdateChecker(settings, note.schedule_settings_save, parent=app)
    note.attach_updater(updater)
    if tray is not None:
        updater.found.connect(tray.notify_update)
    app.aboutToQuit.connect(updater.stop)
    updater.start()
    note.show()
    note.sync_hotkey()

    if result.status in (LoadStatus.RECOVERED, LoadStatus.RESET):
        QTimer.singleShot(0, lambda: QMessageBox.warning(note, "Sticky Todo", result.message))

    code = app.exec()
    if tray is not None:
        tray.hide()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
