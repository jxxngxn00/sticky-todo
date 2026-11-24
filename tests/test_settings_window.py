import json

import pytest
from PySide6.QtGui import QFont

from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import PRESETS, Settings
from sticky_todo.settings_window import SettingsWindow


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


@pytest.fixture
def win(note):
    w = SettingsWindow(note)
    w.show()
    return w


def saved(tmp_path):
    p = tmp_path / "settings.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def click_radio(win, key, value):
    group, values = win._radios[key]
    group.button(values.index(value)).click()


def test_loads_current_values(note, win):
    group, values = win._radios["check_style"]
    assert values[group.checkedId()] == "square"
    assert win.font_size.value() == 12
    assert win.sticky_group.isVisibleTo(win)


def test_live_preview_and_ok_saves(note, win, tmp_path):
    win.font_size.setValue(16)
    click_radio(win, "check_style", "circle")
    assert note.settings["font_size"] == 16
    assert "16pt" in note.card.styleSheet()  # 노트에 즉시 반영
    assert note.list_view.style_opts["check_style"] == "circle"
    assert saved(tmp_path) is None  # 확인 전에는 저장 안 함
    win.ok_btn.click()
    assert saved(tmp_path)["font_size"] == 16


def test_cancel_restores(note, win):
    before = note.settings.as_dict()
    win.font_size.setValue(18)
    win.apply_preset("pink")
    click_radio(win, "done_style", "hide")
    win.cancel_btn.click()
    assert note.settings.as_dict() == before
    assert PRESETS["yellow"]["bg_color"].upper() in note.card.styleSheet()


def test_preset_updates_color_buttons(note, win):
    win.apply_preset("sky")
    assert win.color_buttons["bg_color"].color() == PRESETS["sky"]["bg_color"].upper()
    assert note.settings["title_color"] == PRESETS["sky"]["title_color"].upper()


def test_reset_defaults_keeps_window_and_title(note, win):
    note.settings.set("title", "업무")
    note.settings.set("window_w", 500)
    win.font_size.setValue(20)
    click_radio(win, "done_style", "hide")
    win.reset_defaults()
    assert note.settings["font_size"] == 12
    assert note.settings["done_style"] == "strike"
    assert note.settings["title"] == "업무"
    assert note.settings["window_w"] == 500
    assert win.font_size.value() == 12


def test_done_hint(win):
    click_radio(win, "done_style", "bottom")
    assert "드래그" in win.done_hint.text()
    click_radio(win, "done_style", "strike")
    assert not win.done_hint.isVisibleTo(win)


def test_font_family(note, win):
    # offscreen 테스트 환경엔 한글 글꼴이 없어서 선택 신호를 직접 보낸다
    win.font_combo.currentFontChanged.emit(QFont("Arial"))
    assert note.settings["font_family"] == "Arial"


def test_menu_opens_single_settings_window(note):
    note.open_settings_window()
    first = note._settings_win
    note.open_settings_window()
    assert note._settings_win is first
    first.reject()
