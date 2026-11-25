import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 테스트는 화면 없이

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sticky_todo.storage import TodoStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """안내·경고 창(QMessageBox)이 테스트를 멈추지 않게, 띄우는 대신 기록만 한다."""
    from PySide6.QtWidgets import QMessageBox

    shown = []

    def record(kind, answer):
        def _show(*args, **_kw):
            shown.append((kind, args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else ""))
            return answer

        return _show

    ok = QMessageBox.StandardButton.Ok
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, record(kind, ok))
    monkeypatch.setattr(QMessageBox, "question", record("question", QMessageBox.StandardButton.No))
    return shown


@pytest.fixture
def make_store(qapp, tmp_path):
    def _make(**kw):
        kw.setdefault("autosave", False)
        return TodoStore(tmp_path / "todos.json", tmp_path / "backups", **kw)

    return _make


@pytest.fixture
def store(make_store):
    return make_store()


def tree(store):
    """[(text, indent), ...] 로 현재 상태 확인용."""
    return [(t.text, t.indent) for t in store.todos()]


def local_iso(*args) -> str:
    """이 PC(또는 CI 서버) 시간대 기준 ISO 시각.

    날짜 경계를 다루는 테스트에 '+09:00'을 박으면 UTC인 CI 서버에서 날짜가 어긋난다.
    """
    from datetime import datetime

    return datetime(*args).astimezone().isoformat(timespec="seconds")
