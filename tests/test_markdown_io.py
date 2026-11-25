from datetime import date

import pytest

from sticky_todo.markdown_io import (
    default_filename,
    export_markdown,
    parse_markdown,
    read_markdown_file,
    sanitize_filename,
    save_markdown,
)
from sticky_todo.storage import Todo


def _t(text, done=False, indent=0):
    return Todo(id=text, text=text, done=done, indent=indent)


def test_export_format():
    md = export_markdown(
        [_t("기획서 초안 보내기", True), _t("PR 리뷰 2건"), _t("프론트", indent=1)],
        today=date(2026, 11, 24),
    )
    assert md == (
        "# 오늘 할 일\n"
        "_2026-11-24 내보냄_\n"
        "\n"
        "- [x] 기획서 초안 보내기\n"
        "- [ ] PR 리뷰 2건\n"
        "  - [ ] 프론트\n"
    )


def test_export_flat():
    md = export_markdown([_t("a", indent=0), _t("b", indent=1)], keep_indent=False)
    assert "\n- [ ] b\n" in md


def test_parse_accepted_forms_and_ignores_others():
    text = """# 제목
일반 문단
- [ ] 하나
- [x] 둘
* [ ] 셋
- [X] 넷
* [x] 다섯
- 그냥 불릿
-[ ] 붙여쓴 거
- [ ]
1. [ ] 번호
<script>alert(1)</script>
- [ ] <b>태그도 그냥 글자</b>
"""
    assert parse_markdown(text) == [
        ("하나", False, 0),
        ("둘", True, 0),
        ("셋", False, 0),
        ("넷", True, 0),
        ("다섯", True, 0),
        ("<b>태그도 그냥 글자</b>", False, 0),
    ]


@pytest.mark.parametrize("unit", ["  ", "    ", "\t"])
def test_parse_indent_any_width(unit):
    text = f"- [ ] A\n{unit}- [ ] B\n{unit * 2}- [x] C\n{unit}- [ ] D\n- [ ] E\n"
    assert [(t, i) for t, _, i in parse_markdown(text)] == [
        ("A", 0),
        ("B", 1),
        ("C", 2),
        ("D", 1),
        ("E", 0),
    ]


def test_parse_indent_irregular():
    # 첫 줄이 들여써져 있어도 0, 갑자기 깊어져도 한 단계씩만
    text = "   - [ ] A\n- [ ] B\n        - [ ] C\n   - [ ] D\n"
    assert [(t, i) for t, _, i in parse_markdown(text)] == [("A", 0), ("B", 0), ("C", 1), ("D", 1)]


def test_roundtrip_preserves_tree():
    todos = [_t("A"), _t("B", True, 1), _t("C", False, 2), _t("D", False, 1), _t("E")]
    parsed = parse_markdown(export_markdown(todos))
    assert parsed == [(t.text, t.done, t.indent) for t in todos]


def test_sanitize_filename():
    assert sanitize_filename('a\\b/c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("  할 일...  ") == "할 일"
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("con.md") == "_con.md"
    assert sanitize_filename("???") == "___"
    assert sanitize_filename("") == "할 일"
    assert len(sanitize_filename("가" * 500)) == 150


def test_default_filename():
    assert default_filename("오늘: 할 일", date(2026, 11, 24)) == "오늘_ 할 일 2026-11-24.txt"
    assert default_filename("할 일", date(2026, 11, 24), ".md") == "할 일 2026-11-24.md"


def test_file_roundtrip_utf8_and_cp949(tmp_path):
    p = tmp_path / "out" / "todo.md"
    save_markdown(p, export_markdown([_t("한글")]))
    assert read_markdown_file(p) == [("한글", False, 0)]

    legacy = tmp_path / "legacy.md"
    legacy.write_bytes("- [x] 메모장 ANSI\n".encode("cp949"))
    assert read_markdown_file(legacy) == [("메모장 ANSI", True, 0)]

    bom = tmp_path / "bom.md"
    bom.write_bytes("- [ ] 비오엠\n".encode("utf-8-sig"))
    assert read_markdown_file(bom) == [("비오엠", False, 0)]


def test_import_into_store(store):
    store.add("기존")
    n = store.add_many(parse_markdown("- [ ] A\n  - [x] B\n"))
    assert n == 2
    todos = store.todos()
    assert [(t.text, t.done, t.indent) for t in todos] == [
        ("기존", False, 0),
        ("A", False, 0),
        ("B", True, 1),
    ]
    assert todos[2].completed_at is not None
