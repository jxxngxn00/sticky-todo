"""할 일 글자의 간단한 꾸미기 (마크다운 일부).

    **굵게**   *기울임* / _기울임_   ~~취소선~~   __밑줄__

- 저장은 쓴 글자 그대로. 화면에 그릴 때만 꾸민다 (텍스트 파일로 내보내면 기호 그대로).
- __밑줄__은 표준 마크다운에선 굵게지만, 이 앱에서는 밑줄로 쓴다.
- 짝이 안 맞는 기호는 그냥 글자로 보인다. \\* 처럼 앞에 \\를 붙이면 기호 그대로.
- 할 일에 쓴 HTML(<b> 등)은 해석하지 않고 전부 글자로 바꾼다 (앱이 만든 태그만 쓴다).
  QLabel 서식 글자는 <img src=…>로 PC 파일을 읽을 수도 있어서 꼭 필요하다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# 기호 → (HTML 태그, 단축키에서 감쌀 기호)
TAGS = {"**": "b", "__": "u", "~~": "s", "*": "i", "_": "i"}
WRAP = {"bold": "**", "italic": "*", "underline": "__"}

# 여는 기호 바로 뒤, 닫는 기호 바로 앞은 공백이 아니어야 한다 ('2 * 3 * 4'는 그대로).
# '_'·'__'는 영문·숫자 단어 안(snake_case)에서는 꾸미지 않는다.
# 한글은 붙여 써도 된다: '__중요__해요' → 밑줄 '중요' + '해요'.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("**", re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")),  # ***x***: 바깥이 굵게
    ("__", re.compile(r"(?<![A-Za-z0-9])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9])")),
    ("~~", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")),
    ("*", re.compile(r"(?<!\*)\*(?![\s*])(.+?)(?<=[^\s*])\*(?!\*)")),
    ("_", re.compile(r"(?<![A-Za-z0-9_])_(?![\s_])(.+?)(?<=[^\s_])_(?![A-Za-z0-9_])")),
]
_ESCAPABLE = "\\*_~"
# 이스케이프한 기호를 잠시 맡겨 두는 자리 (유니코드 사용자 영역, 할 일에 쓸 일 없는 글자)
_HOLD = {c: chr(0xE000 + i) for i, c in enumerate(_ESCAPABLE)}
_RELEASE = {v: k for k, v in _HOLD.items()}
_ESCAPE = re.compile(r"\\([\\*_~])")


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    marker: str
    inner: str


def _hold(text: str) -> str:
    return _ESCAPE.sub(lambda m: _HOLD[m[1]], text)


def _release(text: str) -> str:
    return "".join(_RELEASE.get(c, c) for c in text)


def _first_span(text: str, pos: int) -> _Span | None:
    """pos 뒤에서 가장 먼저 시작하는 꾸밈 (같은 자리면 긴 기호 먼저)."""
    best: _Span | None = None
    for marker, pat in _PATTERNS:
        m = pat.search(text, pos)
        if m is None:
            continue
        span = _Span(m.start(), m.end(), marker, m[1])
        if best is None or span.start < best.start:
            best = span
    return best


def _render(text: str, depth: int = 0) -> str:
    out: list[str] = []
    pos = 0
    while depth < 4:  # 겹친 꾸밈은 몇 단계까지만
        span = _first_span(text, pos)
        if span is None:
            break
        out.append(html.escape(_release(text[pos : span.start])))
        tag = TAGS[span.marker]
        out.append(f"<{tag}>{_render(span.inner, depth + 1)}</{tag}>")
        pos = span.end
    out.append(html.escape(_release(text[pos:])))
    return "".join(out)


def _strip(text: str, depth: int = 0) -> str:
    out: list[str] = []
    pos = 0
    while depth < 4:
        span = _first_span(text, pos)
        if span is None:
            break
        out.append(_release(text[pos : span.start]))
        out.append(_strip(span.inner, depth + 1))
        pos = span.end
    out.append(_release(text[pos:]))
    return "".join(out)


def has_format(text: str) -> bool:
    """꾸밈이나 이스케이프가 하나라도 있으면 True (없으면 글자 그대로 그려도 된다)."""
    held = _hold(text)
    return held != text or _first_span(held, 0) is not None


def to_html(text: str) -> str:
    """화면용 HTML. 할 일 글자는 모두 이스케이프되고 b·i·u·s 태그만 남는다."""
    return _render(_hold(text))


def plain(text: str) -> str:
    """기호를 뺀 글자 (검색·정렬·툴팁·알림용). '**중요** 회의' → '중요 회의'."""
    return _strip(_hold(text))


def toggle_wrap(text: str, start: int, end: int, marker: str) -> tuple[str, int, int]:
    """[start, end) 선택을 marker로 감싸거나, 이미 감싸져 있으면 푼다.

    선택이 없으면 기호 한 쌍을 넣고 커서를 그 사이에 둔다.
    돌려주는 값: (새 글자, 새 선택 시작, 새 선택 끝).
    """
    n = len(marker)
    if start > end:
        start, end = end, start
    inside = text[start:end]
    # 1) 선택 바깥에 기호가 붙어 있으면 푼다: **[굵게]**
    if text[max(0, start - n) : start] == marker and text[end : end + n] == marker:
        return text[: start - n] + inside + text[end + n :], start - n, end - n
    # 2) 선택에 기호까지 들어 있으면 푼다: [**굵게**]
    if len(inside) >= 2 * n and inside.startswith(marker) and inside.endswith(marker):
        core = inside[n:-n]
        return text[:start] + core + text[end:], start, start + len(core)
    # 3) 감싼다. 앞뒤 공백은 기호 밖으로 (공백에 붙은 기호는 꾸밈으로 안 읽힌다)
    lead = len(inside) - len(inside.lstrip())
    trail = len(inside) - len(inside.rstrip())
    s, e = start + lead, end - trail
    if s >= e:  # 선택이 없거나 공백뿐: 기호 한 쌍을 넣고 커서를 가운데에
        return text[:start] + marker + marker + text[start:], start + n, start + n
    new = text[:s] + marker + text[s:e] + marker + text[e:]
    return new, s + n, e + n
