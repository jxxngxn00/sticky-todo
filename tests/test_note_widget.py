import json

import pytest
from conftest import tree
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from sticky_todo.note_widget import NoteWidget, compute_drop
from sticky_todo.settings import PRESETS, Settings

# ---------------------------------------------------------------- 드롭 위치 계산 (순수 함수)

E = [("A", 0), ("A1", 1), ("A2", 1), ("B", 0), ("B1", 1)]


@pytest.mark.parametrize(
    ("dragged", "index", "desired", "expected"),
    [
        ("B", 0, 0, ("A", 0)),  # 맨 앞으로
        ("B", 0, 5, ("A", 0)),  # 맨 앞은 0만 가능
        ("A", 5, 0, (None, 0)),  # 맨 뒤로
        ("A", 5, 3, (None, 2)),  # 맨 뒤, B1 밑으로는 최대 2
        ("B", 1, 0, ("A1", 1)),  # A와 A1 사이 → A1을 뺏지 않게 최소 1
        ("B", 1, 2, ("A1", 1)),  # 최대는 A+1
        ("B", 3, 0, ("B", 0)),  # 바로 위 (제자리)
        ("B", 3, 2, ("B", 2)),  # 제자리에서 A2 밑으로
        ("B", 5, 1, ("B", 1)),  # 묶음 끝 = 제자리
        ("A1", 4, 1, ("B1", 1)),  # B와 B1 사이 → B의 하위로
    ],
)
def test_compute_drop(dragged, index, desired, expected):
    assert compute_drop(E, dragged, index, desired) == expected


# ---------------------------------------------------------------- 위젯


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def type_and_enter(widget, text):
    # QTest.keyClicks는 한글 글자를 키 이벤트로 바꾸다 죽어서 글자는 직접 넣는다
    widget.insert(text)
    QTest.keyClick(widget, Qt.Key.Key_Return)


def test_add_via_input_and_count(note, store):
    type_and_enter(note.input, "PR 리뷰")
    type_and_enter(note.input, "회의록")
    assert [t.text for t in store.todos()] == ["PR 리뷰", "회의록"]
    assert len(note.list_view.rows) == 2
    assert note.count_label.text() == "0/2"
    note.list_view.rows[0].check.click()
    assert store.todos()[0].done
    assert note.count_label.text() == "1/2"


def test_input_tab_sets_indent(note, store):
    type_and_enter(note.input, "부모")
    QTest.keyClick(note.input, Qt.Key.Key_Tab)
    assert note.input.level == 1
    type_and_enter(note.input, "자식")
    QTest.keyClick(note.input, Qt.Key.Key_Tab)
    type_and_enter(note.input, "손자")
    QTest.keyClick(note.input, Qt.Key.Key_Tab)
    QTest.keyClick(note.input, Qt.Key.Key_Tab)
    assert note.input.level == 3  # 손자 밑까지만
    QTest.keyClick(note.input, Qt.Key.Key_Backtab)
    QTest.keyClick(note.input, Qt.Key.Key_Backtab)
    QTest.keyClick(note.input, Qt.Key.Key_Backtab)
    type_and_enter(note.input, "형제")
    assert tree(store) == [("부모", 0), ("자식", 1), ("손자", 2), ("형제", 0)]
    assert note.list_view.rows[2].layout().contentsMargins().left() > (
        note.list_view.rows[1].layout().contentsMargins().left()
    )


def test_input_level_never_exceeds_after_delete(note, store):
    store.add("A")
    store.add("B", 1)
    note.input.set_level(2)
    store.delete([store.todos()[0].id])  # 다 지워짐
    assert note.input.level == 0


def test_keyboard_on_list(note, store):
    for t in ["A", "B", "C"]:
        store.add(t)
    lv = note.list_view
    lv.setFocus()
    QTest.keyClick(lv, Qt.Key.Key_Down)
    QTest.keyClick(lv, Qt.Key.Key_Down)
    assert lv.selected_id == store.todos()[1].id
    QTest.keyClick(lv, Qt.Key.Key_Tab)
    assert tree(store) == [("A", 0), ("B", 1), ("C", 0)]
    QTest.keyClick(lv, Qt.Key.Key_Backtab)
    assert tree(store) == [("A", 0), ("B", 0), ("C", 0)]
    QTest.keyClick(lv, Qt.Key.Key_Space)
    assert store.todos()[1].done
    QTest.keyClick(lv, Qt.Key.Key_Delete)
    assert [t.text for t in store.todos()] == ["A", "C"]
    assert note.toast.isVisible() and note.toast_undo.isVisible()
    note.toast_undo.click()
    assert [t.text for t in store.todos()] == ["A", "B", "C"]


def test_inline_edit_and_tab_keeps_editing(note, store):
    store.add("A")
    store.add("B")
    row = note.list_view.rows[1]
    QTest.mouseDClick(row.label, Qt.MouseButton.LeftButton)
    assert row.editing
    row.editor.selectAll()
    row.editor.insert("B 수정")
    QTest.keyClick(row.editor, Qt.Key.Key_Tab)
    assert tree(store) == [("A", 0), ("B 수정", 1)]
    new_row = note.list_view.rows[1]
    assert new_row.editing  # Tab 후에도 계속 편집 중
    QTest.keyClick(new_row.editor, Qt.Key.Key_Escape)
    assert not new_row.editing


def test_edit_escape_cancels(note, store):
    store.add("원래")
    row = note.list_view.rows[0]
    row.begin_edit()
    row.editor.setText("바뀐")
    QTest.keyClick(row.editor, Qt.Key.Key_Escape)
    assert store.todos()[0].text == "원래"


def test_row_delete_button_deletes_children(note, store):
    store.add("P")
    store.add("C", 1)
    note.list_view.rows[0].delete_btn.click()
    assert len(store) == 0
    assert note.toast_label.text() == "2개 삭제됨"


def test_copy_markdown_keeps_indent(note, store):
    store.add("부모")
    store.add("자식", 1)
    note.copy_markdown()
    text = QGuiApplication.clipboard().text()
    assert "- [ ] 부모\n  - [ ] 자식\n" in text
    assert text.startswith("# 오늘 할 일")


def test_rename_and_preset_saved(note, tmp_path):
    note._rename("업무")
    note.apply_preset("mint")
    note.save_settings()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["title"] == "업무"
    assert saved["bg_color"] == PRESETS["mint"]["bg_color"].upper()
    assert note.title_bar.label.text() == "업무"


def test_geometry_remembered(note):
    note.resize(300, 400)
    note.move(50, 60)
    s = note.settings
    assert (s["window_w"], s["window_h"]) == (300, 400)


def test_clear_completed_toast(note, store):
    a = store.add("A")
    store.set_done(a.id, True)
    note.clear_completed()
    assert len(store) == 0
    assert "완료 1개" in note.toast_label.text()


def test_menu_builds(note):
    menu = note.build_menu()
    labels = [a.text() for a in menu.actions() if a.text()]
    assert "마크다운으로 저장…" in labels and "종료" in labels


def test_window_can_shrink(note):
    note.resize(200, 150)
    assert (note.width(), note.height()) == (200, 150)
