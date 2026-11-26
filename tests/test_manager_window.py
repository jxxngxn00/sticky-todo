from datetime import datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from sticky_todo.manager_window import (
    COL_COMPLETED,
    COL_CREATED,
    COL_DONE,
    COL_SELECT,
    COL_TEXT,
    PARENT_ROLE,
    ManagerWindow,
    format_time,
    parent_names,
)
from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.storage import SortKey, Status, Todo


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def win(store, settings):
    w = ManagerWindow(store, settings)
    w.show()
    yield w
    w.close()
    w.deleteLater()


def fill(store):
    """A, A1(하위), B(완료), C 순서로 만들고 시간을 고정한다."""
    a = store.add("A 기획")
    a1 = store.add("A1 하위 작업", 1)
    b = store.add("B 리뷰")
    c = store.add("C 회의")
    store.set_done(b.id, True)
    times = {
        a.id: ("2026-11-20T09:00:00+09:00", None),
        a1.id: ("2026-11-21T09:00:00+09:00", None),
        b.id: ("2026-11-22T09:00:00+09:00", "2026-11-23T10:00:00+09:00"),
        c.id: ("2026-11-23T09:00:00+09:00", None),
    }
    for t in store._todos:
        t.created_at, done_at = times[t.id]
        if done_at:
            t.completed_at = done_at
    store.changed.emit()
    return {"A": a.id, "A1": a1.id, "B": b.id, "C": c.id}


def texts(win):
    return [t.text for t in win.model.rows]


def cell(win, row, col, role=Qt.ItemDataRole.DisplayRole):
    return win.model.index(row, col).data(role)


# ---------------------------------------------------------------- 순수 함수


def test_format_time():
    now = datetime.fromisoformat("2026-11-24T12:00:00+09:00")
    assert format_time("2026-11-24T09:05:00+09:00", now) == "11-24 09:05"
    assert format_time("2025-03-01T09:05:00+09:00", now) == "2025-03-01"
    assert format_time(None, now) == ""


def test_parent_names():
    todos = [
        Todo(id=i, text=i, indent=d) for i, d in [("P", 0), ("C", 1), ("G", 2), ("C2", 1), ("Q", 0)]
    ]
    assert parent_names(todos) == {"C": "P", "G": "C", "C2": "P"}


# ---------------------------------------------------------------- 목록·검색·필터·정렬


def test_default_sort_and_columns(win, store):
    fill(store)
    assert texts(win) == ["C 회의", "B 리뷰", "A1 하위 작업", "A 기획"]  # 최근 추가순
    assert cell(win, 1, COL_DONE, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert cell(win, 1, COL_CREATED) == "11-22 09:00"
    assert cell(win, 1, COL_COMPLETED) == "11-23 10:00"
    assert cell(win, 0, COL_COMPLETED) == ""


def test_parent_hint_flat_list(win, store):
    fill(store)
    row = texts(win).index("A1 하위 작업")
    assert cell(win, row, COL_TEXT, PARENT_ROLE) == "A 기획"
    assert cell(win, texts(win).index("A 기획"), COL_TEXT, PARENT_ROLE) is None
    assert "상위 항목: A 기획" in cell(win, row, COL_TEXT, Qt.ItemDataRole.ToolTipRole)


def test_search_filter_and_counts(win, store):
    fill(store)
    win.search.setText("리뷰")
    assert texts(win) == ["B 리뷰"]
    win.search.setText("기획")  # 부모 이름으로는 하위 항목이 안 걸림
    assert texts(win) == ["A 기획"]
    win.search.clear()
    win.status_buttons[Status.DONE].click()
    assert texts(win) == ["B 리뷰"]
    win.status_buttons[Status.ACTIVE].click()
    assert "B 리뷰" not in texts(win) and len(texts(win)) == 3
    assert win.status_buttons[Status.ALL].text() == "전체 4"
    assert win.status_buttons[Status.ACTIVE].text() == "진행 중 3"
    assert win.status_buttons[Status.DONE].text() == "완료 1"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (SortKey.CREATED_ASC, ["A 기획", "A1 하위 작업", "B 리뷰", "C 회의"]),
        (SortKey.COMPLETED_DESC, ["B 리뷰"]),
        (SortKey.ALPHA, ["A 기획", "A1 하위 작업", "B 리뷰", "C 회의"]),
    ],
)
def test_sort(win, store, key, expected):
    fill(store)
    win.sort_combo.setCurrentIndex(win.sort_combo.findData(key))
    assert texts(win)[: len(expected)] == expected


def test_empty_state(win, store):
    assert win.empty_label.isVisibleTo(win)
    store.add("x")
    assert not win.empty_label.isVisibleTo(win)


def test_ctrl_f_focuses_search(win):
    win.activateWindow()
    win.table.setFocus()
    win.focus_search()
    assert win.search.hasFocus() or win.focusWidget() is win.search


# ---------------------------------------------------------------- 수정·완료


def test_done_toggle_and_edit_via_model(win, store):
    ids = fill(store)
    row = texts(win).index("C 회의")
    win.model.setData(
        win.model.index(row, COL_DONE), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole
    )
    assert store.get(ids["C"]).done
    row = texts(win).index("C 회의")
    win.model.setData(win.model.index(row, COL_TEXT), "  C   회의록  ")
    assert store.get(ids["C"]).text == "C 회의록"
    assert not win.model.setData(win.model.index(row, COL_TEXT), "   ")
    assert store.get(ids["C"]).text == "C 회의록"


def test_note_and_manager_stay_in_sync(store, settings, tmp_path, win):
    note = NoteWidget(store, settings, tmp_path / "s.json")
    note.show()
    store.add("노트에서 추가")
    assert "노트에서 추가" in texts(win)
    win.model.setData(win.model.index(0, COL_TEXT), "관리 창에서 수정")
    assert note.list_view.rows[0].todo.text == "관리 창에서 수정"
    note._settings_timer.stop()
    note.hide()
    note.deleteLater()


# ---------------------------------------------------------------- 선택·일괄 작업


def click_select_cell(win, row):
    rect = win.table.visualRect(win.model.index(row, COL_SELECT))
    QTest.mouseClick(win.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_select_checkbox_toggles_without_clearing(win, store):
    fill(store)
    click_select_cell(win, 0)
    click_select_cell(win, 2)
    assert len(win.selected_ids()) == 2
    assert cell(win, 0, COL_SELECT, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert win.selection_label.text() == "2개 선택"
    click_select_cell(win, 0)
    assert len(win.selected_ids()) == 1


def test_bulk_done_and_undone(win, store):
    ids = fill(store)
    win.select_ids([ids["A"], ids["C"]])
    assert win.done_btn.text() == "완료 처리"
    win.done_btn.click()
    assert store.get(ids["A"]).done and store.get(ids["C"]).done
    assert not store.get(ids["A1"]).done  # 하위로 전파 안 됨
    assert sorted(win.selected_ids()) == sorted([ids["A"], ids["C"]])  # 갱신 후에도 선택 유지
    assert win.done_btn.text() == "완료 취소"
    win.done_btn.click()
    assert not store.get(ids["A"]).done


def test_buttons_disabled_without_selection(win, store):
    fill(store)
    assert not win.delete_btn.isEnabled()
    assert win.selection_label.text() == "선택한 항목 없음"


def test_delete_parent_mentions_children_and_undo(win, store):
    ids = fill(store)
    win.select_ids([ids["A"]])
    win.delete_btn.click()
    assert texts(win) == ["C 회의", "B 리뷰"]
    assert win.toast_label.text() == "2개 삭제됨 (하위 1개 포함)"
    assert win.toast_undo.isVisible()
    win.toast_undo.click()
    assert len(store) == 4
    assert [t.indent for t in store.todos()] == [0, 1, 0, 0]


def test_delete_key_and_ctrl_z(win, store):
    ids = fill(store)
    win.select_ids([ids["C"]])
    win.table.setFocus()
    QTest.keyClick(win.table, Qt.Key.Key_Delete)
    assert "C 회의" not in texts(win)
    QTest.keyClick(win.table, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert "C 회의" in texts(win)


def test_export_selected_flat(win, store, tmp_path, monkeypatch):
    ids = fill(store)
    out = tmp_path / "out.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    win.select_ids([ids["A1"], ids["B"]])
    win.export_btn.click()
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# 오늘 할 일 (선택)")
    assert "- [ ] A1 하위 작업\n" in md  # 들여쓰기 없이 평평하게
    assert "- [x] B 리뷰\n" in md
    assert "A 기획" not in md


def test_export_cancel_does_nothing(win, store, monkeypatch):
    ids = fill(store)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    win.select_ids([ids["A"]])
    win.export_btn.click()
    assert not win.toast.isVisible()


# ---------------------------------------------------------------- 창 위치·노트 연결


def test_geometry_saved_on_close(store, settings):
    w = ManagerWindow(store, settings)
    w.show()
    w.resize(700, 500)
    w.close()
    assert (settings["manager_w"], settings["manager_h"]) == (700, 500)


def test_note_opens_single_manager(store, settings, tmp_path):
    note = NoteWidget(store, settings, tmp_path / "s.json")
    note.show()
    note.open_manager_window()
    first = note._manager_win
    assert first is not None and first.isVisible()
    note.open_manager_window()
    assert note._manager_win is first
    labels = [a.text() for a in note.build_menu().actions()]
    assert "전체 목록 보기" in labels
    first.close()
    note._settings_timer.stop()
    note.hide()
    note.deleteLater()


def test_click_done_circle_toggles(win, store):
    ids = fill(store)
    row = texts(win).index("C 회의")
    rect = win.table.visualRect(win.model.index(row, COL_DONE))
    QTest.mouseClick(win.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert store.get(ids["C"]).done
    assert win.selected_ids() == []  # 완료 칸 클릭은 선택을 바꾸지 않음


def test_accent_follows_settings(win, settings):
    settings.apply_preset("mint")
    win.apply_style()
    assert win.done_delegate.accent.name().upper() == settings["accent_color"]


def test_archived_filter_restore_and_readonly(win, store):
    ids = fill(store)
    store.archive_completed()  # B 리뷰가 보관됨
    assert "B 리뷰" not in texts(win)
    assert win.status_buttons[Status.ARCHIVED].text() == "보관됨 1"
    win.status_buttons[Status.ARCHIVED].click()
    assert texts(win) == ["B 리뷰"]
    idx = win.model.index(0, COL_TEXT)
    assert not idx.flags() & Qt.ItemFlag.ItemIsEditable  # 보관함은 읽기 전용
    rect = win.table.visualRect(win.model.index(0, COL_DONE))
    QTest.mouseClick(win.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert store.get(ids["B"]).done  # 체크도 안 바뀜
    win.select_ids([ids["B"]])
    assert win.done_btn.text() == "노트로 복원"
    win.done_btn.click()
    assert texts(win) == []
    assert "B 리뷰" in [t.text for t in store.todos()]


def menu_texts(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test_context_menu_archive(win, store):
    ids = fill(store)
    win.select_ids([ids["A"]])
    labels = menu_texts(win.build_context_menu())
    assert "보관함으로 (1개)" in labels and "삭제" in labels and "내용 수정" in labels
    win.archive_selected()
    assert win.toast_label.text() == "2개 보관함으로 (하위 1개 포함)"
    assert "A 기획" not in texts(win)
    win.toast_undo.click()
    assert "A 기획" in texts(win)


def test_context_menu_in_archived_view(win, store):
    ids = fill(store)
    store.archive([ids["C"]])
    win.status_buttons[Status.ARCHIVED].click()
    win.select_ids([ids["C"]])
    labels = menu_texts(win.build_context_menu())
    assert labels[0] == "노트로 복원 (1개)" and "완전히 삭제" in labels
    assert "보관함으로 (1개)" not in labels


def test_day_column_and_move_to_tomorrow(win, store):
    from datetime import date, timedelta

    from sticky_todo.manager_window import COL_DAY

    ids = fill(store)
    row = texts(win).index("C 회의")
    assert cell(win, row, COL_DAY) == "오늘"
    win.select_ids([ids["C"]])
    labels = menu_texts(win.build_context_menu())
    assert "날짜 옮기기" in labels and "노트에서 보기" in labels
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    win.move_selected_to(tomorrow)
    assert store.get(ids["C"]).day == tomorrow
    assert cell(win, texts(win).index("C 회의"), COL_DAY) == "내일"
    assert "내일" in win.toast_label.text()


def test_search_spans_all_days(win, store):
    store.add("오늘 회의")
    store.set_day("2026-12-31")
    store.add("연말 회의")
    win.search.setText("회의")
    assert sorted(texts(win)) == ["연말 회의", "오늘 회의"]


@pytest.mark.parametrize("how", ["ctrl_click", "select_column"])
def test_right_click_keeps_multi_selection(win, store, monkeypatch, how):
    """여러 개 고른 뒤 그중 하나를 우클릭해도 선택이 하나로 줄지 않는다 (실제 마우스 입력)."""
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication

    fill(store)
    t = win.table

    def center(row, col):
        return t.visualRect(win.model.index(row, col)).center()

    if how == "ctrl_click":
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=center(0, COL_TEXT))
        QTest.mouseClick(
            t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
            center(2, COL_TEXT),
        )  # fmt: skip
    else:
        click_select_cell(win, 0)
        click_select_cell(win, 2)
    assert len(win.selected_ids()) == 2

    seen = []

    class FakeMenu:
        def __init__(self, menu):
            self.menu = menu

        def exec(self, *_args):
            seen.append((len(win.selected_ids()), menu_texts(self.menu)))

    build = win.build_context_menu
    monkeypatch.setattr(win, "build_context_menu", lambda: FakeMenu(build()))
    pos = center(2, COL_TEXT)
    QTest.mousePress(t.viewport(), Qt.MouseButton.RightButton, pos=pos)
    QTest.mouseRelease(t.viewport(), Qt.MouseButton.RightButton, pos=pos)
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, t.viewport().mapToGlobal(pos))
    QApplication.sendEvent(t.viewport(), ev)
    assert seen and seen[-1][0] == 2
    assert "보관함으로 (2개)" in seen[-1][1]
    assert len(win.selected_ids()) == 2

    # 선택 밖의 줄을 우클릭하면 그 줄만
    pos = center(1, COL_TEXT)
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, t.viewport().mapToGlobal(pos))
    QApplication.sendEvent(t.viewport(), ev)
    assert seen[-1][0] == 1


def test_quick_move_days():
    from sticky_todo.note_widget import quick_move_days

    # 2026-11-26은 목요일 → 다음 주(11/29 일요일 시작)의 월요일은 11/30
    assert quick_move_days("2026-11-26") == [
        ("오늘", "2026-11-26"),
        ("내일", "2026-11-27"),
        ("모레 (11/28)", "2026-11-28"),
        ("다음 주 월요일 (11/30)", "2026-11-30"),
    ]
    # 일요일이면 그 주가 이번 주라서 다음 주 월요일은 8일 뒤
    assert quick_move_days("2026-11-29")[-1] == ("다음 주 월요일 (12/7)", "2026-12-07")
    # 연말
    assert quick_move_days("2026-12-31")[2:] == [
        ("모레 (1/2)", "2027-01-02"),
        ("다음 주 월요일 (1/4)", "2027-01-04"),
    ]


def test_context_menu_move_options_and_pick(win, store, monkeypatch):
    from sticky_todo import date_picker

    ids = fill(store)
    win.select_ids([ids["A"], ids["C"]])
    menu = win.build_context_menu()
    move = next(a.menu() for a in menu.actions() if a.text() == "날짜 옮기기")
    labels = menu_texts(move)
    assert labels[:2] == ["오늘", "내일"]
    assert labels[2].startswith("모레") and labels[3].startswith("다음 주 월요일")
    assert labels[-1] == "날짜 고르기…"

    monkeypatch.setattr(date_picker, "pick_date", lambda *_a, **_k: "2027-02-10")
    next(a for a in move.actions() if a.text() == "날짜 고르기…").trigger()
    # A는 하위 A1까지 함께 옮겨진다
    assert {store.get(i).day for i in (ids["A"], ids["A1"], ids["C"])} == {"2027-02-10"}
    assert "옮겼어요" in win.toast_label.text()


def test_date_picker_dialog(qapp, store):
    """실제 마우스로 달력 날짜를 누르면 그 날이 골라지고 창이 닫힌다."""
    from sticky_todo.date_picker import DatePicker

    store.set_day("2026-09-25")
    store.add("성묘")
    dlg = DatePicker(store, "2026-09-25")
    dlg.show()
    assert dlg.month_label.text() == "2026년 9월"
    cells = {c.day: c for c in dlg.calendar.cells}
    assert cells["2026-09-25"].current and cells["2026-09-25"].holiday
    assert cells["2026-09-25"].open == 1
    dlg.next_btn.click()
    assert dlg.month_label.text() == "2026년 10월"
    i = dlg.calendar.index_of("2026-10-14")
    pos = dlg.calendar.cell_rect(i).center().toPoint()
    QTest.mouseClick(dlg.calendar, Qt.MouseButton.LeftButton, pos=pos)
    assert dlg.selected == "2026-10-14" and dlg.result() == dlg.DialogCode.Accepted
    dlg.deleteLater()
