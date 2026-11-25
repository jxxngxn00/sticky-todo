"""바탕화면 스티키 노트 위젯."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, markdown_io
from .autostart import Autostart
from .month_view import Cell, MonthCalendar
from .paths import default_export_dir
from .settings import PRESETS, Settings
from .storage import Todo, TodoStore, today_str

INDENT_PX = 20
MIME_TODO = "application/x-sticky-todo-id"
FALLBACK_FONTS = ["Pretendard", "맑은 고딕", "Malgun Gothic"]
PRESET_LABELS = {"yellow": "노랑", "pink": "분홍", "sky": "하늘", "mint": "민트", "ios": "iOS"}
TOAST_MS = 10_000  # 실수로 지웠을 때 되돌릴 시간
# 기본 진하기. 배경 대비가 모자라면 soft_alpha()/ring_alpha()가 더 진하게 올린다
# (WCAG: 보조 글자 4.5:1, 아이콘·테두리 3:1)
SOFT_TEXT_ALPHA = 0.66
EMPTY_TIPS = (
    "할 일이 없어요\n\n"
    "• 아래 칸에 적고 Enter\n"
    "• 더블클릭: 고치기 · 우클릭: 더보기\n"
    "• Tab: 하위 할 일로\n"
    "• Ctrl+Alt+T: 어디서든 노트 열기\n"
    "• F1: 도움말 · 단축키"
)
RING_ALPHA = 0.52
DONE_TEXT_ALPHA = 0.55  # 완료 항목(취소선과 함께)
INPUT_TINT_ALPHA = 0.12  # 입력칸 회색 깔기
SHADOW_PX = 14  # 그림자 여백 (그림자를 꺼도 유지)
SHADOW_OFFSET_Y = 3  # 그림자를 살짝 아래로
SHADOW_MAX_ALPHA = 0.14  # 노트 가장자리 바로 바깥의 진하기


def _rgba(color: str, alpha: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


MAX_VISUAL_INDENT = 8  # 이보다 깊은 항목도 화면에서는 이만큼만 밀어서 보여준다


def visual_indent(level: int) -> int:
    """아주 깊게 들여쓴 파일을 불러와도 줄 폭이 끝없이 넓어져 멈추지 않게."""
    return min(level, MAX_VISUAL_INDENT)


def _mix(a: str, b: str, t: float) -> str:
    """a와 b를 t 비율로 섞은 불투명 색 (아이콘처럼 알파를 못 쓰는 곳용)."""
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red() * t + cb.red() * (1 - t))
    g = round(ca.green() * t + cb.green() * (1 - t))
    bl = round(ca.blue() * t + cb.blue() * (1 - t))
    return f"#{r:02X}{g:02X}{bl:02X}"


def _luminance(color: str) -> float:
    c = QColor(color)
    out = []
    for v in (c.redF(), c.greenF(), c.blueF()):
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(a: str, b: str) -> float:
    """WCAG 대비 (1~21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def legible_alpha(text: str, bg: str, base: float, ratio: float) -> float:
    """글자색을 base만큼 옅게 하되, 배경 위에서 ratio 대비가 안 나오면 그만큼 진하게.

    노랑 같은 밝은 배경은 base 그대로, 회색(iOS 프리셋)이나 사용자가 고른 색은 자동으로 진해진다.
    """
    a = base
    while a < 1.0 and contrast(_mix(text, bg, a), bg) < ratio:
        a = round(a + 0.01, 2)
    return min(a, 1.0)


def soft_alpha(text: str, bg: str) -> float:
    """보조 글자(날짜·개수·안내 문구): 4.5:1 이상."""
    return legible_alpha(text, bg, SOFT_TEXT_ALPHA, 4.5)


def ring_alpha(text: str, bg: str) -> float:
    """빈 체크 동그라미·아이콘: 3:1 이상."""
    return legible_alpha(text, bg, RING_ALPHA, 3.0)


def resolve_font_family(name: str) -> str:
    """설치된 글꼴이면 그대로, 아니면 Pretendard → 맑은 고딕 순으로 대체."""
    families = set(QFontDatabase.families())
    for candidate in [name, *FALLBACK_FONTS]:
        if candidate in families:
            return candidate
    return FALLBACK_FONTS[-1]


# ---------------------------------------------------------------- 날짜 표시

WEEKDAYS = "월화수목금토일"


def day_label(day: str, today: date | None = None, *, short: bool = False) -> str:
    """'오늘 · 11월 24일 (월)' / '내일 · …' / '11월 27일 (목)'. short면 '오늘', '11/27 (목)'."""
    d = date.fromisoformat(day)
    today = today or date.today()
    rel = {0: "오늘", 1: "내일", -1: "어제"}.get((d - today).days)
    wd = WEEKDAYS[d.weekday()]
    if short:
        return rel or (f"{d.month}/{d.day} ({wd})" if d.year == today.year else d.isoformat())
    base = f"{d.month}월 {d.day}일 ({wd})"
    if d.year != today.year:
        base = f"{d.year}년 {base}"
    return f"{rel} · {base}" if rel else base


def day_title(day: str, today: date | None = None) -> tuple[str, str]:
    """노트 제목 두 줄: ('오늘', '11월 24일 (화)') / ('11월 27일', '목요일').

    부제는 좁은 노트에서도 잘리지 않게 요일을 짧게 쓴다.
    """
    d = date.fromisoformat(day)
    today = today or date.today()
    rel = {0: "오늘", 1: "내일", -1: "어제"}.get((d - today).days)
    wd = f"{WEEKDAYS[d.weekday()]}요일"
    md = f"{d.month}월 {d.day}일"
    year = f"{d.year}년 " if d.year != today.year else ""
    if rel:
        return rel, f"{year}{md} ({WEEKDAYS[d.weekday()]})"
    return md, f"{year}{wd}".strip()


def _plain_day(day: str) -> str:
    """'11월 24일 (월)' (오늘·내일 같은 말 없이, 다른 해면 연도 붙음)."""
    return day_label(day).split(" · ")[-1]


def _shift_day(day: str, delta: int) -> str:
    return date.fromordinal(date.fromisoformat(day).toordinal() + delta).isoformat()


# ---------------------------------------------------------------- 주간 보기 (일요일 시작)

VIEW_NAMES = {"day": "일", "week": "주", "month": "월"}
VIEW_STEP = {"day": 1, "week": 7}  # ‹ › 한 번에 넘기는 날 수 (월간은 shift_month)
VIEW_UNIT = {"day": "날짜", "week": "주", "month": "달"}


def week_start(day: str) -> str:
    """day가 속한 주의 일요일."""
    d = date.fromisoformat(day)
    return _shift_day(day, -((d.weekday() + 1) % 7))


def week_days(day: str) -> list[str]:
    """day가 속한 주의 일요일~토요일."""
    start = week_start(day)
    return [_shift_day(start, i) for i in range(7)]


def week_title(day: str, today: date | None = None) -> tuple[str, str]:
    """주간 제목 두 줄: ('이번 주', '11월 22일 – 28일') / ('12월 6일 주', '12월 6일 – 12일')."""
    today = today or date.today()
    start = date.fromisoformat(week_start(day))
    end = date.fromordinal(start.toordinal() + 6)
    this = date.fromisoformat(week_start(today.isoformat()))
    rel = {0: "이번 주", 7: "다음 주", -7: "지난주"}.get((start - this).days)
    year = f"{start.year}년 " if start.year != today.year or end.year != today.year else ""
    if end.year != start.year:  # 해를 넘는 주
        tail = f"{end.year}년 {end.month}월 {end.day}일"
    elif end.month != start.month:
        tail = f"{end.month}월 {end.day}일"
    else:
        tail = f"{end.day}일"
    span = f"{year}{start.month}월 {start.day}일 – {tail}"
    return rel or f"{start.month}월 {start.day}일 주", span


# ---------------------------------------------------------------- 월간 보기


def shift_month(day: str, delta: int) -> str:
    """delta달 앞/뒤의 같은 날 (그 달에 없는 날이면 말일: 1월 31일 + 1달 = 2월 28일)."""
    d = date.fromisoformat(day)
    idx = d.year * 12 + d.month - 1 + delta
    year, month = divmod(idx, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last)).isoformat()


def month_days(day: str) -> list[str]:
    """day가 속한 달의 1일 ~ 말일."""
    d = date.fromisoformat(day)
    last = calendar.monthrange(d.year, d.month)[1]
    return [date(d.year, d.month, i).isoformat() for i in range(1, last + 1)]


def month_grid(day: str) -> list[str]:
    """달력에 그릴 날짜들: 1일이 있는 주의 일요일부터 말일이 있는 주의 토요일까지 (5~6주)."""
    days = month_days(day)
    start = week_start(days[0])
    end = _shift_day(week_start(days[-1]), 6)
    n = date.fromisoformat(end).toordinal() - date.fromisoformat(start).toordinal() + 1
    return [_shift_day(start, i) for i in range(n)]


def month_title(day: str, today: date | None = None) -> tuple[str, str]:
    """월간 제목 두 줄: ('11월', '2026년')."""
    d = date.fromisoformat(day)
    return f"{d.month}월", f"{d.year}년"


# ---------------------------------------------------------------- 드래그 위치 계산


def compute_drop(
    entries: list[tuple[str, int]], dragged_id: str, index: int, desired: int
) -> tuple[str | None, int]:
    """드래그한 묶음을 index 자리(그 행 앞)에 놓을 때 (before_id, indent)를 정한다.

    entries: 화면 순서의 (id, indent). index: 0..len(entries).
    놓인 묶음이 뒤따르는 항목을 하위로 빼앗지 않도록
    indent 범위는 [다음 항목 indent, 이전 항목 indent + 1].
    dragged_id가 entries에 없으면 다른 날짜에서 끌고 온 것 (그 자리에 끼워 넣기).
    """
    ids = [e[0] for e in entries]
    i = ids.index(dragged_id) if dragged_id in ids else -1
    end = i + 1
    while i >= 0 and end < len(entries) and entries[end][1] > entries[i][1]:
        end += 1

    if i >= 0 and i <= index <= end:  # 제자리 (깊이만 바뀔 수 있음)
        before: str | None = dragged_id
        prev = entries[i - 1] if i > 0 else None
        nxt = entries[end] if end < len(entries) else None
    else:
        before = entries[index][0] if index < len(entries) else None
        prev = entries[index - 1] if index > 0 else None
        nxt = entries[index] if index < len(entries) else None

    hi = prev[1] + 1 if prev else 0
    lo = nxt[1] if nxt else 0
    return before, max(lo, min(desired, hi))


# ---------------------------------------------------------------- 완료 항목 표시 방식


@dataclass
class _Node:
    todo: Todo
    children: list[_Node] = field(default_factory=list)

    def all_done(self) -> bool:
        return self.todo.done and all(c.all_done() for c in self.children)


def _build_tree(todos: list[Todo]) -> list[_Node]:
    roots: list[_Node] = []
    stack: list[_Node] = []
    for t in todos:
        node = _Node(t)
        while stack and stack[-1].todo.indent >= t.indent:
            stack.pop()
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return roots


def arrange_rows(todos: list[Todo], done_style: str) -> list[tuple[Todo, bool, bool]]:
    """화면에 보일 순서대로 (todo, 흐리게, 취소선).

    - strike: 완료 항목에 취소선(+살짝 흐리게)
    - dim:    완료 항목을 흐리게만
    - bottom: 같은 부모 안에서 완료 항목을 하위 항목과 함께 맨 아래로
    - hide:   하위 항목까지 전부 완료된 항목만 숨김. 안 끝난 하위 항목이 있으면 흐리게 보임
    """
    if done_style == "dim":
        return [(t, t.done, False) for t in todos]
    if done_style == "strike":
        return [(t, t.done, t.done) for t in todos]

    out: list[tuple[Todo, bool, bool]] = []

    def walk(nodes: list[_Node]) -> None:
        if done_style == "bottom":
            nodes = [n for n in nodes if not n.todo.done] + [n for n in nodes if n.todo.done]
        for n in nodes:
            if done_style == "hide" and n.all_done():
                continue
            out.append((n.todo, n.todo.done, n.todo.done))
            walk(n.children)

    walk(_build_tree(todos))
    return out


# ---------------------------------------------------------------- 체크 버튼


class CheckButton(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shape = "circle"  # square / circle / bullet
        self.color = QColor("#1C1C1E")  # 글자색 (빈 테두리는 이 색을 옅게)
        self.accent = QColor("#FF9F0A")  # 완료 채움색
        self.ring_alpha = RING_ALPHA  # 빈 테두리 진하기 (배경에 맞춰 ring_alpha()로 정함)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(22, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 2
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        checked = self.isChecked()
        ring = QColor(self.color)
        ring.setAlphaF(self.ring_alpha)  # 3:1 이상 대비

        if self.shape == "bullet":
            p.setPen(Qt.PenStyle.NoPen)
            if checked:
                self._draw_check(p, r, self.accent)
            else:
                p.setBrush(ring)
                d = side * 0.34
                p.drawEllipse(r.center(), d / 2, d / 2)
            return

        if checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)
        else:
            p.setPen(QPen(ring, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = r.adjusted(0.75, 0.75, -0.75, -0.75)
        if self.shape == "circle":
            p.drawEllipse(r)
        else:
            p.drawRoundedRect(r, 5, 5)
        if checked:
            self._draw_check(p, r, QColor("#FFFFFF"))

    @staticmethod
    def _draw_check(p: QPainter, r: QRectF, color: QColor) -> None:
        path = QPainterPath()
        path.moveTo(r.left() + r.width() * 0.22, r.top() + r.height() * 0.52)
        path.lineTo(r.left() + r.width() * 0.42, r.top() + r.height() * 0.72)
        path.lineTo(r.left() + r.width() * 0.78, r.top() + r.height() * 0.30)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


# ---------------------------------------------------------------- 할 일 한 줄


class WrapLabel(QLabel):
    """줄바꿈 라벨. 실제 너비 기준으로 높이를 알려준다.

    기본 QLabel(wordWrap)은 sizeHint를 어림짐작한 너비로 계산해서,
    한 줄로 충분한데도 두 줄 높이를 차지하는 일이 생긴다.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        # 할 일 내용은 글자 그대로만 보여준다. 기본값(AutoText)이면 '<img src=…>' 같은
        # HTML을 해석해서 PC의 파일을 읽어 그리거나 화면을 망가뜨릴 수 있다.
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def _height(self) -> int:
        if self.width() > 0:
            return self.heightForWidth(self.width())
        return self.fontMetrics().height()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(20, self._height())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self.updateGeometry()


class ElideLabel(QLabel):
    """좁아지면 끝을 '…'로 줄이는 한 줄 라벨 (글자 때문에 노트 최소 폭이 커지지 않게).

    sample을 주면 그 글자가 다 보이는 폭이 최소 폭 (예: 보통 날짜는 잘리지 않게).
    """

    MIN_WIDTH = 48

    def __init__(self, parent: QWidget | None = None, *, sample: str = "", tip: str = "") -> None:
        super().__init__(parent)
        self.sample = sample
        self.tip = tip  # 평소 툴팁. 글자가 잘리면 전체 글자를 툴팁으로
        self.setToolTip(tip)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        width = self.MIN_WIDTH
        if self.sample:
            m = self.contentsMargins()
            width = self.fontMetrics().horizontalAdvance(self.sample) + m.left() + m.right() + 2
        return QSize(width, super().minimumSizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        tip = self.text() if text != self.text() else self.tip
        if self.toolTip() != tip:
            self.setToolTip(tip)  # 잘렸으면 마우스를 올리면 전체가 보이게
        self.style().drawItemText(
            p, rect, int(self.alignment()), self.palette(), self.isEnabled(), text,
            self.foregroundRole(),
        )  # fmt: skip


class TodoRow(QWidget):
    toggled = Signal(str, bool)
    edited = Signal(str, str)
    delete_clicked = Signal(str)
    clicked = Signal(str)
    menu_requested = Signal(str, QPoint)  # 우클릭 (id, 화면 좌표)
    drag_requested = Signal(str, QPoint)
    indent_requested = Signal(str, int)  # 편집 중 Tab / Shift+Tab
    edit_closed = Signal()  # Enter / Esc로 편집을 마침

    def __init__(
        self,
        todo: Todo,
        style: dict,
        parent: QWidget | None = None,
        *,
        dim: bool = False,
        strike: bool = False,
    ) -> None:
        super().__init__(parent)
        self.todo = todo
        self.setObjectName("row")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("selected", False)
        self._press_pos: QPoint | None = None
        self._editing = False

        pad = style["row_pad"]
        # 보드 칸처럼 좁은 곳: 들여쓰기·여백을 줄이고 삭제 버튼 자리를 비워두지 않는다
        compact = style.get("compact", False)
        step = INDENT_PX // 2 if compact else INDENT_PX
        lay = QHBoxLayout(self)
        lay.setContentsMargins(
            (2 if compact else 6) + visual_indent(todo.indent) * step, pad, 2 if compact else 4, pad
        )
        lay.setSpacing(6)

        self.check = CheckButton(self)
        self.check.shape = style["check_style"]
        self.check.color = QColor(style["text_color"])
        self.check.accent = QColor(style["accent_color"])
        ring_a = ring_alpha(style["text_color"], style["bg_color"])
        self.check.ring_alpha = ring_a
        self.check.setChecked(todo.done)
        self.check.setFixedSize(22, 22)
        self.check.toggled.connect(lambda on: self.toggled.emit(self.todo.id, on))
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)

        self.label = WrapLabel(todo.text, self)
        self.label.setProperty("dim", dim)  # QSS: 흐린 글자색
        self.label.setProperty("strike", strike)  # QSS: 취소선
        lay.addWidget(self.label, 1)

        self.editor = QLineEdit(self)
        self.editor.hide()
        self.editor.installEventFilter(self)
        self.editor.editingFinished.connect(lambda: self.finish_edit(commit=True))
        lay.addWidget(self.editor, 1)

        self.delete_btn = QToolButton(self)
        self.delete_btn.setObjectName("rowDelete")
        self.delete_btn.setIcon(
            icons.icon("remove", _mix(style["text_color"], style["bg_color"], ring_a), 18)
        )
        self.delete_btn.setIconSize(QSize(18, 18))
        self.delete_btn.setFixedSize(26, 26)
        self.delete_btn.setToolTip("삭제")
        self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sp = self.delete_btn.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.delete_btn.setSizePolicy(sp)
        self.delete_btn.hide()
        self._show_delete = not compact  # 좁은 칸은 우클릭·Delete 키로 삭제
        self.delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.todo.id))
        if not compact:  # 좁은 칸에서는 레이아웃에 넣지 않고 늘 숨겨 둔다
            lay.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignTop)

    # 선택 표시 (QSS의 [selected="true"])
    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    # ------------------------------------------------------------ 편집

    def begin_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self.editor.setText(self.todo.text)
        self.label.hide()
        self.editor.show()
        self._align_side_widgets(Qt.AlignmentFlag.AlignVCenter)
        self.editor.setFocus()
        self.editor.selectAll()

    def finish_edit(self, commit: bool) -> None:
        if not self._editing:
            return
        self._editing = False
        text = " ".join(self.editor.text().split())
        self.editor.hide()
        self.label.show()
        self._align_side_widgets(Qt.AlignmentFlag.AlignTop)
        if commit and text and text != self.todo.text:
            self.edited.emit(self.todo.id, text)

    def _align_side_widgets(self, align: Qt.AlignmentFlag) -> None:
        """체크·삭제 버튼 세로 정렬.

        평소엔 위쪽(긴 글이 두 줄로 넘어가도 첫 줄 옆에 오도록),
        편집 중엔 편집칸이 더 높으니 가운데.
        """
        lay = self.layout()
        lay.setAlignment(self.check, align)
        lay.setAlignment(self.delete_btn, align)

    @property
    def editing(self) -> bool:
        return self._editing

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.finish_edit(commit=False)
                self.edit_closed.emit()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # 입력칸은 Enter를 부모로 흘려보내는데, 목록은 Enter를 '수정 시작'으로
                # 받아서 편집이 다시 열린다. 여기서 끝낸다.
                self.finish_edit(commit=True)
                self.edit_closed.emit()
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                step = -1 if key == Qt.Key.Key_Backtab else 1
                self.finish_edit(commit=True)
                self.indent_requested.emit(self.todo.id, step)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ 마우스

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.HoverEnter and self._show_delete:
            self.delete_btn.show()
        elif e.type() == QEvent.Type.HoverLeave:
            self.delete_btn.hide()
        return super().event(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.position().toPoint()
            self.clicked.emit(self.todo.id)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if (
            self._press_pos is not None
            and not self._editing
            and e.buttons() & Qt.MouseButton.LeftButton
            and (e.position().toPoint() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            start = self._press_pos
            self._press_pos = None
            self.drag_requested.emit(self.todo.id, start)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        if not self._editing:
            self.menu_requested.emit(self.todo.id, e.globalPos())
        e.accept()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.begin_edit()
            # 이벤트를 여기서 끝낸다. 부모(목록)까지 올라가면 Qt가 목록에 포커스를 줘서
            # 방금 연 편집칸이 포커스를 잃고 바로 닫힌다.
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------- 목록


class _RowContainer(QWidget):
    """드래그 앤 드롭을 받는 목록 내부 위젯."""

    def __init__(self, view: TodoListView) -> None:
        super().__init__()
        self.view = view
        self.setAcceptDrops(True)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        # 할 일 줄이 아닌 빈 곳을 누름 (보드: 그 날 칸 고르기)
        if e.button() == Qt.MouseButton.LeftButton:
            self.view.background_clicked.emit()
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME_TODO):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME_TODO):
            self.view.update_drop(e.position().toPoint())
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self.view.indicator.hide()

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME_TODO):
            self.view.finish_drop(e.position().toPoint())
            e.acceptProposedAction()


DAY_BOX_INSET = 6  # 목록 여백(4)과 더해 배너 여백(10)과 맞춘다


class DayHeader(QWidget):
    """주간 보기의 날짜 머리줄: '수 25  [오늘]        2/4'.

    누르면 그날이 '고른 날'(입력칸이 추가하는 날)이 되고, 더블클릭하면 그날 하루 보기로.
    """

    clicked = Signal(str)
    double_clicked = Signal(str)

    def __init__(
        self,
        day: str,
        done: int,
        total: int,
        *,
        is_today: bool,
        current: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.day = day
        self.setObjectName("dayHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("누르면 이 날에 추가 · 더블클릭: 이 날만 보기")
        d = date.fromisoformat(day)
        # 바깥: 위 간격 + 좌우를 '못 끝낸 일' 배너와 같은 선에 맞춘다 (목록 여백 4 + 6 = 10)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(DAY_BOX_INSET, 6, DAY_BOX_INSET, 2)
        # 안쪽 상자: 고른 날 배경이 깔리는 칸 (QSS margin은 글자와 배경을 어긋나게 해서 안 씀)
        self.box = QFrame(self)
        self.box.setObjectName("dayHeaderBox")
        self.box.setProperty("current", current)
        outer.addWidget(self.box)
        lay = QHBoxLayout(self.box)
        lay.setContentsMargins(12, 5, 8, 5)  # 배너 글자와 같은 들여쓰기
        lay.setSpacing(6)
        self.label = QLabel(f"{WEEKDAYS[d.weekday()]} {d.day}", self.box)
        self.label.setObjectName("dayLabel")
        self.label.setProperty("current", current)
        lay.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.today_pill = QLabel("오늘", self.box)
        self.today_pill.setObjectName("todayPill")
        self.today_pill.setVisible(is_today)
        lay.addWidget(self.today_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.count = QLabel(f"{done}/{total}" if total else "", self.box)
        self.count.setObjectName("dayCount")
        lay.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.day)
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.day)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


@dataclass
class _Section:
    """주간 보기의 한 날짜 구역 (드롭 위치 계산용)."""

    day: str
    header: QWidget | None
    rows: list[TodoRow]


class TodoListView(QScrollArea):
    """노트 순서 그대로 할 일을 보여주고, 키보드·드래그 조작을 TodoStore로 넘긴다.

    days가 None이면 지금 날짜 한 페이지, 날짜 목록이면 날짜별 머리줄과 함께 이어서 보여준다
    (주간 보기). 주간 보기에서는 다른 날짜 구역으로 끌어다 놓으면 그 날로 옮겨진다.
    """

    delete_done = Signal(int)
    undo_requested = Signal()
    menu_requested = Signal(str, QPoint)
    day_clicked = Signal(str)
    day_double_clicked = Signal(str)
    background_clicked = Signal()  # 줄이 아닌 빈 곳을 누름

    # 끌기 정보는 모든 목록이 같이 본다 (주간 보드에서 다른 날 칸으로 놓을 수 있게)
    _drag_state: tuple[str, int, int] | None = None  # (id, 시작 indent, 시작 x)

    def __init__(
        self, store: TodoStore, parent: QWidget | None = None, *, fixed_day: str | None = None
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.fixed_day = fixed_day  # 주간 보드의 칸: 이 날짜만 보여준다
        self.setObjectName("todoList")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.container = _RowContainer(self)
        self.container.setObjectName("rowContainer")
        self._layout = QVBoxLayout(self.container)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(0)
        self._layout.addStretch(1)
        self.setWidget(self.container)
        # 스크롤 막대가 나타나거나 사라지면 날짜 칸 오른쪽 끝을 다시 맞춘다
        self.verticalScrollBar().rangeChanged.connect(
            lambda *_: QTimer.singleShot(0, self, self._align_headers)
        )

        self.empty_label = QLabel(EMPTY_TIPS, self.container)
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.insertWidget(0, self.empty_label)

        self.indicator = QFrame(self.container)
        self.indicator.setObjectName("dropIndicator")
        self.indicator.setFixedHeight(2)
        self.indicator.hide()

        self.rows: list[TodoRow] = []
        self.days: list[str] | None = None  # None: 한 페이지 / 날짜 목록: 주간 보기
        self._sections: list[_Section] = []
        self._extras: list[QWidget] = []  # 날짜 머리줄, '비어 있음' 표시
        self.selected_id: str | None = None
        self.style_opts: dict = {}
        self._pending_edit: str | None = None
        if fixed_day is not None:
            self.empty_label.setObjectName("boardEmpty")

    @property
    def _drag(self) -> tuple[str, int, int] | None:
        return TodoListView._drag_state

    @_drag.setter
    def _drag(self, value: tuple[str, int, int] | None) -> None:
        TodoListView._drag_state = value

    # ------------------------------------------------------------ 그리기

    def _make_row(self, todo: Todo, dim: bool, strike: bool) -> TodoRow:
        row = TodoRow(todo, self.style_opts, self.container, dim=dim, strike=strike)
        row.toggled.connect(self.store.set_done)
        row.edited.connect(self._on_edited)
        row.delete_clicked.connect(self.request_delete)
        row.clicked.connect(self.select)
        row.menu_requested.connect(self._on_row_menu)
        row.drag_requested.connect(self._start_drag)
        row.indent_requested.connect(self._on_editor_indent)
        row.edit_closed.connect(self.setFocus)  # 바로 방향키로 이동할 수 있게
        return row

    def rebuild(self) -> None:
        for w in [*self.rows, *self._extras]:
            self._layout.removeWidget(w)
            w.hide()
            w.deleteLater()
        self.rows = []
        self._extras = []
        self._sections = []
        done_style = self.style_opts.get("done_style", "strike")
        if self.days is None:
            day = self.fixed_day or self.store.current_day
            todos = self.store.todos(day)
            shown = arrange_rows(todos, done_style)
            for i, (todo, dim, strike) in enumerate(shown):
                row = self._make_row(todo, dim, strike)
                self._layout.insertWidget(i, row)
                self.rows.append(row)
            self._sections = [_Section(day, None, list(self.rows))]
            if self.fixed_day is not None:  # 보드 칸은 짧게
                self.empty_label.setText("완료한 일만 있음" if todos else "비어 있음")
            else:
                self.empty_label.setText("완료한 할 일만 있어요" if todos else EMPTY_TIPS)
            self.empty_label.setVisible(not shown)
        else:
            self._build_days(done_style)
            self.empty_label.hide()
        if self.selected_id and not any(r.todo.id == self.selected_id for r in self.rows):
            self.selected_id = None
        self._paint_selection()
        if self._pending_edit:
            row = self._row(self._pending_edit)
            self._pending_edit = None
            if row:
                row.begin_edit()

    def _build_days(self, done_style: str) -> None:
        today = today_str()
        i = 0
        for day in self.days or []:
            todos = self.store.todos(day)
            done = sum(t.done for t in todos)
            header = DayHeader(
                day,
                done,
                len(todos),
                is_today=day == today,
                current=day == self.store.current_day,
                parent=self.container,
            )
            header.clicked.connect(self.day_clicked.emit)
            header.double_clicked.connect(self.day_double_clicked.emit)
            self._layout.insertWidget(i, header)
            self._extras.append(header)
            i += 1
            section = _Section(day, header, [])
            for todo, dim, strike in arrange_rows(todos, done_style):
                row = self._make_row(todo, dim, strike)
                self._layout.insertWidget(i, row)
                self.rows.append(row)
                section.rows.append(row)
                i += 1
            if not section.rows:
                empty = QLabel("완료한 할 일만 있어요" if todos else "비어 있음", self.container)
                empty.setObjectName("dayEmpty")
                self._layout.insertWidget(i, empty)
                self._extras.append(empty)
                i += 1
            self._sections.append(section)
        self._align_headers()

    def _align_headers(self) -> None:
        """스크롤 막대가 보이면 그만큼 오른쪽 여백을 줄여서, 날짜 칸 오른쪽 끝을 배너와 맞춘다."""
        bar = max(0, self.width() - self.viewport().width())
        right = max(0, DAY_BOX_INSET - bar)
        for s in self._sections:
            if isinstance(s.header, DayHeader):
                m = s.header.layout().contentsMargins()
                if m.right() != right:
                    s.header.layout().setContentsMargins(m.left(), m.top(), right, m.bottom())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._align_headers()

    def scroll_to(self, todo_id: str) -> None:
        """그 항목이 보이게 스크롤 (레이아웃이 자리 잡은 뒤에)."""

        def go() -> None:
            row = self._row(todo_id)
            if row is not None:
                self.ensureWidgetVisible(row, 0, 8)

        QTimer.singleShot(0, go)

    def _on_row_menu(self, todo_id: str, pos: QPoint) -> None:
        self.select(todo_id)
        self.menu_requested.emit(todo_id, pos)

    def begin_edit(self, todo_id: str) -> None:
        row = self._row(todo_id)
        if row:
            row.begin_edit()

    def _row(self, todo_id: str | None) -> TodoRow | None:
        return next((r for r in self.rows if r.todo.id == todo_id), None)

    def _paint_selection(self) -> None:
        for r in self.rows:
            r.set_selected(r.todo.id == self.selected_id)

    def select(self, todo_id: str | None) -> None:
        self.selected_id = todo_id
        self._paint_selection()
        row = self._row(todo_id)
        if row:
            self.ensureWidgetVisible(row, 0, 8)
        self.setFocus()

    def scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        )

    # ------------------------------------------------------------ 동작

    def request_delete(self, todo_id: str) -> None:
        n = self.store.delete([todo_id])
        if n:
            self.delete_done.emit(n)

    def _on_edited(self, todo_id: str, text: str) -> None:
        self.store.update_text(todo_id, text)

    def _on_editor_indent(self, todo_id: str, step: int) -> None:
        self._pending_edit = todo_id
        changed = self.store.indent(todo_id) if step > 0 else self.store.outdent(todo_id)
        if not changed:  # 변화 없으면 rebuild가 안 일어나니 직접 편집 재개
            self._pending_edit = None
            row = self._row(todo_id)
            if row:
                row.begin_edit()

    def event(self, e) -> bool:
        # Tab은 포커스 이동에 먹히기 전에 가로챈다
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if self.selected_id:
                if e.key() == Qt.Key.Key_Tab:
                    self.store.indent(self.selected_id)
                else:
                    self.store.outdent(self.selected_id)
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.matches(QKeySequence.StandardKey.Undo):
            self.undo_requested.emit()
            return
        sid = self.selected_id
        ids = [r.todo.id for r in self.rows]
        key = e.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and ids:
            if sid not in ids:
                self.select(ids[0] if key == Qt.Key.Key_Down else ids[-1])
            else:
                i = ids.index(sid) + (1 if key == Qt.Key.Key_Down else -1)
                self.select(ids[max(0, min(i, len(ids) - 1))])
            return
        if sid and sid in ids:
            row = self._row(sid)
            if key == Qt.Key.Key_Space:
                self.store.set_done(sid, not row.todo.done)
                return
            if key in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                row.begin_edit()
                return
            if key == Qt.Key.Key_Delete:
                i = ids.index(sid)
                self.request_delete(sid)
                remaining = [r.todo.id for r in self.rows]
                self.select(remaining[min(i, len(remaining) - 1)] if remaining else None)
                return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ 드래그

    @property
    def drag_enabled(self) -> bool:
        # '맨 아래로' 모드는 화면 순서와 실제 순서가 달라서 드래그 정렬을 끈다
        return self.style_opts.get("done_style") != "bottom"

    def _start_drag(self, todo_id: str, press_pos: QPoint) -> None:
        row = self._row(todo_id)
        if row is None or not self.drag_enabled:
            return
        start_x = row.mapTo(self.container, press_pos).x()
        self._drag = (todo_id, row.todo.indent, start_x)
        mime = QMimeData()
        mime.setData(MIME_TODO, todo_id.encode())
        drag = QDrag(row)
        drag.setMimeData(mime)
        pix = row.grab()
        drag.setPixmap(pix)
        drag.setHotSpot(press_pos)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag = None
        self.indicator.hide()

    def _section_at(self, y: int) -> _Section | None:
        """y 위치가 속한 날짜 구역 (그 날 머리줄부터 다음 날 머리줄 전까지)."""
        found = self._sections[0] if self._sections else None
        for s in self._sections:
            if s.header is not None and s.header.geometry().top() <= y:
                found = s
        return found

    def _drop_target(self, pos: QPoint) -> tuple[str | None, int, int, _Section] | None:
        if self._drag is None:
            return None
        section = self._section_at(pos.y())
        if section is None:
            return None
        dragged, start_indent, start_x = self._drag
        rows = section.rows
        index = len(rows)
        for i, r in enumerate(rows):
            if pos.y() < r.geometry().center().y():
                index = i
                break
        desired = start_indent + round((pos.x() - start_x) / INDENT_PX)
        entries = [(r.todo.id, r.todo.indent) for r in rows]
        before, indent = compute_drop(entries, dragged, index, desired)
        return before, indent, index, section

    def update_drop(self, pos: QPoint) -> None:
        self._autoscroll(pos)
        target = self._drop_target(pos)
        if target is None:
            return
        _, indent, index, section = target
        rows = section.rows
        if index < len(rows):
            y = rows[index].geometry().top()
        elif rows:
            y = rows[-1].geometry().bottom() + 1
        elif section.header is not None:  # 빈 날: 머리줄 바로 밑
            y = section.header.geometry().bottom() + 2
        else:  # 빈 칸·빈 페이지: 맨 위
            y = 4
        x = 6 + indent * INDENT_PX + 4
        self.indicator.setGeometry(x, max(0, y - 1), self.container.width() - x - 8, 2)
        self.indicator.show()
        self.indicator.raise_()

    def _autoscroll(self, pos: QPoint) -> None:
        """끌면서 위·아래 끝에 가까이 가면 스크롤 (화면 밖 날짜로 옮길 수 있게)."""
        y = self.container.mapTo(self.viewport(), pos).y()
        bar = self.verticalScrollBar()
        if y < 28:
            bar.setValue(bar.value() - 14)
        elif y > self.viewport().height() - 28:
            bar.setValue(bar.value() + 14)

    def finish_drop(self, pos: QPoint) -> None:
        target = self._drop_target(pos)
        self.indicator.hide()
        if target is None or self._drag is None:
            return
        before, indent, _, section = target
        self.store.move(self._drag[0], before_id=before, indent=indent, day=section.day)


# ---------------------------------------------------------------- 주간 보드 (넓은 노트)

LIST_MIN_HEIGHT = 64  # 일·주 보기 목록의 최소 높이
MONTH_LIST_MIN = 110  # 월간: 목록 칸이 이보다 낮아지면 목록을 숨기고 달력만
BOARD_MIN_WIDTH = 960  # 주간 보기에서 노트가 이만큼 넓으면 가로 7칸 보드로


class BoardColumn(QFrame):
    """보드의 한 칸. 빈 곳을 누르면 그 날을 고른다."""

    clicked = Signal(str)

    def __init__(self, day: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.day = day
        self.setObjectName("boardColumn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.day)
        super().mouseReleaseEvent(e)


class WeekBoard(QWidget):
    """일~토 7칸. 칸마다 날짜 머리줄 + 그 날 목록. 칸을 누르면 그 날을 고른다.

    칸 사이로 끌어다 놓으면 그 날로 옮겨진다 (목록들이 끌기 정보를 같이 본다).
    """

    day_clicked = Signal(str)  # 칸(머리줄·빈 곳)을 누름: 그 날을 고름
    day_double_clicked = Signal(str)

    def __init__(self, store: TodoStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("weekBoard")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 2, 10, 0)
        self._lay.setSpacing(6)
        self.columns: list[QFrame] = []
        self.lists: list[TodoListView] = []
        self.headers: list[QWidget] = []
        self.style_opts: dict = {}
        self.days: list[str] = []

    def rebuild(self, days: list[str], wire: Callable[[TodoListView], None]) -> None:
        """days(7일)로 칸을 다시 만든다. wire: 목록 신호를 노트에 잇는 함수."""
        if days != self.days:
            for col in self.columns:
                self._lay.removeWidget(col)
                col.hide()
                col.deleteLater()
            self.columns, self.lists, self.headers = [], [], []
            self.days = list(days)
            for d in days:
                self._add_column(d, wire)
        today = today_str()
        for i, (col, lv, d) in enumerate(zip(self.columns, self.lists, self.days, strict=True)):
            lv.style_opts = {**self.style_opts, "compact": True}
            lv.rebuild()
            todos = self.store.todos(d)
            old = self.headers[i]
            header = DayHeader(
                d,
                sum(t.done for t in todos),
                len(todos),
                is_today=d == today,
                current=d == self.store.current_day,
                parent=col,
            )
            header.layout().setContentsMargins(0, 0, 0, 4)
            header.clicked.connect(self.day_clicked.emit)
            header.double_clicked.connect(self.day_double_clicked.emit)
            col.layout().replaceWidget(old, header)
            old.hide()
            old.deleteLater()
            self.headers[i] = header
            col.setProperty("today", d == today)
            col.setProperty("current", d == self.store.current_day)
            col.style().unpolish(col)
            col.style().polish(col)

    def _add_column(self, day: str, wire: Callable[[TodoListView], None]) -> None:
        col = BoardColumn(day, self)
        col.clicked.connect(self.day_clicked.emit)
        c_lay = QVBoxLayout(col)
        c_lay.setContentsMargins(4, 6, 4, 4)
        c_lay.setSpacing(0)
        placeholder = QWidget(col)
        c_lay.addWidget(placeholder)
        self.headers.append(placeholder)
        lv = TodoListView(self.store, col, fixed_day=day)
        lv.style_opts = self.style_opts
        lv.background_clicked.connect(lambda d=day: self.day_clicked.emit(d))
        wire(lv)
        c_lay.addWidget(lv, 1)
        self._lay.addWidget(col, 1)
        self.columns.append(col)
        self.lists.append(lv)


# ---------------------------------------------------------------- 제목 바


class TitleBar(QWidget):
    """‹ 날짜 › 와 고정·메뉴·닫기 버튼. 끌면 창이 움직이고, 날짜를 더블클릭하면 오늘로."""

    prev_requested = Signal()
    next_requested = Signal()
    today_requested = Signal()
    mode_requested = Signal(str)  # 일 / 주 버튼

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._press: QPoint | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 10, 8, 6)
        outer.setSpacing(4)
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        outer.addLayout(lay)

        # 일 · 주 보기 전환 (iOS 세그먼트 버튼)
        self.mode_switch = QFrame(self)
        self.mode_switch.setObjectName("modeSwitch")
        m_lay = QHBoxLayout(self.mode_switch)
        m_lay.setContentsMargins(2, 2, 2, 2)
        m_lay.setSpacing(0)
        self.mode_buttons: dict[str, QToolButton] = {}
        for i, (mode, text) in enumerate(VIEW_NAMES.items(), start=1):
            b = QToolButton(self.mode_switch)
            b.setObjectName("modeButton")
            b.setText(text)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFixedSize(40, 24)
            b.setToolTip(f"{text} 단위로 보기 (Ctrl+{i})")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mode: self.mode_requested.emit(m))
            m_lay.addWidget(b)
            self.mode_buttons[mode] = b
        self.mode_buttons["day"].setChecked(True)
        # 오른쪽: 완료 개수 ('7 / 12 완료')
        self.count_label = QLabel(self)
        self.count_label.setObjectName("countLabel")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row2 = QHBoxLayout()
        row2.setContentsMargins(6, 0, 6, 0)
        row2.setSpacing(12)
        row2.addWidget(self.mode_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        row2.addWidget(self.count_label, 1)
        outer.addLayout(row2)

        self.prev_btn = self._button("이전 날짜 (Alt+←)")
        self.prev_btn.setFixedSize(26, 32)
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        # 보통 날짜('12월 30일', '12월 30일 (수)')는 잘리지 않을 만큼 폭을 지키고,
        # 그보다 긴 제목(해를 넘는 주 등)은 '…'로 줄인다 (마우스를 올리면 전체)
        self.label = ElideLabel(self, sample="12월 30일", tip="더블클릭하면 오늘로")
        self.label.setText(title)
        self.label.setObjectName("titleLabel")
        self.sub = ElideLabel(self, sample="12월 30일 (수)")
        self.sub.setObjectName("titleSub")
        title_box = QVBoxLayout()
        title_box.setContentsMargins(4, 0, 4, 0)
        title_box.setSpacing(1)
        title_box.addWidget(self.label)
        title_box.addWidget(self.sub)
        self.next_btn = self._button("다음 날짜 (Alt+→)")
        self.next_btn.setFixedSize(26, 32)
        self.next_btn.clicked.connect(self.next_requested.emit)
        lay.addWidget(self.prev_btn)
        lay.addLayout(title_box)
        lay.addWidget(self.next_btn)
        lay.addStretch(1)

        self.pin_btn = self._button("항상 위에 고정")
        self.pin_btn.setCheckable(True)
        self.pin_btn.toggled.connect(lambda _: self._paint_icons())
        self.menu_btn = self._button("메뉴")
        self.close_btn = self._button("닫기")
        for b in (self.pin_btn, self.menu_btn, self.close_btn):
            b.setFixedSize(28, 32)  # 좁은 노트에서도 날짜가 잘리지 않게
            lay.addWidget(b)
        self._colors = ("#1C1C1E", "#FF9F0A", "#FFFFFF")
        self._paint_icons()

    def set_colors(self, text: str, accent: str, bg: str) -> None:
        self._colors = (text, accent, bg)
        self._paint_icons()

    def _paint_icons(self) -> None:
        text, accent, bg = self._colors
        soft = _mix(text, bg, max(0.55, ring_alpha(text, bg)))
        if self.pin_btn.isChecked():
            self.pin_btn.setIcon(icons.icon("pin_fill", accent))
        else:
            self.pin_btn.setIcon(icons.icon("pin", soft))
        self.menu_btn.setIcon(icons.icon("more", soft))
        self.close_btn.setIcon(icons.icon("close", soft))
        self.prev_btn.setIcon(icons.icon("chevron_left", soft))
        self.next_btn.setIcon(icons.icon("chevron_right", soft))

    def _button(self, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("titleButton")
        b.setIconSize(QSize(20, 20))
        b.setFixedSize(32, 32)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def set_title(self, title: str, sub: str = "") -> None:
        self.label.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

    def set_progress(self, done: int, total: int) -> None:
        self.count_label.setText(f"{done} / {total} 완료" if total else "할 일 없음")

    def set_mode(self, mode: str) -> None:
        self.mode_buttons[mode].setChecked(True)
        unit = VIEW_UNIT[mode]
        self.prev_btn.setToolTip(f"이전 {unit} (Alt+←)")
        self.next_btn.setToolTip(f"다음 {unit} (Alt+→)")

    # 누르는 순간이 아니라 끌기 시작할 때 창 이동을 시작한다 (더블클릭이 씹히지 않게)
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            e.accept()  # 받아야 이어지는 마우스 이동이 이 위젯으로 온다
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if (
            self._press is not None
            and e.buttons() & Qt.MouseButton.LeftButton
            and (e.position().toPoint() - self._press).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press = None
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()  # 윈도우 스냅 등 기본 이동 동작
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._press = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pos = e.position().toPoint()
        if self.label.geometry().contains(pos) or self.sub.geometry().contains(pos):
            self.today_requested.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------- 입력창


class TodoInput(QLineEdit):
    """Enter로 추가. Tab / Shift+Tab으로 새 할 일의 들여쓰기 단계를 정한다."""

    submitted = Signal(str, int)

    def __init__(self, max_level: Callable[[], int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("todoInput")
        self._max_level = max_level
        self.level = 0
        self.setMaxLength(500)
        self.setMinimumWidth(60)
        self.returnPressed.connect(self._submit)
        self._colors = ("#1C1C1E", "#FF9F0A")
        self.target = ""  # 주간 보기: 추가될 날짜 ('오늘', '11/27 (목)')
        self._lead = self.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        self._update_look()

    def set_target(self, target: str) -> None:
        self.target = target
        self._update_look()

    def set_colors(self, text: str, accent: str) -> None:
        self._colors = (text, accent)
        self._update_look()

    def set_level(self, level: int) -> None:
        self.level = max(0, min(level, self._max_level()))
        self._update_look()

    def _update_look(self) -> None:
        text, accent = self._colors
        self.setTextMargins(visual_indent(self.level) * INDENT_PX, 0, 0, 0)
        if self.level:
            self._lead.setIcon(icons.icon("subtask", accent))
            self.setPlaceholderText("하위 할 일")
        else:
            self._lead.setIcon(icons.icon("add", accent))
            self.setPlaceholderText(f"{self.target} 할 일 추가" if self.target else "새로운 할 일")

    def _submit(self) -> None:
        text = " ".join(self.text().split())
        if text:
            self.submitted.emit(text, self.level)
            self.clear()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.set_level(self.level + (1 if e.key() == Qt.Key.Key_Tab else -1))
            return True
        return super().event(e)


# ---------------------------------------------------------------- 노트 창


class NoteWidget(QWidget):
    hidden_to_tray = Signal()

    def __init__(
        self,
        store: TodoStore,
        settings: Settings,
        settings_path: Path,
        parent=None,
        *,
        autostart: Autostart | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.autostart = autostart
        self.hotkey = None  # main에서 GlobalHotkey를 붙인다
        self.hide_to_tray = False  # 트레이가 있으면 ✕는 숨기기
        self._quitting = False
        self.settings = settings
        self.settings_path = Path(settings_path)
        self.setWindowTitle("Sticky Todo")
        # 최소 크기는 고정값 대신 레이아웃이 정한다: 보기(일·주·월)·배너·글자 크기에 따라
        # 제목·버튼·달력·입력칸이 눌려서 글자가 겹치기 직전까지만 줄어든다.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._apply_window_flags()

        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(500)
        self._settings_timer.timeout.connect(self.save_settings)
        self._restoring = True
        self.view_mode = settings["view_mode"]  # day / week
        self._settings_win = None
        self._manager_win = None
        self._help_win = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(400)
        self._hover_timer.timeout.connect(self._update_title_visibility)

        outer = QVBoxLayout(self)
        self._outer = outer
        self.card = QFrame(self)
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        self.title_bar = TitleBar(day_title(store.current_day)[0], self.card)
        self.title_bar.pin_btn.setChecked(settings["always_on_top"])
        self.title_bar.pin_btn.toggled.connect(self.set_always_on_top)
        self.title_bar.menu_btn.clicked.connect(self._show_menu)
        self.title_bar.close_btn.clicked.connect(self.close)
        self.title_bar.prev_requested.connect(lambda: self.go_day(-1))
        self.title_bar.next_requested.connect(lambda: self.go_day(1))
        self.title_bar.today_requested.connect(self.go_today)
        self.title_bar.mode_requested.connect(self.set_view_mode)
        self.title_bar.set_mode(self.view_mode)
        card_lay.addWidget(self.title_bar)

        # 오늘 페이지에서: 지난 날 못 끝낸 일 가져오기
        self.overdue_bar = QFrame(self.card)
        self.overdue_bar.setObjectName("overdueBar")
        o_lay = QHBoxLayout(self.overdue_bar)
        o_lay.setContentsMargins(12, 7, 8, 7)
        self.overdue_label = QLabel(self.overdue_bar)
        self.overdue_label.setObjectName("overdueLabel")
        self.overdue_btn = QToolButton(self.overdue_bar)
        self.overdue_btn.setObjectName("overdueButton")
        self.overdue_btn.setText("가져오기")
        self.overdue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overdue_btn.clicked.connect(self.bring_overdue)
        # 글자 크기가 다른 두 글자(안내·가져오기)를 배너 세로 가운데에 맞춘다
        o_lay.addWidget(self.overdue_label, 1, Qt.AlignmentFlag.AlignVCenter)
        o_lay.addWidget(self.overdue_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.overdue_bar.hide()
        overdue_wrap = QVBoxLayout()
        overdue_wrap.setContentsMargins(10, 0, 10, 4)
        overdue_wrap.addWidget(self.overdue_bar)
        card_lay.addLayout(overdue_wrap)

        # 월간 보기: 달력 + 고른 날 제목 (아래 목록은 고른 날 하루치)
        self.month_panel = QWidget(self.card)
        self.month_panel.setObjectName("monthPanel")
        mp_lay = QVBoxLayout(self.month_panel)
        mp_lay.setContentsMargins(10, 0, 10, 0)
        mp_lay.setSpacing(0)
        self.calendar = MonthCalendar(MIME_TODO, self.month_panel)
        self.calendar.day_clicked.connect(self.store.set_day)
        self.calendar.day_double_clicked.connect(self.open_day)
        # 끌기(drag)가 끝난 뒤에 옮긴다 (끄는 중에 목록을 다시 그리면 끌던 줄이 사라짐)
        self.calendar.todo_dropped.connect(self.move_todo, Qt.ConnectionType.QueuedConnection)
        mp_lay.addWidget(self.calendar)
        # 고른 날 제목 + 목록 보이기/숨기기 (달력만 보고 싶을 때)
        caption_row = QFrame(self.month_panel)
        caption_row.setObjectName("monthCaptionRow")
        cr_lay = QHBoxLayout(caption_row)
        cr_lay.setContentsMargins(12, 6, 4, 0)
        self.month_caption = ElideLabel(caption_row)
        self.month_caption.setObjectName("monthCaption")
        cr_lay.addWidget(self.month_caption, 1, Qt.AlignmentFlag.AlignVCenter)
        self.list_toggle = QToolButton(caption_row)
        self.list_toggle.setObjectName("listToggle")
        self.list_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_toggle.clicked.connect(self.toggle_month_list)
        cr_lay.addWidget(self.list_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        mp_lay.addWidget(caption_row)
        self.month_panel.hide()
        card_lay.addWidget(self.month_panel)

        self.list_view = TodoListView(store, self.card)
        self.list_view.day_clicked.connect(self.store.set_day)  # 주간: 이 날에 추가
        self.list_view.day_double_clicked.connect(self.open_day)
        card_lay.addWidget(self.list_view, 1)
        self._wire_list(self.list_view)

        # 주간 보기 + 넓은 노트: 가로 7칸 보드
        self.board = WeekBoard(store, self.card)
        self.board.day_clicked.connect(self.store.set_day)
        self.board.day_double_clicked.connect(self.open_day)
        self.board.hide()
        card_lay.addWidget(self.board, 1)
        # 월간에서 목록을 숨겼을 때 입력칸을 아래에 붙여 두는 빈 공간
        self.month_filler = QWidget(self.card)
        self.month_filler.hide()
        card_lay.addWidget(self.month_filler, 1)

        # 알림 (삭제 되돌리기 등)
        self.toast = QFrame(self.card)
        self.toast.setObjectName("toast")
        t_lay = QHBoxLayout(self.toast)
        t_lay.setContentsMargins(14, 9, 12, 9)
        self.toast_label = QLabel(self.toast)
        self.toast_label.setTextFormat(Qt.TextFormat.PlainText)
        self.toast_undo = QToolButton(self.toast)
        self.toast_undo.setObjectName("toastButton")
        self.toast_undo.setText("되돌리기")
        self.toast_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toast_undo.clicked.connect(self.undo_delete)
        t_lay.addWidget(self.toast_label, 1)
        t_lay.addWidget(self.toast_undo)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        card_lay.addWidget(self.toast)

        bottom = QWidget(self.card)
        bottom.setObjectName("bottomBar")
        b_lay = QHBoxLayout(bottom)
        b_lay.setContentsMargins(12, 8, 14, 14)
        b_lay.setSpacing(8)
        self.input = TodoInput(self._max_input_level, bottom)
        self.input.submitted.connect(self._add)
        b_lay.addWidget(self.input, 1)
        self.count_label = self.title_bar.count_label  # 완료 개수는 제목 바 진행 막대 위에
        card_lay.addWidget(bottom)

        # 크기 조절 손잡이: 레이아웃 밖에서 카드 오른쪽 아래 모서리에 붙인다
        self.grip = QSizeGrip(self.card)
        self.grip.setFixedSize(18, 18)
        self.card.installEventFilter(self)

        for seq, delta in (("Alt+Left", -1), ("Alt+Right", 1)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda d=delta: self.go_day(d))

        for seq, mode in (("Ctrl+1", "day"), ("Ctrl+2", "week"), ("Ctrl+3", "month")):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda m=mode: self.set_view_mode(m))

        help_key = QShortcut(QKeySequence("F1"), self)
        help_key.setContext(Qt.ShortcutContext.WindowShortcut)
        help_key.activated.connect(self.open_help_window)

        manager = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        manager.setContext(Qt.ShortcutContext.WindowShortcut)
        manager.activated.connect(self.open_manager_window)

        store.changed.connect(self._on_store_changed)
        store.save_failed.connect(lambda msg: self.show_toast(f"저장 실패: {msg}"))

        self.apply_settings()
        self._restore_geometry()

        # 날짜가 바뀌었는지 1분마다 확인해서 지난 날 완료한 항목을 보관
        self._day_timer = QTimer(self)
        self._day_timer.setInterval(60_000)
        self._today = today_str()
        self._day_timer.timeout.connect(self.check_new_day)
        self._day_timer.start()
        QTimer.singleShot(0, self.auto_archive)
        self._restoring = False

    # ------------------------------------------------------------ 설정 반영

    def _apply_window_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.settings["always_on_top"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def note_palette(self) -> dict:
        """노트를 그릴 색들."""
        s = self.settings
        return {
            "card": s["bg_color"],
            "title": s["title_color"],
            "text": s["text_color"],
            "solid": s["bg_color"],
            "accent": s["accent_color"],
            "border": "none",
            "radius": s["corner_radius"],
            "shadow": s["shadow"],
        }

    def apply_settings(self) -> None:
        self._apply_style()
        # 목록은 글자 크기에 맞춰 할 일 두어 줄은 보이는 높이 아래로 줄지 않게
        line = QFontMetrics(self.font()).height()
        self.list_view.setMinimumHeight(max(LIST_MIN_HEIGHT, round(line * 3.5)))
        s = self.settings
        pal = self.note_palette()
        spacing = {"compact": 1, "normal": 4, "relaxed": 8}[s["line_spacing"]]
        self.list_view.style_opts = {
            "row_pad": spacing,
            "check_style": s["check_style"],
            "done_style": s["done_style"],
            "text_color": pal["text"],
            "bg_color": pal["solid"],
            "accent_color": pal["accent"],
        }
        self._update_title_visibility()
        if self._manager_win is not None:
            self._manager_win.apply_style()  # 강조색을 관리 창에도
        self.sync_hotkey()
        self._on_store_changed()

    def _apply_style(self) -> None:
        s = self.settings
        family = resolve_font_family(s["font_family"])
        font = QFont(family)
        font.setPointSize(s["font_size"])
        self.setFont(font)

        pal = self.note_palette()
        card, title, text = pal["card"], pal["title"], pal["text"]
        accent = pal["accent"]
        r = pal["radius"]
        size = s["font_size"]
        # 보조 글자는 배경색에 맞춰 4.5:1 이상 나오는 진하기로 (회색 배경이면 자동으로 더 진하게)
        soft = _rgba(text, soft_alpha(text, card))
        title_soft = _rgba(text, soft_alpha(text, title))
        # 강조색 위 글자('오늘' 표시): 흰색과 진한 글자 중 대비가 큰 쪽
        on_accent = max((text, "#FFFFFF"), key=lambda c: contrast(c, accent))
        sunday = "#B3261E" if contrast("#B3261E", card) >= 4.5 else text
        self.calendar.set_colors(
            text=text,
            soft=_mix(text, card, soft_alpha(text, card)),
            accent=accent,
            on_accent=on_accent,
            current_bg=_mix(accent, card, 0.12),
            drop=accent,
            sunday=sunday,
            done_dot=_mix(text, card, ring_alpha(text, card)),
        )
        self.title_bar.set_colors(text, accent, title)
        self.input.set_colors(text, accent)
        # 카드 안쪽에만 적용한다. 노트 창 전체에 걸면 메뉴·알림창까지 노트 글자색을 물려받아
        # 다크모드(검은 배경)에서 글자가 안 보인다.
        self.card.setStyleSheet(f"""
            QWidget {{
                color: {text}; font-family: "{family}"; font-size: {size}pt;
            }}
            #card {{ background: {card}; border-radius: {r}px; border: {pal["border"]}; }}
            #titleBar {{
                background: {title};
                border-top-left-radius: {r}px; border-top-right-radius: {r}px;
            }}
            #titleLabel {{ font-size: {size + 4}pt; font-weight: 700; }}
            #titleSub {{ color: {title_soft}; font-size: {max(size - 1, 10)}pt; }}
            #titleButton {{ border: none; background: transparent; border-radius: 16px; }}
            #titleButton:hover {{ background: {_rgba(text, 0.08)}; }}
            #titleButton:checked {{ background: {_rgba(accent, 0.14)}; }}
            #todoList, #rowContainer, #bottomBar {{ background: transparent; }}
            #todoList > QWidget > QWidget {{ background: transparent; }}
            #row {{ border-radius: 10px; }}
            #row:hover {{ background: {_rgba(text, 0.05)}; }}
            #row[selected="true"] {{ background: {_rgba(accent, 0.14)}; }}
            #row QLabel[dim="true"] {{ color: {_rgba(text, DONE_TEXT_ALPHA)}; }}
            #row QLabel[strike="true"] {{ text-decoration: line-through; }}
            #rowDelete {{ border: none; background: transparent; }}
            #emptyLabel {{
                color: {soft}; padding: 20px 14px;
                font-size: {max(size - 1, 10)}pt;
            }}
            #overdueBar {{ background: {_rgba(accent, 0.12)}; border-radius: 10px; }}
            #overdueLabel {{ color: {_rgba(text, 0.8)}; font-size: {max(size - 1, 10)}pt; }}
            #overdueButton {{
                border: none; background: transparent; color: {accent}; font-weight: 700;
                padding: 0;
                font-size: {max(size - 1, 10)}pt;
            }}
            #dropIndicator {{ background: {accent}; border-radius: 1px; }}
            #modeSwitch {{ background: {_rgba("#767680", INPUT_TINT_ALPHA)}; border-radius: 8px; }}
            #modeButton {{
                border: none; background: transparent; border-radius: 6px;
                color: {soft}; font-size: {max(size - 1, 10)}pt;
            }}
            #modeButton:checked {{ background: #FFFFFF; color: {text}; font-weight: 600; }}
            #dayHeader {{ background: transparent; }}
            #dayHeaderBox {{ border-radius: 10px; background: transparent; }}
            #dayHeaderBox:hover {{ background: {_rgba(text, 0.05)}; }}
            #dayHeaderBox[current="true"] {{ background: {_rgba(accent, 0.12)}; }}
            #dayLabel {{ color: {soft}; font-weight: 600; font-size: {max(size - 1, 10)}pt; }}
            #dayLabel[current="true"] {{ color: {text}; font-weight: 700; }}
            #todayPill {{
                background: {accent}; color: {on_accent}; border-radius: 6px;
                padding: 0 6px; font-weight: 700; font-size: {max(size - 3, 8)}pt;
            }}
            #boardColumn {{ background: {_rgba(text, 0.035)}; border-radius: 10px; }}
            #boardColumn[today="true"] {{ background: rgba(255, 255, 255, 0.55); }}
            #boardColumn[current="true"] {{ border: 1.5px solid {_rgba(accent, 0.55)}; }}
            #boardEmpty {{ color: {soft}; padding: 6px 0; font-size: {max(size - 2, 9)}pt; }}
            #monthCaptionRow {{ border-top: 1px solid {_rgba(text, 0.08)}; }}
            #monthCaption {{ font-weight: 700; }}
            #listToggle {{
                border: none; background: transparent; border-radius: 7px; padding: 3px 8px;
                color: {soft}; font-size: {max(size - 2, 9)}pt;
            }}
            #listToggle:hover {{ background: {_rgba(text, 0.06)}; color: {text}; }}
            #dayCount {{ color: {soft}; font-size: {max(size - 2, 9)}pt; }}
            #dayEmpty {{ color: {soft}; padding: 2px 0 4px 36px; font-size: {max(size - 2, 9)}pt; }}
            QLineEdit {{
                background: {_rgba("#767680", INPUT_TINT_ALPHA)}; border: none; border-radius: 10px;
                padding: 5px 8px; selection-background-color: {_rgba(accent, 0.35)};
            }}
            #todoInput {{ padding: 7px 6px; }}
            #countLabel {{
                color: {soft}; font-size: {max(size - 1, 10)}pt;
            }}
            #toast {{
                background: rgba(28, 28, 30, 0.88); border-radius: 12px; margin: 0 8px 6px 8px;
            }}
            #toast QLabel {{ color: #FFFFFF; font-size: {max(size - 1, 10)}pt; }}
            #toastButton {{
                border: none; background: transparent; color: {accent}; font-weight: 700;
                font-size: {max(size - 2, 9)}pt;
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 1px; }}
            QScrollBar::handle:vertical {{
                background: {_rgba(text, 0.18)}; border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            QSizeGrip {{ width: 18px; height: 18px; }}
            QToolTip {{
                color: #FFFFFF; background: rgba(28, 28, 30, 0.92); border: none;
                border-radius: 6px; padding: 4px 8px;
            }}
        """)

        # 입력칸 안내 글씨("새로운 할 일")도 4.5:1 이상으로 (스타일시트로는 못 바꿔서 팔레트로)
        # 입력칸은 카드보다 살짝 어두운 회색이 깔리므로 그 위에서 잰다
        input_bg = _mix("#767680", card, INPUT_TINT_ALPHA)
        placeholder = self.input.palette()
        placeholder.setColor(
            QPalette.ColorRole.PlaceholderText,
            QColor(_mix(text, input_bg, soft_alpha(text, input_bg))),
        )
        self.input.setPalette(placeholder)

        # 그림자는 paintEvent에서 카드 바깥 여백에 직접 그린다.
        # (QGraphicsDropShadowEffect는 카드를 그림으로 캐시해서 색 변경이 안 보이고
        #  글자 렌더링도 틀어지는 문제가 있었다)
        # 그림자를 꺼도 여백은 그대로 둔다 (끄는 순간 노트가 커지면 어색함)
        self._outer.setContentsMargins(SHADOW_PX, SHADOW_PX, SHADOW_PX, SHADOW_PX)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        pal = self.note_palette()
        if not pal["shadow"]:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.card.geometry())
        radius = pal["radius"]
        # 바깥에서 안쪽으로 아주 옅은 둥근 사각형을 겹쳐 그린다. 겹친 결과의 농도가
        # 가장자리에서 바깥으로 갈수록 곡선(제곱)으로 줄어들도록 겹마다 농도를 다르게 준다.
        spread = SHADOW_PX - SHADOW_OFFSET_Y
        for i in range(spread, 0, -1):
            outer = SHADOW_MAX_ALPHA * (1 - i / spread) ** 2
            inner = SHADOW_MAX_ALPHA * (1 - (i - 1) / spread) ** 2
            color = QColor(0, 0, 0)
            color.setAlphaF(max(inner - outer, 0.0))
            p.setBrush(color)
            r = rect.adjusted(-i, -i + SHADOW_OFFSET_Y, i, i + SHADOW_OFFSET_Y)
            p.drawRoundedRect(r, radius + i, radius + i)

    # 제목 바: 항상 표시 / 마우스 올릴 때만

    def _update_title_visibility(self, hovered: bool | None = None) -> None:
        if hovered is None:
            hovered = self.isVisible() and self.geometry().contains(QCursor.pos())
        show = self.settings["title_bar_mode"] == "always" or hovered
        self.title_bar.setVisible(show)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover_timer.stop()
        self._update_title_visibility(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover_timer.start()  # 살짝 늦게 숨겨서 깜빡임 방지
        super().leaveEvent(e)

    def _on_store_changed(self) -> None:
        day = self.store.current_day
        board = self.board_active
        self.board.setVisible(board)
        self.list_view.setVisible(not board)
        if board:
            days = week_days(day)
            self.list_view.days = None
            self.board.style_opts = self.list_view.style_opts
            self.board.rebuild(days, self._wire_list)
            todos = [t for d in days for t in self.store.todos(d)]
            self.title_bar.set_progress(sum(t.done for t in todos), len(todos))
            self.title_bar.set_title(*week_title(day))
            self.input.set_target(day_label(day, short=True))
        elif self.view_mode == "week":
            days = week_days(day)
            self.list_view.days = days
            self.list_view.rebuild()
            todos = [t for d in days for t in self.store.todos(d)]
            self.title_bar.set_progress(sum(t.done for t in todos), len(todos))
            self.title_bar.set_title(*week_title(day))
            self.input.set_target(day_label(day, short=True))
        elif self.view_mode == "month":
            self.list_view.days = None
            self.list_view.rebuild()
            todos = [t for d in month_days(day) for t in self.store.todos(d)]
            self.title_bar.set_progress(sum(t.done for t in todos), len(todos))
            self.title_bar.set_title(*month_title(day))
            self.input.set_target(day_label(day, short=True))
            self._update_calendar()
        else:
            self.list_view.days = None
            self.list_view.rebuild()
            c = self.store.counts(day)
            self.title_bar.set_progress(c["done"], c["all"])
            self.title_bar.set_title(*day_title(day))
            self.input.set_target("")
        self.month_panel.setVisible(self.view_mode == "month")
        overdue = len(self.store.overdue()) if day == today_str() else 0
        self.overdue_label.setText(f"지난 날 못 끝낸 일 {overdue}개")
        self.overdue_bar.setVisible(overdue > 0)
        self._apply_month_list()
        if self.input.level > self._max_input_level():
            self.input.set_level(self.input.level)

    # ------------------------------------------------------------ 월간: 달력만 보기

    def month_list_height(self) -> int:
        """월간에서 목록을 보이려면 필요한 노트 높이 (달력·입력칸 등 + 목록 최소 높이)."""
        lay = self.card.layout()
        used = 0
        for i in range(lay.count()):
            item = lay.itemAt(i)
            w = item.widget()
            if w is None:  # 배너를 감싼 레이아웃
                if item.layout() is not None and not self.overdue_bar.isHidden():
                    used += item.layout().sizeHint().height()
                continue
            if w in (self.list_view, self.month_filler, self.board):
                continue
            # 숨긴 것은 빼되, 제목 바는 마우스를 올리면 나타나므로 늘 센다 (깜빡임 방지)
            if w.isHidden() and w is not self.title_bar:
                continue
            used += w.sizeHint().height()
        return used + MONTH_LIST_MIN + 2 * SHADOW_PX

    def month_list_shown(self) -> bool:
        """목록을 보일지: 켜 두었고(토글) 노트가 충분히 높을 때."""
        return self.settings["month_list"] and self.height() >= self.month_list_height()

    def _apply_month_list(self) -> None:
        month = self.view_mode == "month"
        if not month:
            self.month_filler.hide()
            return
        shown = self.month_list_shown()
        self.list_view.setVisible(shown)
        self.month_filler.setVisible(not shown)
        self.list_toggle.setText("목록 숨기기" if shown else "목록 보기")
        if shown:
            tip = "달력만 보기"
        elif self.settings["month_list"]:
            tip = "노트가 낮아서 목록을 숨겼어요. 누르면 노트를 늘려서 보여줘요"
        else:
            tip = "고른 날의 할 일 목록 보기"
        self.list_toggle.setToolTip(tip)

    def toggle_month_list(self) -> None:
        """목록 숨기기 ↔ 보기. 노트가 낮아서 숨겨졌던 거면 노트를 늘려서 보여준다."""
        if self.month_list_shown():
            self.settings.set("month_list", False)
        else:
            self.settings.set("month_list", True)
            need = self.month_list_height() + 120  # 할 일 몇 줄 보일 만큼
            if self.height() < need:
                self.resize(self.width(), need)
        self._apply_month_list()
        self.schedule_settings_save()

    def _wire_list(self, lv: TodoListView) -> None:
        """할 일 목록(노트 목록·보드 칸)의 신호를 노트 동작에 잇는다."""
        lv.delete_done.connect(lambda n: self.show_toast(f"{n}개 삭제됨", undo=True))
        lv.menu_requested.connect(lambda tid, pos: self.build_row_menu(tid).exec(pos))
        lv.undo_requested.connect(self.undo_delete)

    @property
    def board_active(self) -> bool:
        """주간 보기에서 노트가 넓으면(960px 이상) 가로 보드."""
        return self.view_mode == "week" and self.width() >= BOARD_MIN_WIDTH

    def _views(self) -> list[TodoListView]:
        return self.board.lists if self.board_active else [self.list_view]

    def _view_for(self, todo_id: str) -> TodoListView:
        """그 할 일이 보이는 목록 (보드면 그 날 칸)."""
        return next((v for v in self._views() if v._row(todo_id)), self.list_view)

    def _update_calendar(self) -> None:
        day = self.store.current_day
        month = date.fromisoformat(day).month
        today = today_str()
        cells = []
        for i, d in enumerate(month_grid(day)):
            todos = self.store.todos(d)
            done = sum(t.done for t in todos)
            left = len(todos) - done
            parts = [_plain_day(d)]
            if d == today:
                parts.append("오늘")
            parts.append(f"남은 일 {left} · 끝낸 일 {done}" if todos else "할 일 없음")
            cells.append(
                Cell(
                    day=d,
                    number=date.fromisoformat(d).day,
                    in_month=date.fromisoformat(d).month == month,
                    today=d == today,
                    current=d == day,
                    open=left,
                    done=done,
                    sunday=i % 7 == 0,
                    tip=" · ".join(parts) + "\n누르면 이 날 목록 · 더블클릭: 이 날만 보기",
                )
            )
        self.calendar.set_cells(cells)
        todos = self.store.todos(day)
        rel = day_label(day, short=True)
        caption = _plain_day(day) + (f" · {rel}" if rel in ("오늘", "내일", "어제") else "")
        if todos:
            caption += f"  {sum(t.done for t in todos)}/{len(todos)}"
        self.month_caption.setText(caption)

    def _max_input_level(self) -> int:
        todos = self.store.todos()
        return todos[-1].indent + 1 if todos else 0

    # ------------------------------------------------------------ 동작

    def _add(self, text: str, level: int) -> None:
        todo = self.store.add(text, level)
        if self.board_active:
            self._view_for(todo.id).scroll_to(todo.id)
        elif self.view_mode == "week":
            # 고른 날 구역 끝에 들어가므로, 목록 맨 아래가 아니라 그 항목이 보이게
            self.list_view.scroll_to(todo.id)
        else:
            self.list_view.scroll_to_bottom()

    # ------------------------------------------------------------ 날짜 페이지

    def go_day(self, delta: int) -> None:
        """‹ ›: 하루 보기는 하루씩, 주간은 한 주씩, 월간은 한 달씩 (같은 날짜로)."""
        if self.view_mode == "month":
            self.store.set_day(shift_month(self.store.current_day, delta))
            return
        step = VIEW_STEP[self.view_mode]
        self.store.set_day(_shift_day(self.store.current_day, delta * step))

    def open_day(self, day: str) -> None:
        """주간·월간 보기에서 날짜 더블클릭: 그 날 하루 보기로."""
        self.store.set_day(day)
        self.set_view_mode("day")

    def set_view_mode(self, mode: str) -> None:
        """일 / 주 / 월 보기 전환. 노트 크기는 보기마다 따로 기억한다 (위치는 같이)."""
        if mode not in VIEW_NAMES:
            return
        self.title_bar.set_mode(mode)
        if mode == self.view_mode:
            return
        self.view_mode = mode
        self.settings.set("view_mode", mode)
        w_key, h_key = self._size_keys()
        self._restoring = True  # 크기를 바꾸는 동안 다른 보기의 크기에 덮어쓰지 않게
        self.resize(self.settings[w_key], self.settings[h_key])
        self._restoring = False
        self._on_store_changed()
        self.schedule_settings_save()

    def go_today(self) -> None:
        self.store.set_day(today_str())

    def bring_overdue(self) -> None:
        n = self.store.bring_overdue()
        if n:
            self.show_toast(f"지난 날 못 끝낸 일 {n}개를 가져왔어요")

    def check_new_day(self) -> None:
        """1분마다: 자정이 지났으면 보던 '오늘'을 새 오늘로 넘기고 자동 보관."""
        today = today_str()
        if today != self._today:
            if self.store.current_day == self._today:
                self.store.set_day(today)
            self._today = today
        self.auto_archive()

    def set_always_on_top(self, on: bool) -> None:
        self.settings.set("always_on_top", on)
        geo = self.geometry()
        self._apply_window_flags()
        self.setGeometry(geo)
        self.show()
        self.schedule_settings_save()

    def undo_delete(self) -> None:
        n = self.store.undo_delete()
        if n:
            self.show_toast(f"{n}개 복구됨")

    def show_toast(self, text: str, undo: bool = False) -> None:
        self.toast_label.setText(text)
        self.toast_undo.setVisible(undo)
        self.toast.show()
        self._toast_timer.start(TOAST_MS)

    def apply_preset(self, name: str) -> None:
        self.settings.apply_preset(name)
        self.apply_settings()
        self.schedule_settings_save()

    # ------------------------------------------------------------ 메뉴

    def _show_menu(self) -> None:
        menu = self.build_menu()
        btn = self.title_bar.menu_btn
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        undo = menu.addAction("방금 지운 것 되돌리기\tCtrl+Z", self.undo_delete)
        undo.setEnabled(self.store.can_undo())
        menu.addSeparator()
        menu.addAction("텍스트 파일로 저장…", self.export_markdown_file)
        menu.addAction("목록 복사 (메모장·카톡에 붙여넣기)", self.copy_markdown)
        menu.addAction("텍스트 파일 불러오기…", self.import_markdown_file)
        menu.addSeparator()
        act = menu.addAction("전체 목록 보기", self.open_manager_window)
        act.setShortcut(QKeySequence("Ctrl+Shift+L"))
        colors = menu.addMenu("색상")
        for key, label in PRESET_LABELS.items():
            a = colors.addAction(label, lambda k=key: self.apply_preset(k))
            a.setCheckable(True)
            a.setChecked(all(self.settings[f] == v.upper() for f, v in PRESETS[key].items()))
        archive = menu.addAction("완료 항목 보관", self.archive_completed)
        archive.setEnabled(self.store.counts(self.store.current_day)["done"] > 0)
        menu.addSeparator()
        menu.addAction("설정…", self.open_settings_window)
        help_act = menu.addAction("도움말 · 단축키", self.open_help_window)
        help_act.setShortcut(QKeySequence("F1"))
        auto = menu.addAction("시작 시 자동 실행")
        auto.setCheckable(True)
        auto.setEnabled(self.autostart is not None)
        auto.setChecked(self.autostart is not None and self.autostart.is_enabled())
        auto.toggled.connect(self.set_autostart)
        menu.addSeparator()
        menu.addAction("종료", self.quit_app)
        return menu

    def open_settings_window(self) -> None:
        from .settings_window import SettingsWindow

        if self._settings_win is None:
            self._settings_win = SettingsWindow(self)
            self._settings_win.destroyed.connect(self._forget_settings_window)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def open_help_window(self) -> None:
        from .help_window import HelpWindow

        if self._help_win is None:
            self._help_win = HelpWindow(self.store.todos_file.parent, self)
            self._help_win.destroyed.connect(self._forget_help_window)
        self._help_win.show()
        self._help_win.raise_()
        self._help_win.activateWindow()

    def _forget_help_window(self) -> None:
        self._help_win = None

    def open_manager_window(self) -> None:
        from .manager_window import ManagerWindow

        if self._manager_win is None:
            self._manager_win = ManagerWindow(
                self.store, self.settings, on_settings_changed=self.schedule_settings_save
            )
            self._manager_win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self._manager_win.destroyed.connect(self._forget_manager_window)
        win = self._manager_win
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()

    def _forget_manager_window(self) -> None:
        self._manager_win = None

    def _forget_settings_window(self) -> None:
        self._settings_win = None

    # ------------------------------------------------------------ 할 일 우클릭 메뉴

    def build_row_menu(self, todo_id: str) -> QMenu:
        todo = self.store.get(todo_id)
        menu = QMenu(self)
        if todo is None:
            return menu
        today = today_str()
        day = todo.day
        menu.addAction("고치기\t더블클릭", lambda: self._view_for(todo_id).begin_edit(todo_id))
        menu.addAction(
            "완료 취소" if todo.done else "완료 처리",
            lambda: self.store.set_done(todo_id, not todo.done),
        )
        menu.addAction("하위 할 일로\tTab", lambda: self.store.indent(todo_id))
        menu.addAction("상위로 꺼내기\tShift+Tab", lambda: self.store.outdent(todo_id))
        menu.addSeparator()
        later = "내일로 미루기" if day == today else "다음 날로 미루기"
        menu.addAction(later, lambda: self.move_todo(todo_id, _shift_day(day, 1)))
        if day != today:
            menu.addAction("오늘로 옮기기", lambda: self.move_todo(todo_id, today))
        other = menu.addMenu("다른 날로 옮기기")
        for d in week_days(day):
            if d != day:
                other.addAction(day_label(d), lambda d=d: self.move_todo(todo_id, d))
        other.addSeparator()
        next_week = _shift_day(day, 7)
        other.addAction(
            f"다음 주 같은 요일 ({day_label(next_week, short=True)})",
            lambda: self.move_todo(todo_id, next_week),
        )
        menu.addAction("보관함으로", lambda: self.archive_todo(todo_id))
        menu.addSeparator()
        menu.addAction("삭제", lambda: self._view_for(todo_id).request_delete(todo_id))
        return menu

    def move_todo(self, todo_id: str, day: str) -> None:
        n = self.store.move_to_day([todo_id], day)
        if n:
            extra = f" (하위 {n - 1}개 포함)" if n > 1 else ""
            self.show_toast(f"{day_label(day, short=True)} 페이지로 옮겼어요{extra}")

    def archive_todo(self, todo_id: str) -> None:
        n = self.store.archive([todo_id])
        if n:
            self.show_toast(f"{n}개 보관함으로", undo=True)

    def archive_completed(self) -> None:
        n = self.store.archive_completed(day=self.store.current_day)
        if n:
            self.show_toast(f"완료 {n}개 보관함으로", undo=True)

    def auto_archive(self, today: date | None = None) -> int:
        """설정이 켜져 있으면 어제까지 완료한 항목을 보관한다."""
        if not self.settings["auto_archive"]:
            return 0
        n = self.store.archive_completed(before=today or date.today())
        if n:
            self.show_toast(f"지난 날 완료한 {n}개를 보관했어요", undo=True)
            if not self.settings["archive_hint_seen"]:
                self.settings.set("archive_hint_seen", True)
                self.schedule_settings_save()
                QMessageBox.information(
                    self,
                    "완료한 일을 보관했어요",
                    f"어제까지 완료한 할 일 {n}개를 보관함으로 옮겼어요. 지워진 게 아니에요.\n\n"
                    "☰ 메뉴 → 전체 목록 보기 → '보관됨'에서 다시 볼 수 있어요.\n"
                    "(설정 → 목록 동작에서 자동 보관을 끌 수 있어요)",
                )
        return n

    def page_title(self) -> str:
        """마크다운 제목: '11월 24일 (월) 할 일' · '11월 22일 – 28일 할 일' · '2026년 11월 할 일'"""
        day = self.store.current_day
        if self.view_mode == "week":
            return f"{week_title(day)[1]} 할 일"
        if self.view_mode == "month":
            title, year = month_title(day)
            return f"{year} {title} 할 일"
        return f"{_plain_day(day)} 할 일"

    def export_days(self) -> list[str]:
        """내보낼 날짜들: 하루 보기는 그 날, 주간은 그 주 전체, 월간은 그 달 전체."""
        if self.view_mode == "week":
            return week_days(self.store.current_day)
        if self.view_mode == "month":
            return month_days(self.store.current_day)
        return [self.store.current_day]

    def markdown_text(self) -> str:
        days = self.export_days()
        if len(days) == 1:
            return markdown_io.export_markdown(self.store.todos(days[0]), title=self.page_title())
        # 여러 날: 날짜마다 소제목 (할 일 없는 날은 뺀다)
        sections = [(_plain_day(d), self.store.todos(d)) for d in days]
        return markdown_io.export_sections(
            [(h, t) for h, t in sections if t], title=self.page_title()
        )

    def copy_markdown(self) -> None:
        QGuiApplication.clipboard().setText(self.markdown_text())
        self.show_toast("목록을 복사했어요. 메모장이나 카톡에 붙여넣기 하세요")

    def export_markdown_file(self) -> None:
        folder = default_export_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        days = self.export_days()
        kind = {"week": "주간 할 일", "month": "월간 할 일"}.get(self.view_mode, "할 일")
        name = markdown_io.default_filename(kind, date.fromisoformat(days[0]))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "텍스트 파일로 저장",
            str(folder / name),
            "텍스트 파일 (*.txt);;마크다운 (*.md);;모든 파일 (*)",
        )
        if not path:
            return
        try:
            markdown_io.save_markdown(Path(path), self.markdown_text())
        except OSError as e:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장하지 못했어요.\n{e}")
            return
        self.show_toast(f"{Path(path).name} 저장됨")

    def import_markdown_file(self) -> None:
        folder = default_export_dir()
        if not folder.is_dir():
            folder = folder.parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            "텍스트 파일 불러오기",
            str(folder),
            "텍스트 파일 (*.txt *.md *.markdown);;모든 파일 (*)",
        )
        if not path:
            return
        try:
            items = markdown_io.read_markdown_file(Path(path))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "불러오기 실패", f"파일을 읽지 못했어요.\n{e}")
            return
        if not items:
            QMessageBox.information(
                self,
                "불러오기",
                "불러올 할 일을 찾지 못했어요.\n'- [ ] 할 일' 처럼 시작하는 줄만 불러올 수 있어요.",
            )
            return
        items = self.confirm_import(items)
        if not items:
            return
        n = self.store.add_many(items)
        self.list_view.scroll_to_bottom()
        self.show_toast(f"{n}개 불러옴")

    def confirm_import(self, items: list) -> list:
        """많으면 먼저 묻고, 최대 개수를 넘으면 앞부분만 불러온다. 취소하면 빈 목록."""
        total = len(items)
        limit = markdown_io.MAX_IMPORT_ITEMS
        if total <= markdown_io.CONFIRM_IMPORT_ITEMS:
            return items
        if total > limit:
            text = (
                f"할 일이 {total}개 있어요. 한 번에 {limit}개까지만 불러올 수 있어요.\n"
                f"앞의 {limit}개만 불러올까요?"
            )
        else:
            text = f"할 일 {total}개를 지금 페이지에 불러올까요?"
        answer = QMessageBox.question(self, "불러오기", text)
        if answer != QMessageBox.StandardButton.Yes:
            return []
        return items[:limit]

    # ------------------------------------------------------------ 창 위치/크기

    def _size_keys(self) -> tuple[str, str]:
        """지금 보기의 크기 설정 키. 하루 보기는 예전 키(window_w/h)를 그대로 쓴다."""
        if self.view_mode in ("week", "month"):
            return f"{self.view_mode}_w", f"{self.view_mode}_h"
        return "window_w", "window_h"

    def _restore_geometry(self) -> None:
        s = self.settings
        w_key, h_key = self._size_keys()
        width = s[w_key]
        self.resize(width, s[h_key])
        x, y = s["window_x"], s["window_y"]
        if x is not None and y is not None:
            center = QPoint(x + width // 2, y + 20)
            if QGuiApplication.screenAt(center) is not None:
                self.move(x, y)
                return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - width - 40, area.top() + 40)

    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        self._remember_geometry()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._remember_geometry()
        if self.view_mode == "week" and self.board.isVisible() != self.board_active:
            self._on_store_changed()  # 960px을 넘나들면 가로 보드 ↔ 세로 목록
        elif self.view_mode == "month":
            self._apply_month_list()  # 낮아지면 달력만

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.card and e.type() == QEvent.Type.Resize:
            self.grip.move(
                self.card.width() - self.grip.width(), self.card.height() - self.grip.height()
            )
            self.grip.raise_()
        return super().eventFilter(obj, e)

    def _remember_geometry(self) -> None:
        if self._restoring:
            return
        g = self.geometry()
        self.settings.set("window_x", g.x())
        self.settings.set("window_y", g.y())
        w_key, h_key = self._size_keys()
        self.settings.set(w_key, g.width())
        self.settings.set(h_key, g.height())
        self.schedule_settings_save()

    def schedule_settings_save(self) -> None:
        self._settings_timer.start()

    def save_settings(self) -> None:
        self._settings_timer.stop()
        try:
            self.settings.save(self.settings_path)
        except OSError as e:
            self.show_toast(f"설정 저장 실패: {e}")

    def set_autostart(self, on: bool) -> None:
        if self.autostart is None:
            return
        try:
            self.autostart.set_enabled(on)
        except OSError as err:
            self.show_toast(f"자동 실행 설정 실패: {err}")
            return
        self.show_toast("윈도우를 켜면 자동으로 실행돼요" if on else "자동 실행을 껐어요")

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def summon(self) -> None:
        """전역 단축키: 노트를 앞으로 꺼내고 입력칸에 커서."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def sync_hotkey(self) -> None:
        """설정에 맞춰 전역 단축키를 등록/해제. 다른 앱이 쓰고 있으면 알려준다."""
        if self.hotkey is None:
            return
        if self.settings["global_hotkey"]:
            if not self.hotkey.registered and not self.hotkey.register():
                self.show_toast("Ctrl+Alt+T는 다른 프로그램이 쓰고 있어서 사용할 수 없어요")
        else:
            self.hotkey.unregister()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.hide_to_tray and not self._quitting:
            # ✕는 트레이로 숨기기. 저장은 미리 해둔다.
            e.ignore()
            if not self.settings["tray_hint_seen"]:
                self.settings.set("tray_hint_seen", True)
                QMessageBox.information(
                    self,
                    "노트를 숨겨요",
                    "노트는 꺼지지 않고 작업표시줄 오른쪽(시계 옆 ^ 안)에 있어요.\n\n"
                    "• 노란 메모지 아이콘을 누르거나\n"
                    "• 아무 데서나 Ctrl+Alt+T를 누르면 다시 나타나요.\n\n"
                    "완전히 끄려면 ☰ 메뉴 → 종료를 누르세요.",
                )
            self.save_settings()
            self.store.flush()
            self.hide()
            self.hidden_to_tray.emit()
            return
        if self._manager_win is not None:
            self._manager_win.close()  # 관리 창 위치도 저장되도록 먼저 닫는다
        self.save_settings()
        self.store.flush()
        super().closeEvent(e)
        QApplication.quit()
