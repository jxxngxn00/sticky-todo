"""v0.2.0이 실제로 저장한 파일(tests/data/v0_2_0)을 지금 버전이 그대로 여는지.

데이터는 v0.2.0 태그의 코드로 만들었다 (할 일·하위 할 일·완료·보관·다른 달, 분홍 프리셋 설정).
"""

import shutil
from pathlib import Path

import pytest

from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.storage import LoadStatus, TodoStore

DATA = Path(__file__).parent / "data" / "v0_2_0"


@pytest.fixture
def old_files(tmp_path):
    for name in ("todos.json", "settings.json"):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path


def test_todos_from_v0_2_0(qapp, old_files):
    store = TodoStore(old_files / "todos.json", old_files / "backups", autosave=False)
    assert store.load().status == LoadStatus.OK
    store.set_day("2026-11-20")
    rows = [(t.text, t.done, t.indent) for t in store.todos()]
    assert rows == [
        ("보고서 초안 **보내기**", True, 0),
        ("자료 첨부", False, 1),
        ("운동 30분", False, 0),
    ]
    assert [t.text for t in store.archived()] == ["장보기"]
    store.set_day("2026-12-25")
    assert [t.text for t in store.todos()] == ["크리스마스 카드"]
    # 다시 저장해도 그대로 읽힌다
    assert store.flush()
    again = TodoStore(old_files / "todos.json", old_files / "backups", autosave=False)
    assert again.load().status == LoadStatus.OK
    assert len(again.all_todos()) == len(store.all_todos())


def test_settings_from_v0_2_0(qapp, old_files):
    s = Settings.load(old_files / "settings.json")
    assert s["accent_color"] == "#FF375F" and s["font_size"] == 14
    assert s["check_style"] == "square" and s["done_style"] == "dim"
    assert s["update_check"] is True  # 새로 생긴 설정은 기본값 (알림 켜짐)


def test_note_opens_with_v0_2_0_files(qapp, old_files):
    store = TodoStore(old_files / "todos.json", old_files / "backups", autosave=False)
    store.load()
    settings = Settings.load(old_files / "settings.json")
    w = NoteWidget(store, settings, old_files / "settings.json")
    w.show()
    try:
        assert w.theme.images == {}  # 테마 폴더가 없으면 기본 모양
        store.set_day("2026-11-20")
        qapp.processEvents()
        texts = [r.todo.text for r in w.list_view.rows]
        assert "보고서 초안 **보내기**" in texts
        for mode in ("week", "month", "day"):
            w.set_view_mode(mode)
            qapp.processEvents()
    finally:
        w._settings_timer.stop()
        w.hide()
        w.deleteLater()
