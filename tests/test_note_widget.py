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
    assert note.count_label.text() == "0 / 2 완료"
    note.list_view.rows[0].check.click()
    assert store.todos()[0].done
    assert note.count_label.text() == "1 / 2 완료"


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


def test_window_can_shrink_but_not_overlap(note):
    """작게 줄일 수는 있지만(예전 323px 고정 문제), 글자가 겹치는 크기까지는 안 된다."""
    note.resize(200, 150)
    QGuiApplication.processEvents()
    assert note.width() <= 300 and note.height() <= 280  # 충분히 작게
    _assert_nothing_squeezed(note)


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
    assert note.count_label.text() == "0 / 1 완료"
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
        "다른 날로 옮기기",
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
    from sticky_todo.note_widget import INPUT_TINT_ALPHA, _mix, ring_alpha, soft_alpha

    p = PRESETS[preset]
    bg, text = p["bg_color"].upper(), p["text_color"].upper()
    assert _contrast(_mix(text, bg, soft_alpha(text, bg)), bg) >= 4.5  # 날짜·개수·안내 글씨
    assert _contrast(_mix(text, bg, ring_alpha(text, bg)), bg) >= 3.0  # 빈 체크 동그라미·아이콘
    field = _mix("#767680", bg, INPUT_TINT_ALPHA)  # 입력칸 안내 글씨는 입력칸 회색 위
    assert _contrast(_mix(text, field, soft_alpha(text, field)), field) >= 4.5


def test_light_presets_keep_original_alpha():
    """노랑처럼 원래 대비가 충분한 배경은 예전 진하기 그대로 (보이는 모양이 안 바뀜)."""
    from sticky_todo.note_widget import RING_ALPHA, SOFT_TEXT_ALPHA, ring_alpha, soft_alpha

    y = PRESETS["yellow"]
    assert soft_alpha(y["text_color"], y["bg_color"]) == SOFT_TEXT_ALPHA
    assert ring_alpha(y["text_color"], y["bg_color"]) == RING_ALPHA


@pytest.mark.parametrize("bg", ["#EBEBEB", "#B0B0B0", "#FFB3C0", "#8FBF9F"])
def test_custom_background_gets_darker_soft_text(bg):
    """진한 배경을 골라도 보조 글자가 기준 아래로 흐려지지 않는다 (안 되면 진한 글자 그대로)."""
    from sticky_todo.note_widget import _mix, soft_alpha

    a = soft_alpha("#1C1C1E", bg)
    assert a == 1.0 or _contrast(_mix("#1C1C1E", bg, a), bg) >= 4.5


def test_ios_preset_applies(note):
    note.apply_preset("ios")
    css = note.card.styleSheet()
    assert "#EBEBEB" in css.upper()
    assert note.settings["text_color"] == "#2F2F32"
    assert note.settings["accent_color"] == "#FF9F0A"


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


# ---------------------------------------------------------------- 주간 보기


@pytest.mark.parametrize(
    ("day", "start"),
    [
        ("2026-11-22", "2026-11-22"),  # 일요일은 그 주의 첫날
        ("2026-11-25", "2026-11-22"),  # 수요일
        ("2026-11-28", "2026-11-22"),  # 토요일은 마지막 날
        ("2026-11-29", "2026-11-29"),  # 다음 주 일요일
        ("2027-01-01", "2026-12-27"),  # 해를 넘는 주
    ],
)
def test_week_starts_on_sunday(day, start):
    from sticky_todo.note_widget import week_days, week_start

    assert week_start(day) == start
    days = week_days(day)
    assert days[0] == start and len(days) == 7 and day in days


def test_week_title():
    from datetime import date

    from sticky_todo.note_widget import week_title

    today = date(2026, 11, 25)
    assert week_title("2026-11-25", today) == ("이번 주", "11월 22일 – 28일")
    assert week_title("2026-12-01", today) == ("다음 주", "11월 29일 – 12월 5일")
    assert week_title("2026-11-16", today) == ("지난주", "11월 15일 – 21일")
    assert week_title("2026-12-09", today) == ("12월 6일 주", "12월 6일 – 12일")
    assert week_title("2027-01-01", today) == ("12월 27일 주", "2026년 12월 27일 – 2027년 1월 2일")


def test_compute_drop_from_other_day():
    """다른 날에서 끌고 온 항목은 그 자리에 끼워 넣는다."""
    assert compute_drop(E, "X", 0, 3) == ("A", 0)
    assert compute_drop(E, "X", 3, 1) == ("B", 1)  # A2 밑, B 앞 → A의 하위 가능
    assert compute_drop(E, "X", 5, 0) == (None, 0)
    assert compute_drop([], "X", 0, 2) == (None, 0)  # 빈 날


def _week_note(note):
    note.set_view_mode("week")
    QGuiApplication.processEvents()
    return note


def test_week_view_shows_seven_days(note, store):
    from sticky_todo.note_widget import _shift_day, week_days

    store.add("오늘 일")
    days = week_days(today_str())
    store.set_day(days[0] if days[0] != today_str() else days[1])
    store.add("다른 날 일")
    store.set_day(today_str())
    _week_note(note)
    lv = note.list_view
    assert lv.days == days
    assert [s.day for s in lv._sections] == days
    assert sorted(r.todo.text for r in lv.rows) == ["다른 날 일", "오늘 일"]
    assert note.count_label.text() == "0 / 2 완료"
    assert note.title_bar.label.text() == "이번 주"
    assert note.input.placeholderText() == "오늘 할 일 추가"
    # 다른 주로 넘기면 한 주씩
    note.go_day(1)
    assert store.current_day == _shift_day(today_str(), 7)
    assert note.title_bar.label.text() == "다음 주"


def test_week_header_click_sets_target_day(note, store):
    from sticky_todo.note_widget import day_label

    _week_note(note)
    other = next(d for d in note.list_view.days if d != today_str())
    note.list_view.day_clicked.emit(other)
    assert store.current_day == other
    assert note.input.placeholderText() == f"{day_label(other, short=True)} 할 일 추가"
    note.input.insert("그 날 할 일")
    QTest.keyClick(note.input, Qt.Key.Key_Return)
    assert [t.text for t in store.todos(other)] == ["그 날 할 일"]


def test_week_header_double_click_opens_day(note, store):
    _week_note(note)
    other = note.list_view.days[0]
    note.list_view.day_double_clicked.emit(other)
    assert note.view_mode == "day"
    assert store.current_day == other
    assert note.list_view.days is None


def test_week_drag_to_other_day(note, store):
    """주간 보기에서 다른 날 구역에 놓으면 그 날로 옮겨진다."""
    from sticky_todo.note_widget import week_days

    days = week_days(today_str())
    target = days[6] if days[6] != today_str() else days[0]
    a = store.add("옮길 일")
    _week_note(note)
    lv = note.list_view
    section = next(s for s in lv._sections if s.day == target)
    lv._drag = (a.id, 0, 0)
    pos = section.header.geometry().center()
    pos.setY(section.header.geometry().bottom() + 3)
    lv.finish_drop(pos)
    lv._drag = None
    assert store.todos(today_str()) == []
    assert [t.text for t in store.todos(target)] == ["옮길 일"]


def test_row_menu_move_to_other_day(note, store):
    from sticky_todo.note_widget import _shift_day, week_days

    a = store.add("A")
    menu = note.build_row_menu(a.id)
    sub = next(act.menu() for act in menu.actions() if act.text() == "다른 날로 옮기기")
    labels = [act.text() for act in sub.actions() if act.text()]
    assert len(labels) == 7  # 같은 주 다른 6일 + 다음 주 같은 요일
    assert labels[-1].startswith("다음 주 같은 요일")
    sub.actions()[-1].trigger()
    assert [t.text for t in store.todos(_shift_day(today_str(), 7))] == ["A"]
    assert len(week_days(today_str())) == 7


def test_view_mode_remembers_size_per_mode(note, store):
    note.resize(300, 400)
    QGuiApplication.processEvents()
    assert (note.settings["window_w"], note.settings["window_h"]) == (300, 400)
    note.set_view_mode("week")
    QGuiApplication.processEvents()
    assert (note.width(), note.height()) == (note.settings["week_w"], note.settings["week_h"])
    note.resize(420, 700)
    QGuiApplication.processEvents()
    assert (note.settings["week_w"], note.settings["week_h"]) == (420, 700)
    assert (note.settings["window_w"], note.settings["window_h"]) == (300, 400)  # 그대로
    note.set_view_mode("day")
    QGuiApplication.processEvents()
    assert (note.width(), note.height()) == (300, 400)
    note.save_settings()
    saved = json.loads(note.settings_path.read_text(encoding="utf-8"))
    assert saved["view_mode"] == "day" and saved["week_w"] == 420


def test_view_mode_shortcut_buttons(note):
    note.title_bar.mode_buttons["week"].click()
    assert note.view_mode == "week"
    assert note.title_bar.mode_buttons["week"].isChecked()
    note.title_bar.mode_buttons["day"].click()
    assert note.view_mode == "day"


def test_count_label_follows_counts(note, store):
    assert note.count_label.text() == "할 일 없음"
    a = store.add("A")
    store.add("B")
    store.set_done(a.id, True)
    assert note.count_label.text() == "1 / 2 완료"


def test_day_header_box_aligns_with_banner(note, store):
    """날짜 선택 칸의 좌우 끝이 '못 끝낸 일' 배너와 같은 선."""
    from sticky_todo.note_widget import _shift_day

    store.set_day(_shift_day(today_str(), -1))
    store.add("어제 못 한 일")
    store.set_day(today_str())
    note.set_view_mode("week")
    note.resize(360, 1400)  # 스크롤 막대 없이
    QGuiApplication.processEvents()
    _assert_header_matches_banner(note)
    note.resize(360, 500)  # 스크롤 막대가 생겨도
    QGuiApplication.processEvents()
    QGuiApplication.processEvents()
    assert note.list_view.verticalScrollBar().isVisible()
    _assert_header_matches_banner(note)


def _assert_header_matches_banner(note):
    assert note.overdue_bar.isVisible()
    header = next(s.header for s in note.list_view._sections if s.day == today_str())
    from PySide6.QtCore import QPoint

    box_x = header.box.mapTo(note.card, QPoint(0, 0)).x()
    banner_x = note.overdue_bar.mapTo(note.card, QPoint(0, 0)).x()
    assert box_x == banner_x
    assert box_x + header.box.width() == banner_x + note.overdue_bar.width()
    # 글자가 상자 세로 가운데
    label = header.label.geometry()
    assert abs(label.center().y() - header.box.rect().center().y()) <= 1


def test_week_export_has_whole_week(note, store):
    from sticky_todo.note_widget import _plain_day, week_days

    days = week_days(today_str())
    for d in (days[0], days[3], days[6]):
        store.set_day(d)
        store.add(f"{d} 할 일")
    store.set_day(today_str())
    note.set_view_mode("week")
    text = note.markdown_text()
    for d in (days[0], days[3], days[6]):
        assert f"## {_plain_day(d)}" in text
        assert f"- [ ] {d} 할 일" in text
    assert f"## {_plain_day(days[1])}" not in text  # 빈 날은 뺀다
    assert text.startswith("# ") and "할 일" in text.splitlines()[0]
    # 다시 불러오면 체크리스트만 읽힌다
    from sticky_todo.markdown_io import parse_markdown

    assert len(parse_markdown(text)) == 3
    # 하루 보기는 그 날만
    note.set_view_mode("day")
    store.set_day(days[0])
    assert "##" not in note.markdown_text()
    assert f"{days[0]} 할 일" in note.markdown_text()


# ---------------------------------------------------------------- 월간 보기


@pytest.mark.parametrize(
    ("day", "delta", "expected"),
    [
        ("2026-11-25", 1, "2026-12-25"),
        ("2026-11-25", -1, "2026-10-25"),
        ("2026-12-15", 1, "2027-01-15"),  # 해 넘김
        ("2027-01-31", 1, "2027-02-28"),  # 없는 날은 말일로
        ("2028-01-31", 1, "2028-02-29"),  # 윤년
        ("2026-03-31", -1, "2026-02-28"),
    ],
)
def test_shift_month(day, delta, expected):
    from sticky_todo.note_widget import shift_month

    assert shift_month(day, delta) == expected


def test_month_grid_starts_sunday_and_covers_month():
    from datetime import date

    from sticky_todo.note_widget import month_days, month_grid

    grid = month_grid("2026-11-25")  # 2026년 11월 1일은 일요일
    assert grid[0] == "2026-11-01" and grid[-1] == "2026-12-05" and len(grid) == 35
    assert len(month_days("2026-11-25")) == 30
    grid = month_grid("2026-08-10")  # 8월 1일 토요일 → 6주
    assert grid[0] == "2026-07-26" and len(grid) == 42
    assert all(date.fromisoformat(d).weekday() == 6 for d in grid[::7])


def _month_note(note):
    note.set_view_mode("month")
    QGuiApplication.processEvents()
    return note


def test_month_view_calendar_and_list(note, store):
    from sticky_todo.note_widget import month_days

    store.add("오늘 할 일")
    other = next(d for d in month_days(today_str()) if d != today_str())
    store.set_day(other)
    t = store.add("다른 날 할 일")
    store.set_done(t.id, True)
    store.set_day(today_str())
    _month_note(note)
    assert note.month_panel.isVisible()
    assert note.list_view.days is None  # 아래 목록은 고른 날 하루치
    assert [r.todo.text for r in note.list_view.rows] == ["오늘 할 일"]
    cells = {c.day: c for c in note.calendar.cells}
    assert cells[today_str()].today and cells[today_str()].current
    assert (cells[today_str()].open, cells[today_str()].done) == (1, 0)
    assert (cells[other].open, cells[other].done) == (0, 1)
    assert note.count_label.text() == "1 / 2 완료"  # 한 달 전체
    from datetime import date

    d = date.fromisoformat(today_str())
    assert note.title_bar.label.text() == f"{d.month}월"
    # 날짜 누르면 아래 목록이 그 날로
    note.calendar.day_clicked.emit(other)
    assert store.current_day == other
    assert [r.todo.text for r in note.list_view.rows] == ["다른 날 할 일"]
    # ‹ ›는 한 달씩
    note.go_day(1)
    assert date.fromisoformat(store.current_day).month == (d.month % 12) + 1


def test_month_calendar_click_and_double_click(note, store):
    from PySide6.QtCore import QPoint

    _month_note(note)
    cal = note.calendar
    i = next(i for i, c in enumerate(cal.cells) if c.in_month and not c.current)
    target = cal.cells[i].day
    center = cal.cell_rect(i).center().toPoint()
    QTest.mouseClick(cal, Qt.MouseButton.LeftButton, pos=center)
    assert store.current_day == target
    QTest.mouseDClick(cal, Qt.MouseButton.LeftButton, pos=QPoint(center))
    assert note.view_mode == "day" and store.current_day == target


def test_month_drop_moves_todo(note, store):
    a = store.add("옮길 일")
    _month_note(note)
    cal = note.calendar
    target = next(c.day for c in cal.cells if c.in_month and not c.current)
    cal.todo_dropped.emit(a.id, target)
    QGuiApplication.processEvents()  # 끌기가 끝난 뒤 처리 (queued)
    assert store.todos(today_str()) == []
    assert [t.text for t in store.todos(target)] == ["옮길 일"]


def test_month_export_has_whole_month(note, store):
    from sticky_todo.markdown_io import parse_markdown
    from sticky_todo.note_widget import _plain_day, month_days

    days = month_days(today_str())
    for d in (days[0], days[-1]):
        store.set_day(d)
        store.add(f"{d} 할 일")
    store.set_day(today_str())
    _month_note(note)
    text = note.markdown_text()
    assert text.splitlines()[0].endswith("월 할 일")
    for d in (days[0], days[-1]):
        assert f"## {_plain_day(d)}" in text
    assert len(parse_markdown(text)) == 2


def test_month_size_remembered_separately(note):
    note.set_view_mode("month")
    QGuiApplication.processEvents()
    assert (note.width(), note.height()) == (note.settings["month_w"], note.settings["month_h"])
    note.resize(380, 720)
    QGuiApplication.processEvents()
    assert (note.settings["month_w"], note.settings["month_h"]) == (380, 720)
    note.set_view_mode("week")
    QGuiApplication.processEvents()
    assert note.settings["month_w"] == 380
    assert not note.month_panel.isVisible()


def test_month_colors_meet_contrast():
    """달력 글자색: 일요일 빨강은 배경 대비가 모자라면 일반 글자색으로."""
    from sticky_todo.note_widget import contrast

    for preset in PRESETS.values():
        assert contrast("#B3261E", preset["bg_color"]) >= 4.5


# ---------------------------------------------------------------- 주간 보드 (넓은 노트)


def _board_note(note, width=1000):
    note.set_view_mode("week")
    note.resize(width, 520)
    QGuiApplication.processEvents()
    return note


def test_week_switches_to_board_when_wide(note, store):
    from sticky_todo.note_widget import week_days

    store.add("오늘 일")
    _board_note(note)
    assert note.board_active and note.board.isVisible()
    assert not note.list_view.isVisible()
    assert note.board.days == week_days(today_str())
    assert len(note.board.lists) == 7
    i = note.board.days.index(today_str())
    assert [r.todo.text for r in note.board.lists[i].rows] == ["오늘 일"]
    # 좁히면 다시 세로 목록
    note.resize(400, 520)
    QGuiApplication.processEvents()
    assert not note.board.isVisible() and note.list_view.isVisible()
    assert note.list_view.days == week_days(today_str())


def test_board_drag_to_other_column(note, store):
    from PySide6.QtCore import QPoint

    from sticky_todo.note_widget import TodoListView

    a = store.add("옮길 일")
    _board_note(note)
    days = note.board.days
    target = next(i for i, d in enumerate(days) if d != today_str())
    TodoListView._drag_state = (a.id, 0, 10)  # 오늘 칸에서 끌기 시작
    note.board.lists[target].finish_drop(QPoint(10, 6))  # 빈 칸 맨 위
    TodoListView._drag_state = None
    assert store.todos(today_str()) == []
    assert [t.text for t in store.todos(days[target])] == ["옮길 일"]


def test_board_column_click_selects_day(note, store):
    _board_note(note)
    i, d = next((i, d) for i, d in enumerate(note.board.days) if d != today_str())
    # 칸의 빈 곳(목록 아래 공간)을 누르면 그 날이 골라진다
    lv = note.board.lists[i]
    QTest.mouseClick(lv.container, Qt.MouseButton.LeftButton, pos=lv.container.rect().center())
    assert store.current_day == d
    assert note.board.columns[i].property("current") is True
    assert note.input.placeholderText().endswith("할 일 추가")
    note.input.insert("보드에서 추가")
    QTest.keyClick(note.input, Qt.Key.Key_Return)
    assert [t.text for t in store.todos(d)] == ["보드에서 추가"]


def test_board_row_menu_edit_uses_its_column(note, store):
    a = store.add("고칠 일")
    _board_note(note)
    note._view_for(a.id).begin_edit(a.id)
    i = note.board.days.index(today_str())
    assert note.board.lists[i]._row(a.id).editing


# ---------------------------------------------------------------- 도움말


def test_help_window_opens_with_tabs(note):
    note.open_help_window()
    win = note._help_win
    assert win is not None and win.isVisible()
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert names == ["단축키", "마우스로 하기", "보기와 데이터"]
    shortcuts = win.tabs.widget(0)
    keys = [shortcuts.item(r, 0).text() for r in range(shortcuts.rowCount())]
    for k in ("Ctrl+Z", "Ctrl+1 / 2 / 3", "Ctrl+Alt+T", "F1"):
        assert k in keys
    data = win.tabs.widget(2)
    rows = {data.item(r, 0).text(): data.item(r, 1).text() for r in range(data.rowCount())}
    assert rows["저장 위치"] == str(note.store.todos_file.parent)
    # 다시 열면 같은 창
    note.open_help_window()
    assert note._help_win is win
    win.close()


def test_help_in_menu_and_empty_tips(note):
    labels = [a.text() for a in note.build_menu().actions()]
    assert "도움말 · 단축키" in labels
    assert "F1" in note.list_view.empty_label.text()


# ---------------------------------------------------------------- 월간: 달력만 보기


def test_month_list_toggle(note, store):
    store.add("할 일")
    note.set_view_mode("month")
    QGuiApplication.processEvents()
    assert note.list_view.isVisible()
    assert note.list_toggle.text() == "목록 숨기기"
    note.list_toggle.click()
    QGuiApplication.processEvents()
    assert not note.list_view.isVisible()
    assert note.calendar.isVisible() and note.input.isVisible()
    assert note.list_toggle.text() == "목록 보기"
    assert note.settings["month_list"] is False
    note.list_toggle.click()
    QGuiApplication.processEvents()
    assert note.list_view.isVisible() and note.settings["month_list"] is True


def test_month_list_hides_when_short_and_grows_back(note, store):
    note.set_view_mode("month")
    QGuiApplication.processEvents()
    need = note.month_list_height()
    note.resize(note.width(), need - 20)  # 목록 칸이 너무 낮아짐 → 달력만
    QGuiApplication.processEvents()
    assert not note.list_view.isVisible()
    assert note.list_toggle.text() == "목록 보기"
    assert note.settings["month_list"] is True  # 토글은 켜진 채
    note.resize(note.width(), need + 50)  # 다시 높이면 목록이 돌아옴
    QGuiApplication.processEvents()
    assert note.list_view.isVisible()
    # 낮을 때 '목록 보기'를 누르면 노트를 늘려서 보여준다
    note.resize(note.width(), need - 20)
    QGuiApplication.processEvents()
    note.list_toggle.click()
    QGuiApplication.processEvents()
    assert note.height() >= need and note.list_view.isVisible()


def test_month_list_setting_does_not_affect_other_views(note):
    note.set_view_mode("month")
    note.list_toggle.click()  # 달력만
    note.set_view_mode("day")
    QGuiApplication.processEvents()
    assert note.list_view.isVisible() and not note.month_filler.isVisible()


# ---------------------------------------------------------------- 최소 크기 (글자 겹침 방지)


def _visible_stack(note):
    lay = note.card.layout()
    out = []
    for i in range(lay.count()):
        item = lay.itemAt(i)
        w = item.widget()
        if w is not None and w.isVisible():
            out.append(w)
        elif w is None and item.layout() is not None and note.overdue_bar.isVisible():
            out.append(note.overdue_bar)
    return out


def _assert_nothing_squeezed(note):
    stack = _visible_stack(note)
    for w in stack:
        # 위젯이 최소 높이보다 눌리면 글자가 겹친다
        assert w.height() >= w.minimumSizeHint().height(), (w.objectName(), w.height())
    tb = note.title_bar
    assert tb.count_label.width() >= tb.count_label.sizeHint().width()  # '7 / 12 완료' 안 잘림
    assert tb.mode_switch.geometry().right() < tb.count_label.geometry().left()
    assert tb.next_btn.geometry().right() < tb.pin_btn.geometry().left()
    if note.view_mode == "month":
        assert note.calendar.width() >= note.calendar.minimumWidth() > 0


@pytest.mark.parametrize("mode", ["day", "week", "month"])
@pytest.mark.parametrize("font_size", [12, 20])
def test_cannot_shrink_until_text_overlaps(note, store, mode, font_size):
    from sticky_todo.note_widget import _shift_day

    store.set_day(_shift_day(today_str(), -1))
    store.add("어제 못 한 일")  # 배너까지 보이게
    store.set_day(today_str())
    for i in range(3):
        store.add(f"할 일 {i}")
    note.settings.set("font_size", font_size)
    note.apply_settings()
    note.set_view_mode(mode)
    note.resize(1, 1)  # 끝까지 줄이기
    for _ in range(3):
        QGuiApplication.processEvents()
    assert note.width() >= note.minimumWidth() and note.height() >= note.minimumHeight()
    _assert_nothing_squeezed(note)
