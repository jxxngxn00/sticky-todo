import pytest
from PySide6.QtCore import QCoreApplication

from sticky_todo.storage import TodoStore


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
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
