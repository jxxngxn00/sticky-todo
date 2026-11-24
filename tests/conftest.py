import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 테스트는 화면 없이

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sticky_todo.storage import TodoStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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
