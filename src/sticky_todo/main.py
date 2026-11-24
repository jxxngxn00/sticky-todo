"""진입점: 데이터·설정을 읽고 노트 위젯을 띄운다."""

from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile, QTimer, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMessageBox

from .note_widget import NoteWidget
from .paths import resolve_paths
from .settings import Settings
from .storage import LoadStatus, TodoStore, UnsupportedVersionError

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
        QMessageBox.information(None, "Sticky Todo", "Sticky Todo가 이미 실행 중이에요.")
        return 0

    settings = Settings.load(paths.settings_file)
    store = TodoStore(paths.todos_file, paths.backups_dir, parent=app)
    try:
        result = store.load()
    except UnsupportedVersionError as e:
        QMessageBox.critical(None, "Sticky Todo", str(e))
        return 1

    note = NoteWidget(store, settings, paths.settings_file)
    app.aboutToQuit.connect(store.flush)
    app.aboutToQuit.connect(note.save_settings)
    note.show()

    if result.status in (LoadStatus.RECOVERED, LoadStatus.RESET):
        QTimer.singleShot(0, lambda: QMessageBox.warning(note, "Sticky Todo", result.message))

    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
