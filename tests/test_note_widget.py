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
    assert text.startswith(f"# {note.page_title()}")
    assert note.page_title().endswith(" 할 일")


def test_rename_and_preset_saved(note, tmp_path):
    note.apply_preset("mint")
    note.save_settings()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["bg_color"] == PRESETS["mint"]["bg_color"].upper()


def test_geometry_remembered(note):
    note.resize(300, 400)
    note.move(50, 60)
    s = note.settings
    assert (s["window_w"], s["window_h"]) == (300, 400)


def test_archive_completed_toast(note, store):
    a = store.add("A")
    store.set_done(a.id, True)
    note.archive_completed()
    assert len(store) == 0
    assert [t.text for t in store.archived()] == ["A"]
    assert "보관" in note.toast_label.text()
    note.toast_undo.click()
    assert [t.text for t in store.todos()] == ["A"]


def test_auto_archive_on_new_day(note, store):
    from datetime import date

    a = store.add("어제 끝낸 일")
    b = store.add("오늘 끝낸 일")
    store.add("안 끝난 일")
    store.set_done(a.id, True)
    store.set_done(b.id, True)
    from conftest import local_iso

    store._todos[0].completed_at = local_iso(2026, 11, 23, 18, 0)
    store._todos[1].completed_at = local_iso(2026, 11, 24, 9, 0)
    assert note.auto_archive(today=date(2026, 11, 24)) == 1
    assert [t.text for t in store.todos()] == ["오늘 끝낸 일", "안 끝난 일"]
    assert "지난 날" in note.toast_label.text()
    note.settings.set("auto_archive", False)
    assert note.auto_archive(today=date(2026, 11, 25)) == 0


def test_menu_builds(note):
    menu = note.build_menu()
    labels = [a.text() for a in menu.actions() if a.text()]
    assert "텍스트 파일로 저장…" in labels and "종료" in labels
    assert not any("마크다운" in x for x in labels)  # 개발 용어 대신 쉬운 말
    undo = next(a for a in menu.actions() if a.text().startswith("방금 지운 것 되돌리기"))
    assert not undo.isEnabled()  # 지운 게 없으면 비활성


def test_window_can_shrink(note):
    note.resize(200, 150)
    assert (note.width(), note.height()) == (200, 150)


def test_preset_restyles_existing_widgets(note):
    """색을 바꾸면 새로 만든 줄뿐 아니라 제목 바·입력창도 새 색을 받아야 한다."""
    note.apply_preset("mint")
    mint = PRESETS["mint"]
    assert mint["title_color"].upper() in note.card.styleSheet()
    assert mint["bg_color"].upper() in note.card.styleSheet()
    assert note.card.graphicsEffect() is None  # 그림을 캐시하는 효과를 쓰지 않음
    size = note.settings["font_size"]
    expected = {
        note.title_bar.label: size + 4,  # 제목은 크게
        note.input: size,
        note.list_view.empty_label: size - 1,  # 사용 팁
        note.count_label: size - 1,  # 개수는 조금 작게
    }
    for w, pt in expected.items():
        w.ensurePolished()
        assert w.font().pointSize() == pt


def test_menu_and_dialogs_do_not_inherit_note_colors(note):
    """노트 글자색이 메뉴에 새면 다크모드에서 글자가 안 보인다."""
    assert note.styleSheet() == ""
    menu = note.build_menu()
    menu.ensurePolished()
    assert menu.palette().color(menu.foregroundRole()).name().upper() != note.settings["text_color"]


# ---------------------------------------------------------------- 완료 항목 표시 방식

from sticky_todo.note_widget import arrange_rows  # noqa: E402
from sticky_todo.storage import Todo  # noqa: E402


def _todos(spec):
    """spec: [("A", indent, done), ...]"""
    return [Todo(id=t, text=t, indent=i, done=d) for t, i, d in spec]


def _show(rows):
    return [(t.text, dim, strike) for t, dim, strike in rows]


SPEC = [
    ("A", 0, True),
    ("A1", 1, False),
    ("B", 0, False),
    ("C", 0, True),
    ("C1", 1, True),
    ("D", 0, False),
    ("D1", 1, True),
    ("D2", 1, False),
]


def test_arrange_strike_and_dim():
    todos = _todos(SPEC)
    assert [r[0].text for r in arrange_rows(todos, "strike")] == [t for t, _, _ in SPEC]
    assert _show(arrange_rows(todos, "strike"))[0] == ("A", True, True)
    assert _show(arrange_rows(todos, "dim"))[0] == ("A", True, False)


def test_arrange_bottom_within_parent():
    rows = arrange_rows(_todos(SPEC), "bottom")
    assert [r[0].text for r in rows] == ["B", "D", "D2", "D1", "A", "A1", "C", "C1"]


def test_arrange_hide_only_fully_done():
    rows = _show(arrange_rows(_todos(SPEC), "hide"))
    # A는 안 끝난 A1이 있어서 흐리게 남고, C(+C1)와 D1은 숨김
    assert rows == [
        ("A", True, True),
        ("A1", False, False),
        ("B", False, False),
        ("D", False, False),
        ("D2", False, False),
    ]


def test_note_hide_mode_and_drag_flag(note, store):
    a = store.add("A")
    store.add("B")
    store.set_done(a.id, True)
    note.settings.set("done_style", "hide")
    note.apply_settings()
    assert [r.todo.text for r in note.list_view.rows] == ["B"]
    assert note.list_view.drag_enabled
    note.settings.set("done_style", "bottom")
    note.apply_settings()
    assert [r.todo.text for r in note.list_view.rows] == ["B", "A"]
    assert not note.list_view.drag_enabled


def test_check_style_and_spacing_applied(note, store):
    store.add("A")
    note.settings.set("check_style", "bullet")
    note.settings.set("line_spacing", "relaxed")
    note.apply_settings()
    row = note.list_view.rows[0]
    assert row.check.shape == "bullet"
    assert row.layout().contentsMargins().top() == 8


def test_title_bar_hover_mode(note):
    note.settings.set("title_bar_mode", "hover")
    note.apply_settings()
    note._update_title_visibility(False)
    assert not note.title_bar.isVisible()
    note._update_title_visibility(True)
    assert note.title_bar.isVisible()


def test_ctrl_z_on_list_restores(note, store):
    store.add("A")
    note.list_view.request_delete(store.todos()[0].id)
    assert len(store) == 0
    note.list_view.setFocus()
    QTest.keyClick(note.list_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(store) == 1


def test_enter_commits_and_does_not_reopen(note, store):
    store.add("A")
    store.add("B")
    note.list_view.select(store.todos()[1].id)
    row = note.list_view.rows[1]
    row.begin_edit()
    row.editor.setText("B 수정")
    QTest.keyClick(row.editor, Qt.Key.Key_Return)
    assert store.todos()[1].text == "B 수정"
    assert not any(r.editing for r in note.list_view.rows)  # 다시 열리지 않음


def test_double_click_event_is_consumed(note, store):
    store.add("A")
    row = note.list_view.rows[0]
    QTest.mouseDClick(row.label, Qt.MouseButton.LeftButton)
    assert row.editing


def test_shadow_toggle_keeps_note_size(note):
    note.resize(300, 300)
    before = note.layout().contentsMargins().left()
    note.settings.set("shadow", False)
    note.apply_settings()
    assert note.layout().contentsMargins().left() == before


def test_side_buttons_centered_while_editing(note, store):
    store.add("A")
    row = note.list_view.rows[0]
    lay = row.layout()
    top = Qt.AlignmentFlag.AlignTop
    center = Qt.AlignmentFlag.AlignVCenter
    assert lay.itemAt(lay.indexOf(row.check)).alignment() == top
    row.begin_edit()
    assert lay.itemAt(lay.indexOf(row.check)).alignment() == center
    assert lay.itemAt(lay.indexOf(row.delete_btn)).alignment() == center
    QTest.keyClick(row.editor, Qt.Key.Key_Escape)
    assert lay.itemAt(lay.indexOf(row.check)).alignment() == top


# ---------------------------------------------------------------- 날짜 페이지

from sticky_todo.note_widget import day_label  # noqa: E402
from sticky_todo.storage import today_str  # noqa: E402


def test_day_label():
    from datetime import date

    today = date(2026, 11, 24)
    assert day_label("2026-11-24", today) == "오늘 · 11월 24일 (화)"
    assert day_label("2026-11-25", today) == "내일 · 11월 25일 (수)"
    assert day_label("2026-11-23", today) == "어제 · 11월 23일 (월)"
    assert day_label("2026-11-27", today) == "11월 27일 (금)"
    assert day_label("2027-01-02", today) == "2027년 1월 2일 (토)"
    assert day_label("2026-11-25", today, short=True) == "내일"
    assert day_label("2026-12-01", today, short=True) == "12/1 (화)"


def test_next_day_page_is_separate(note, store):
    store.add("오늘 일")
    note.title_bar.next_btn.click()
    assert note.title_bar.label.text().startswith("내일")
    assert note.list_view.rows == [] or not note.list_view.rows
    note.input.insert("내일 일")
    QTest.keyClick(note.input, Qt.Key.Key_Return)
    assert [t.text for t in store.todos()] == ["내일 일"]
    assert note.count_label.text() == "0/1"
    note.title_bar.prev_btn.click()
    assert [r.todo.text for r in note.list_view.rows] == ["오늘 일"]
    note.go_day(1)
    note.title_bar.today_requested.emit()  # 제목 더블클릭 = 오늘로
    assert store.current_day == today_str()


def test_overdue_bar_and_bring(note, store):
    store.set_day("2020-01-01")
    store.add("옛날 일")
    store.set_day(today_str())
    store.add("오늘 일")
    assert note.overdue_bar.isVisibleTo(note)
    assert note.overdue_label.text() == "지난 날 못 끝낸 일 1개"
    note.overdue_btn.click()
    assert [t.text for t in store.todos()] == ["옛날 일", "오늘 일"]
    assert not note.overdue_bar.isVisibleTo(note)
    note.go_day(1)
    assert not note.overdue_bar.isVisibleTo(note)  # 오늘 페이지에서만


def test_new_day_moves_today_page(note, store):
    note._today = "2020-01-01"
    store.set_day("2020-01-01")
    note.check_new_day()
    assert store.current_day == today_str()


# ---------------------------------------------------------------- 할 일 우클릭 메뉴


def _labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test_row_menu_on_today(note, store):
    a = store.add("A")
    labels = _labels(note.build_row_menu(a.id))
    assert labels == [
        "고치기\t더블클릭",
        "완료 처리",
        "하위 할 일로\tTab",
        "상위로 꺼내기\tShift+Tab",
        "내일로 미루기",
        "보관함으로",
        "삭제",
    ]


def test_row_menu_postpone_moves_with_children(note, store):
    from sticky_todo.note_widget import _shift_day

    p = store.add("P")
    store.add("C", 1)
    store.add("Q")
    note.move_todo(p.id, _shift_day(today_str(), 1))
    assert [t.text for t in store.todos()] == ["Q"]
    assert [t.text for t in store.todos(_shift_day(today_str(), 1))] == ["P", "C"]
    assert note.toast_label.text() == "내일 페이지로 옮겼어요 (하위 1개 포함)"


def test_row_menu_on_other_day_offers_today(note, store):
    note.go_day(2)
    t = store.add("모레 일")
    labels = _labels(note.build_row_menu(t.id))
    assert "다음 날로 미루기" in labels and "오늘로 옮기기" in labels
    note.move_todo(t.id, today_str())
    assert store.get(t.id).day == today_str()


def test_row_menu_actions(note, store):
    a = store.add("A")
    store.add("B")
    menu = note.build_row_menu(a.id)
    {x.text(): x for x in menu.actions()}["완료 처리"].trigger()
    assert store.get(a.id).done
    b = store.todos()[1]
    {x.text(): x for x in note.build_row_menu(b.id).actions()}["하위 할 일로\tTab"].trigger()
    assert store.get(b.id).indent == 1
    note.archive_todo(a.id)
    assert [t.text for t in store.archived()] == ["A", "B"]  # 하위 항목까지
    note.toast_undo.click()
    assert len(store.todos()) == 2


# ---------------------------------------------------------------- 사용성 (보기 쉽게, 알기 쉽게)


def _contrast(fg, bg):
    def lum(h):
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted([lum(fg), lum(bg)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("preset", list(PRESETS))
def test_soft_text_meets_contrast(preset):
    from sticky_todo.note_widget import RING_ALPHA, SOFT_TEXT_ALPHA, _mix

    p = PRESETS[preset]
    bg, text = p["bg_color"].upper(), p["text_color"].upper()
    assert _contrast(_mix(text, bg, SOFT_TEXT_ALPHA), bg) >= 4.5  # 날짜·개수·안내 글씨
    assert _contrast(_mix(text, bg, RING_ALPHA), bg) >= 3.0  # 빈 체크 동그라미·아이콘


def test_empty_page_shows_tips(note):
    text = note.list_view.empty_label.text()
    assert "Enter" in text and "우클릭" in text and "Ctrl+Alt+T" in text


def test_first_hide_explains_tray_once(note, dialogs):
    note.hide_to_tray = True
    note.close()
    note.show()
    note.close()
    hints = [d for d in dialogs if d[1] == "노트를 숨겨요"]
    assert len(hints) == 1  # 처음 한 번만
    assert "Ctrl+Alt+T" in hints[0][2]


def test_first_auto_archive_explains_once(note, store, dialogs):
    from datetime import date

    for _ in range(2):
        t = store.add("어제 끝낸 일")
        store.set_done(t.id, True)
        store._todos[-1].completed_at = "2026-11-20T10:00:00+09:00"
        note.auto_archive(today=date(2026, 11, 24))
    hints = [d for d in dialogs if d[1] == "완료한 일을 보관했어요"]
    assert len(hints) == 1 and "보관됨" in hints[0][2]


def test_toast_stays_long_enough(note):
    from sticky_todo.note_widget import TOAST_MS

    assert TOAST_MS >= 10_000


def test_day_title_fits():
    from datetime import date

    from sticky_todo.note_widget import day_title

    today = date(2026, 11, 25)
    assert day_title("2026-11-25", today) == ("오늘", "11월 25일 (수)")
    assert day_title("2026-11-27", today) == ("11월 27일", "금요일")
