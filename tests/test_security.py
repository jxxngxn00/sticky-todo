"""남이 준 마크다운 파일 등 신뢰할 수 없는 입력에 대한 방어."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from sticky_todo import markdown_io
from sticky_todo.manager_window import COL_TEXT, ManagerWindow
from sticky_todo.markdown_io import parse_markdown
from sticky_todo.note_widget import MAX_VISUAL_INDENT, NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.storage import MAX_TEXT_LEN

EVIL = '<h1 style="font-size:200px">큰글씨</h1><img src="file:///C:/Windows/win.ini">'


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def test_html_in_todo_is_shown_as_plain_text(note, store):
    store.add_many(parse_markdown(f"- [ ] {EVIL}\n"))
    label = note.list_view.rows[0].label
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == EVIL  # 글자 그대로
    # 큰 글씨·이미지로 그려지지 않음 (고치기 전에는 3840x2437로 그려졌음)
    assert label.sizeHint().height() < 400


def test_toast_is_plain_text(note):
    note.show_toast("<b>x</b>")
    assert note.toast_label.textFormat() == Qt.TextFormat.PlainText


def test_manager_tooltip_is_escaped(store):
    store.add(EVIL)
    win = ManagerWindow(store, Settings())
    tip = win.model.index(0, COL_TEXT).data(Qt.ItemDataRole.ToolTipRole)
    assert "<img" not in tip and "&lt;img" in tip
    win.close()


def test_long_text_is_truncated(store):
    t = store.add("가" * 10_000)
    assert len(t.text) == MAX_TEXT_LEN
    store.add_many(parse_markdown("- [ ] " + "나" * 10_000))
    assert len(store.todos()[-1].text) == MAX_TEXT_LEN


def test_deep_indent_is_capped_on_screen(note, store):
    md = "".join(f"{'  ' * i}- [ ] {i}\n" for i in range(30))
    store.add_many(parse_markdown(md))
    margins = [r.layout().contentsMargins().left() for r in note.list_view.rows]
    assert max(margins) == margins[MAX_VISUAL_INDENT]  # 일정 깊이 이후로는 더 안 밀림
    assert store.todos()[-1].indent == 29  # 데이터 깊이는 그대로


def test_import_asks_and_limits(note, monkeypatch):
    many = [(f"할 일 {i}", False, 0) for i in range(markdown_io.MAX_IMPORT_ITEMS + 50)]
    answers = []

    def ask(*_args, **_kw):
        answers.append(1)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", ask)
    assert note.confirm_import(many) == []  # 아니오 → 아무것도 안 불러옴
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert len(note.confirm_import(many)) == markdown_io.MAX_IMPORT_ITEMS
    few = many[: markdown_io.CONFIRM_IMPORT_ITEMS]
    assert note.confirm_import(few) == few  # 적으면 묻지 않음
    assert len(answers) == 1


def test_import_file_size_limit(tmp_path):
    big = tmp_path / "big.md"
    big.write_bytes(b"- [ ] x\n" * (markdown_io.MAX_IMPORT_BYTES // 8 + 10))
    with pytest.raises(ValueError):
        markdown_io.read_markdown_file(big)
