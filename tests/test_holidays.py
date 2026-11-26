"""대한민국 공휴일 표와 화면 표시."""

from datetime import date, timedelta

import pytest
from PySide6.QtGui import QGuiApplication

from sticky_todo.holidays import FIRST_YEAR, HOLIDAYS, LAST_YEAR, holiday
from sticky_todo.note_widget import (
    HOLIDAY_RED,
    HOLIDAY_TINT_ALPHA,
    NoteWidget,
    _mix,
    contrast,
    holiday_text_color,
)
from sticky_todo.settings import PRESETS, Settings

# ------------------------------------------------------------ 표 자체

# 토·일과 겹치면 다음 평일이 대체공휴일 (공휴일에 관한 법률, 2026 개정 포함)
WEEKEND_SUBSTITUTED = {
    "3·1절", "노동절", "어린이날", "부처님오신날", "제헌절",
    "광복절", "개천절", "한글날", "기독탄신일",
}  # fmt: skip


def _d(s: str) -> date:
    return date.fromisoformat(s)


def test_dates_are_valid_and_in_range():
    for day in HOLIDAYS:
        assert _d(day).isoformat() == day
        assert FIRST_YEAR <= _d(day).year <= LAST_YEAR


def test_substitutes_fall_on_weekdays_and_are_not_other_holidays():
    for day, h in HOLIDAYS.items():
        if h.name == "대체공휴일":
            assert _d(day).weekday() < 5, day


def _next_free_weekday(d: date) -> date:
    d += timedelta(days=1)
    while (
        d.weekday() >= 5
        or d.isoformat() in HOLIDAYS
        and HOLIDAYS[d.isoformat()].name != "대체공휴일"
    ):
        d += timedelta(days=1)
    return d


def test_every_weekend_overlap_has_its_substitute():
    for day, h in HOLIDAYS.items():
        d = _d(day)
        if h.name in WEEKEND_SUBSTITUTED and d.weekday() >= 5:
            sub = _next_free_weekday(d).isoformat()
            assert HOLIDAYS.get(sub) and HOLIDAYS[sub].name == "대체공휴일", (day, h)


def test_seollal_chuseok_sunday_rule():
    """설날·추석은 일요일(또는 다른 공휴일)과 겹칠 때만 대체공휴일."""
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for name in ("설날", "추석"):
            days = sorted(
                d for d, h in HOLIDAYS.items() if d.startswith(str(year)) and h.name == name
            )
            assert len(days) == 3, (year, name)
            last = _d(days[-1])
            after = (last + timedelta(days=1)).isoformat()
            overlap = any(_d(x).weekday() == 6 for x in days) or (
                f"{year}-10-03" in days  # 2028 추석·개천절
            )
            assert (
                HOLIDAYS.get(after) is not None and HOLIDAYS[after].name == "대체공휴일"
            ) == overlap


def test_official_spot_checks():
    # 2027년 월력요항: 대체공휴일 7일
    subs_2027 = [d for d, h in HOLIDAYS.items() if d.startswith("2027") and h.name == "대체공휴일"]
    assert len(subs_2027) == 7
    assert holiday("2026-05-01").name == "노동절"  # 2026. 5. 1. 시행
    assert holiday("2026-06-03").full == "전국동시지방선거"
    assert holiday("2027-07-19").full == "대체공휴일(제헌절)"
    assert holiday("2028-01-27").name == "설날"  # 한국은 1/27 (중국은 1/26)
    assert holiday("2028-04-12").full == "국회의원선거일"
    assert holiday("2028-10-05").name == "대체공휴일"
    assert holiday("2026-06-06").name == "현충일" and holiday("2026-06-08") is None  # 대체 없음
    assert holiday("2026-11-26") is None
    assert holiday("2025-12-25") is None and holiday("2029-01-01") is None  # 표 밖은 표시 안 함


@pytest.mark.parametrize("preset", list(PRESETS))
def test_badge_text_is_legible(preset):
    p = PRESETS[preset]
    fg = holiday_text_color(p["text_color"], p["bg_color"], p["title_color"])
    for bg in (p["bg_color"], p["title_color"]):
        assert contrast(fg, _mix(HOLIDAY_RED, bg, HOLIDAY_TINT_ALPHA)) >= 4.5


def test_badge_falls_back_to_text_on_dark_background():
    assert holiday_text_color("#FFFFFF", "#202020", "#202020") == "#FFFFFF"


# ------------------------------------------------------------ 화면


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def settle():
    for _ in range(3):
        QGuiApplication.processEvents()


def test_day_view_badge(note, store):
    store.set_day("2026-09-25")
    note.set_view_mode("day")
    pill = note.title_bar.holiday_pill
    assert pill.isVisible() and pill.text() == "추석"
    store.set_day("2026-05-25")
    assert pill.text() == "대체공휴일" and pill.toolTip() == "대체공휴일(부처님오신날)"
    store.set_day("2026-09-28")
    assert not pill.isVisible()
    store.set_day("2026-09-25")
    note.set_view_mode("week")  # 주간·월간에선 머리줄·달력이 보여 주므로 제목 배지는 숨김
    assert not pill.isVisible()


def test_week_list_headers(note, store):
    store.set_day("2026-09-23")
    note.set_view_mode("week")
    note.resize(320, 600)
    settle()
    headers = {s.day: s.header for s in note.list_view._sections}
    chuseok = headers["2026-09-25"]
    assert chuseok.holiday_pill.isVisible() and chuseok.holiday_pill.text() == "추석"
    assert chuseok.label.property("red") is True
    plain = headers["2026-09-23"]
    assert not plain.holiday_pill.isVisible() and plain.label.property("red") is False
    assert headers["2026-09-20"].label.property("red") is True  # 일요일도 달력처럼 빨강


def test_board_headers(note, store):
    store.set_day("2026-10-05")
    note.set_view_mode("week")
    note.resize(1000, 460)
    settle()
    assert note.board_active
    pills = {h.day: h.holiday_pill for h in note.board.headers}
    sub = pills["2026-10-05"]
    assert sub.isVisible() and sub.full == "대체공휴일" and sub.toolTip() == "대체공휴일(개천절)"
    assert _shown_or_shortened(sub)
    assert not pills["2026-10-06"].isVisible()


def _shown_or_shortened(pill) -> bool:
    """좁은 보드 칸에서는 '대체…'처럼 줄 수 있지만, 앞 두 글자는 늘 보인다."""
    t = pill.text()
    return t == pill.full or (t.endswith("…") and t.startswith(pill.full[:2]))


@pytest.mark.parametrize("day", ["2026-05-25", "2026-09-25"])
def test_board_badges_do_not_widen_note_at_default_font(note, store, day):
    """기본 글자 크기에서는 공휴일 배지 때문에 보드가 960px보다 넓어지지 않는다."""
    store.set_day(day)
    note.set_view_mode("week")
    note.resize(960, 420)
    settle()
    assert note.board_active and note.width() == 960
    for h in note.board.headers:
        p = h.holiday_pill
        if p.isVisible():
            assert _shown_or_shortened(p)
            assert p.geometry().right() < h.count.geometry().left() or not h.count.text()


def test_month_calendar_marks_holidays(note, store):
    store.set_day("2026-09-01")
    note.set_view_mode("month")
    cells = {c.day: c for c in note.calendar.cells}
    assert cells["2026-09-25"].holiday and "추석" in cells["2026-09-25"].tip
    assert not cells["2026-09-23"].holiday


@pytest.mark.parametrize("font_size", [12, 20])
def test_badge_never_clipped_when_narrow(note, store, font_size):
    note.settings.set("font_size", font_size)
    note.apply_settings()
    store.set_day("2026-05-24")  # 가장 긴 이름 '부처님오신날'
    note.set_view_mode("day")
    note.resize(1, 1)
    settle()
    tb = note.title_bar
    pill = tb.holiday_pill
    assert pill.width() >= pill.sizeHint().width()
    assert tb.sub.geometry().right() < pill.geometry().left()
    assert pill.geometry().right() < tb.next_btn.geometry().left()
    assert tb.next_btn.geometry().right() < tb.pin_btn.geometry().left()

    note.set_view_mode("week")
    note.resize(1, 1)
    settle()
    header = next(s.header for s in note.list_view._sections if s.day == "2026-05-24")
    p = header.holiday_pill
    assert _shown_or_shortened(p)
    assert p.geometry().right() < header.count.geometry().left() or not header.count.text()
