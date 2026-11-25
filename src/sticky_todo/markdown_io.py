"""마크다운 체크리스트 내보내기/불러오기.

불러온 내용은 텍스트로만 다루고 절대 실행하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from .fileio import atomic_write_text
from .storage import Todo

INDENT_UNIT = "  "  # 내보낼 때 한 단계당 공백 2칸
TAB_WIDTH = 4
MAX_IMPORT_BYTES = 1 * 1024 * 1024
MAX_IMPORT_ITEMS = 200  # 한 번에 불러오는 최대 개수 (많으면 화면이 느려짐)
CONFIRM_IMPORT_ITEMS = 50  # 이보다 많으면 먼저 물어본다
DEFAULT_TITLE = "오늘 할 일"

# - [ ] / - [x] / - [X] / * [ ] / * [x]
_CHECK_LINE = re.compile(r"^(?P<lead>[ \t]*)[-*][ \t]+\[(?P<mark>[ xX])\][ \t]+(?P<text>.*)$")

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ---------------------------------------------------------------- 내보내기


def export_markdown(
    todos: Iterable[Todo],
    title: str = DEFAULT_TITLE,
    today: date | None = None,
    keep_indent: bool = True,
) -> str:
    """keep_indent=False면 계층 없이 평평하게 (관리 창의 선택 내보내기 등)."""
    today = today or date.today()
    title = " ".join(title.split()) or DEFAULT_TITLE
    lines = [f"# {title}", f"_{today.isoformat()} 내보냄_", ""]
    for t in todos:
        level = t.indent if keep_indent else 0
        mark = "x" if t.done else " "
        text = " ".join(t.text.split())
        lines.append(f"{INDENT_UNIT * level}- [{mark}] {text}")
    return "\n".join(lines) + "\n"


def export_sections(
    sections: Iterable[tuple[str, Iterable[Todo]]],
    title: str = DEFAULT_TITLE,
    today: date | None = None,
) -> str:
    """여러 날을 한 파일로: 날짜마다 '## 소제목' 아래 체크리스트 (주간·월간 내보내기).

    불러오기는 체크리스트 줄만 읽으므로 소제목이 있어도 그대로 다시 불러올 수 있다.
    """
    today = today or date.today()
    title = " ".join(title.split()) or DEFAULT_TITLE
    lines = [f"# {title}", f"_{today.isoformat()} 내보냄_"]
    for heading, todos in sections:
        lines += ["", f"## {' '.join(heading.split())}"]
        for t in todos:
            mark = "x" if t.done else " "
            lines.append(f"{INDENT_UNIT * t.indent}- [{mark}] {' '.join(t.text.split())}")
    return "\n".join(lines) + "\n"


def sanitize_filename(name: str, fallback: str = "할 일") -> str:
    name = _FORBIDDEN.sub("_", name)
    name = " ".join(name.split()).rstrip(". ")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    return name[:150]


def default_filename(
    title: str = DEFAULT_TITLE, today: date | None = None, ext: str = ".txt"
) -> str:
    """메모장으로 바로 열리게 기본은 .txt (내용은 '- [ ] 할 일' 체크리스트)."""
    today = today or date.today()
    return f"{sanitize_filename(title)} {today.isoformat()}{ext}"


def save_markdown(path: Path, text: str) -> None:
    atomic_write_text(Path(path), text)


# ---------------------------------------------------------------- 불러오기


def parse_markdown(text: str) -> list[tuple[str, bool, int]]:
    """체크리스트 줄만 (text, done, indent)로 뽑는다. 그 외 줄은 무시.

    들여쓰기는 앞쪽 공백 너비를 상대적으로 비교해서 단계로 바꾼다.
    (2칸이든 4칸이든 탭이든 상관없음)
    """
    result: list[tuple[str, bool, int]] = []
    stack: list[int] = []  # 조상들의 공백 너비
    for raw in text.splitlines():
        m = _CHECK_LINE.match(raw)
        if not m:
            continue
        item = " ".join(m["text"].split())
        if not item:
            continue
        width = len(m["lead"].expandtabs(TAB_WIDTH))
        while stack and stack[-1] >= width:
            stack.pop()
        level = len(stack)
        level = min(level, result[-1][2] + 1) if result else 0
        stack.append(width)
        result.append((item, m["mark"] in "xX", level))
    return result


def read_markdown_file(path: Path) -> list[tuple[str, bool, int]]:
    path = Path(path)
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("파일이 너무 커요 (1MB 초과)")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp949", errors="replace")  # 예전 메모장 ANSI 저장 대비
    return parse_markdown(text)
