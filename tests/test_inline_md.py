"""할 일 글자 꾸미기 (마크다운 일부)."""

import pytest

from sticky_todo.inline_md import has_format, plain, to_html, toggle_wrap


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("**굵게**", "<b>굵게</b>"),
        ("*기울임*", "<i>기울임</i>"),
        ("_기울임_", "<i>기울임</i>"),
        ("~~취소~~", "<s>취소</s>"),
        ("__밑줄__", "<u>밑줄</u>"),
        ("**중요** 회의", "<b>중요</b> 회의"),
        ("__중요__해요", "<u>중요</u>해요"),  # 한글 조사는 붙여 써도 된다
        ("*중요*해요", "<i>중요</i>해요"),
        ("***둘 다***", "<b><i>둘 다</i></b>"),
        ("**굵게 *안에 기울임***", "<b>굵게 <i>안에 기울임</i></b>"),
        ("*기울임 **안에 굵게** 끝*", "<i>기울임 <b>안에 굵게</b> 끝</i>"),
        ("a **b** c ~~d~~ e", "a <b>b</b> c <s>d</s> e"),
    ],
)
def test_to_html(text, html):
    assert to_html(text) == html


@pytest.mark.parametrize(
    "text",
    [
        "2 * 3 * 4",  # 공백에 붙은 기호
        "snake_case_name",  # 영문 단어 안의 _
        "my__var__name",
        "**안 닫힘",
        "*",
        "**",
        "****",
        "~~ 공백 ~~",
        "평범한 할 일",
    ],
)
def test_left_as_is(text):
    assert not has_format(text)
    assert plain(text) == text


def test_html_in_todo_is_never_interpreted():
    text = '<img src="C:/secret.png"> **<b>진짜</b>** & "따옴표"'
    out = to_html(text)
    assert "<img" not in out and "<b><b>" not in out
    assert out == (
        "&lt;img src=&quot;C:/secret.png&quot;&gt; "
        "<b>&lt;b&gt;진짜&lt;/b&gt;</b> &amp; &quot;따옴표&quot;"
    )
    assert to_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_backslash_escapes():
    assert to_html(r"\*별표\*") == "*별표*"
    assert plain(r"\*별표\* **굵게**") == "*별표* 굵게"
    assert has_format(r"\*별표\*")  # 기호를 빼야 하니 서식 처리 필요
    assert to_html(r"경로 C:\\temp") == r"경로 C:\temp"


def test_plain():
    assert plain("**중요** 회의 ~~취소~~ __밑줄__ *기울임*") == "중요 회의 취소 밑줄 기울임"
    assert plain("***둘 다***") == "둘 다"


@pytest.mark.parametrize(
    ("text", "sel", "marker", "expected"),
    [
        # 감싸기 (선택은 안쪽 글자 그대로)
        ("회의 준비", (0, 2), "**", ("**회의** 준비", 2, 4)),
        # 앞뒤 공백은 기호 밖으로
        ("회의 준비", (2, 5), "**", ("회의 **준비**", 5, 7)),
        # 기호 안쪽을 고른 채로 다시 누르면 풀기
        ("**회의** 준비", (2, 4), "**", ("회의 준비", 0, 2)),
        # 기호까지 고른 채로 누르면 풀기
        ("**회의** 준비", (0, 6), "**", ("회의 준비", 0, 2)),
        # 선택이 없으면 기호 한 쌍 + 커서 가운데
        ("회의", (2, 2), "*", ("회의**", 3, 3)),
        ("회의", (0, 0), "__", ("____회의", 2, 2)),
        # 거꾸로 고른 선택
        ("회의 준비", (2, 0), "__", ("__회의__ 준비", 2, 4)),
    ],
)
def test_toggle_wrap(text, sel, marker, expected):
    assert toggle_wrap(text, *sel, marker) == expected


# ------------------------------------------------------------ 화면 (실제 키 입력)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from sticky_todo.note_widget import NoteWidget  # noqa: E402
from sticky_todo.settings import Settings  # noqa: E402


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "settings.json")
    w.show()
    w.activateWindow()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def row_label(note, text):
    return next(r.label for r in note.list_view.rows if r.todo.text == text)


def test_note_rows_render_format_safely(note, store):
    store.add("**중요** 회의")
    store.add("평범한 일")
    store.add('<img src="C:/x.png"> __밑줄__')
    rich = row_label(note, "**중요** 회의")
    assert rich.textFormat() == Qt.TextFormat.RichText and rich.text() == "<b>중요</b> 회의"
    plain_label = row_label(note, "평범한 일")
    assert plain_label.textFormat() == Qt.TextFormat.PlainText
    evil = row_label(note, '<img src="C:/x.png"> __밑줄__')
    assert "<img" not in evil.text() and "&lt;img" in evil.text() and "<u>밑줄</u>" in evil.text()


def _type(widget, text):
    widget.setFocus()
    widget.insert(text)


def test_input_ctrl_b_i_u(note, store):
    inp = note.input
    _type(inp, "회의 준비")
    inp.setSelection(0, 2)
    QTest.keyClick(inp, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert inp.text() == "**회의** 준비" and inp.selectedText() == "회의"
    QTest.keyClick(inp, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)  # 다시 누르면 풀림
    assert inp.text() == "회의 준비"
    inp.setSelection(3, 2)
    QTest.keyClick(inp, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier)
    assert inp.text() == "회의 __준비__"
    QTest.keyClick(inp, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)  # 되돌리기
    assert inp.text() == "회의 준비"
    inp.end(False)
    QTest.keyClick(inp, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)  # 선택 없음
    assert inp.text() == "회의 준비**" and inp.cursorPosition() == len("회의 준비*")
    inp.clear()
    _type(inp, "**중요** 회의")
    QTest.keyClick(inp, Qt.Key.Key_Return)
    assert [t.text for t in store.todos()] == ["**중요** 회의"]  # 저장은 기호 그대로


def test_inline_editor_ctrl_i(note, store):
    store.add("회의 준비")
    row = note.list_view.rows[0]
    row.begin_edit()
    ed = row.editor
    ed.setSelection(0, 2)
    QTest.keyClick(ed, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
    assert ed.text() == "*회의* 준비"
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert store.todos()[0].text == "*회의* 준비"
    assert note.list_view.rows[0].label.text() == "<i>회의</i> 준비"


def test_manager_shows_plain_searches_and_edits(store, tmp_path):
    from sticky_todo.manager_window import COL_TEXT, HTML_ROLE, ManagerWindow

    store.add("**중요** 회의")
    a = store.add("상위")
    store.add("~~하위~~ 일", 1)
    w = ManagerWindow(store, Settings())
    w.show()
    try:
        texts = {w.model.index(r, COL_TEXT).data(): r for r in range(w.model.rowCount())}
        assert set(texts) == {"중요 회의", "상위", "하위 일"}
        r = texts["중요 회의"]
        idx = w.model.index(r, COL_TEXT)
        assert idx.data(Qt.ItemDataRole.EditRole) == "**중요** 회의"
        assert idx.data(HTML_ROLE) == "<b>중요</b> 회의"
        assert w.model.index(texts["상위"], COL_TEXT).data(HTML_ROLE) is None
        w.grab()  # 꾸민 글자 그리기가 죽지 않는지
        # 검색은 기호를 빼고
        w.search.setText("중요 회의")
        assert [t.text for t in w.model.rows] == ["**중요** 회의"]
        w.search.setText("")
        # 고치기: 실제 편집칸에서 Ctrl+B
        w.table.edit(idx)
        QGuiApplication.processEvents()
        ed = QApplication.focusWidget()
        assert isinstance(ed, QLineEdit) and ed.text() == "**중요** 회의"
        ed.setSelection(len("**중요** "), 2)
        QTest.keyClick(ed, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        assert ed.text() == "**중요** **회의**"
        QTest.keyClick(ed, Qt.Key.Key_Return)
        QGuiApplication.processEvents()
        assert any(t.text == "**중요** **회의**" for t in store.todos())
        assert a.id
    finally:
        w.close()
        w.deleteLater()
