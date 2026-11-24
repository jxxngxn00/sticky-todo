"""바탕화면 스티키 노트 위젯."""

from __future__ import annotations

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
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
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
from .paths import default_export_dir
from .settings import PRESETS, Settings
from .storage import Todo, TodoStore, today_str

INDENT_PX = 20
MIME_TODO = "application/x-sticky-todo-id"
FALLBACK_FONTS = ["Pretendard", "맑은 고딕", "Malgun Gothic"]
PRESET_LABELS = {"yellow": "노랑", "pink": "분홍", "sky": "하늘", "mint": "민트"}
TOAST_MS = 5000
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
    """노트 제목 두 줄: ('오늘', '11월 24일 화요일') / ('11월 27일', '목요일')."""
    d = date.fromisoformat(day)
    today = today or date.today()
    rel = {0: "오늘", 1: "내일", -1: "어제"}.get((d - today).days)
    wd = f"{WEEKDAYS[d.weekday()]}요일"
    md = f"{d.month}월 {d.day}일"
    year = f"{d.year}년 " if d.year != today.year else ""
    if rel:
        return rel, f"{year}{md} {wd}"
    return md, f"{year}{wd}".strip()


def _shift_day(day: str, delta: int) -> str:
    return date.fromordinal(date.fromisoformat(day).toordinal() + delta).isoformat()


# ---------------------------------------------------------------- 드래그 위치 계산


def compute_drop(
    entries: list[tuple[str, int]], dragged_id: str, index: int, desired: int
) -> tuple[str | None, int]:
    """드래그한 묶음을 index 자리(그 행 앞)에 놓을 때 (before_id, indent)를 정한다.

    entries: 화면 순서의 (id, indent). index: 0..len(entries).
    놓인 묶음이 뒤따르는 항목을 하위로 빼앗지 않도록
    indent 범위는 [다음 항목 indent, 이전 항목 indent + 1].
    """
    ids = [e[0] for e in entries]
    i = ids.index(dragged_id)
    end = i + 1
    while end < len(entries) and entries[end][1] > entries[i][1]:
        end += 1

    if i <= index <= end:  # 제자리 (깊이만 바뀔 수 있음)
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

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(20, 20)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 2
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        checked = self.isChecked()
        ring = QColor(self.color)
        ring.setAlphaF(0.30)

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
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6 + visual_indent(todo.indent) * INDENT_PX, pad, 4, pad)
        lay.setSpacing(6)

        self.check = CheckButton(self)
        self.check.shape = style["check_style"]
        self.check.color = QColor(style["text_color"])
        self.check.accent = QColor(style["accent_color"])
        self.check.setChecked(todo.done)
        self.check.setFixedSize(20, 20)
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
            icons.icon("remove", _mix(style["text_color"], style["bg_color"], 0.28), 16)
        )
        self.delete_btn.setIconSize(QSize(16, 16))
        self.delete_btn.setToolTip("삭제")
        self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sp = self.delete_btn.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.delete_btn.setSizePolicy(sp)
        self.delete_btn.hide()
        self.delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.todo.id))
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
        if e.type() == QEvent.Type.HoverEnter:
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


class TodoListView(QScrollArea):
    """노트 순서 그대로 할 일을 보여주고, 키보드·드래그 조작을 TodoStore로 넘긴다."""

    delete_done = Signal(int)
    undo_requested = Signal()
    menu_requested = Signal(str, QPoint)

    def __init__(self, store: TodoStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
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

        self.empty_label = QLabel("할 일이 없어요", self.container)
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.insertWidget(0, self.empty_label)

        self.indicator = QFrame(self.container)
        self.indicator.setObjectName("dropIndicator")
        self.indicator.setFixedHeight(2)
        self.indicator.hide()

        self.rows: list[TodoRow] = []
        self.selected_id: str | None = None
        self.style_opts: dict = {}
        self._drag: tuple[str, int, int] | None = None  # (id, 시작 indent, 시작 x)
        self._pending_edit: str | None = None

    # ------------------------------------------------------------ 그리기

    def rebuild(self) -> None:
        for row in self.rows:
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        todos = self.store.todos()
        shown = arrange_rows(todos, self.style_opts.get("done_style", "strike"))
        for i, (todo, dim, strike) in enumerate(shown):
            row = TodoRow(todo, self.style_opts, self.container, dim=dim, strike=strike)
            row.toggled.connect(self.store.set_done)
            row.edited.connect(self._on_edited)
            row.delete_clicked.connect(self.request_delete)
            row.clicked.connect(self.select)
            row.menu_requested.connect(self._on_row_menu)
            row.drag_requested.connect(self._start_drag)
            row.indent_requested.connect(self._on_editor_indent)
            row.edit_closed.connect(self.setFocus)  # 바로 방향키로 이동할 수 있게
            self._layout.insertWidget(i, row)
            self.rows.append(row)
        self.empty_label.setText("완료한 할 일만 있어요" if todos else "할 일이 없어요")
        self.empty_label.setVisible(not shown)
        if self.selected_id and not any(r.todo.id == self.selected_id for r in self.rows):
            self.selected_id = None
        self._paint_selection()
        if self._pending_edit:
            row = self._row(self._pending_edit)
            self._pending_edit = None
            if row:
                row.begin_edit()

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

    def _drop_target(self, pos: QPoint) -> tuple[str | None, int, int] | None:
        if self._drag is None or not self.rows:
            return None
        dragged, start_indent, start_x = self._drag
        index = len(self.rows)
        for i, r in enumerate(self.rows):
            if pos.y() < r.geometry().center().y():
                index = i
                break
        desired = start_indent + round((pos.x() - start_x) / INDENT_PX)
        entries = [(r.todo.id, r.todo.indent) for r in self.rows]
        before, indent = compute_drop(entries, dragged, index, desired)
        return before, indent, index

    def update_drop(self, pos: QPoint) -> None:
        target = self._drop_target(pos)
        if target is None:
            return
        _, indent, index = target
        if index < len(self.rows):
            y = self.rows[index].geometry().top()
        else:
            y = self.rows[-1].geometry().bottom() + 1
        x = 6 + indent * INDENT_PX + 4
        self.indicator.setGeometry(x, max(0, y - 1), self.container.width() - x - 8, 2)
        self.indicator.show()
        self.indicator.raise_()

    def finish_drop(self, pos: QPoint) -> None:
        target = self._drop_target(pos)
        self.indicator.hide()
        if target is None or self._drag is None:
            return
        before, indent, _ = target
        self.store.move(self._drag[0], before_id=before, indent=indent)


# ---------------------------------------------------------------- 제목 바


class TitleBar(QWidget):
    """‹ 날짜 › 와 고정·메뉴·닫기 버튼. 끌면 창이 움직이고, 날짜를 더블클릭하면 오늘로."""

    prev_requested = Signal()
    next_requested = Signal()
    today_requested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._press: QPoint | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(0)

        self.prev_btn = self._button("이전 날짜 (Alt+←)")
        self.prev_btn.setFixedSize(24, 28)
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        self.label = QLabel(title, self)
        self.label.setObjectName("titleLabel")
        self.label.setToolTip("더블클릭하면 오늘로")
        # 좁은 창에서도 줄어들 수 있게 (넘치는 글자는 잘림)
        self.label.setMinimumWidth(20)
        self.sub = QLabel(self)
        self.sub.setObjectName("titleSub")
        self.sub.setMinimumWidth(20)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(6, 0, 6, 0)
        title_box.setSpacing(1)
        title_box.addWidget(self.label)
        title_box.addWidget(self.sub)
        self.next_btn = self._button("다음 날짜 (Alt+→)")
        self.next_btn.setFixedSize(24, 28)
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
            lay.addWidget(b)
        self._colors = ("#1C1C1E", "#FF9F0A", "#FFFFFF")
        self._paint_icons()

    def set_colors(self, text: str, accent: str, bg: str) -> None:
        self._colors = (text, accent, bg)
        self._paint_icons()

    def _paint_icons(self) -> None:
        text, accent, bg = self._colors
        soft = _mix(text, bg, 0.55)
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
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(28, 28)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def set_title(self, title: str, sub: str = "") -> None:
        self.label.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

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
        self._lead = self.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
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
            self.setPlaceholderText("새로운 할 일")

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
        self.setMinimumSize(200, 150)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._apply_window_flags()

        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(500)
        self._settings_timer.timeout.connect(self.save_settings)
        self._restoring = True
        self._settings_win = None
        self._manager_win = None
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
        o_lay.addWidget(self.overdue_label, 1)
        o_lay.addWidget(self.overdue_btn)
        self.overdue_bar.hide()
        overdue_wrap = QVBoxLayout()
        overdue_wrap.setContentsMargins(10, 0, 10, 4)
        overdue_wrap.addWidget(self.overdue_bar)
        card_lay.addLayout(overdue_wrap)

        self.list_view = TodoListView(store, self.card)
        self.list_view.delete_done.connect(lambda n: self.show_toast(f"{n}개 삭제됨", undo=True))
        self.list_view.menu_requested.connect(lambda tid, pos: self.build_row_menu(tid).exec(pos))
        card_lay.addWidget(self.list_view, 1)

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
        self.count_label = QLabel(bottom)
        self.count_label.setObjectName("countLabel")
        b_lay.addWidget(self.input, 1)
        b_lay.addWidget(self.count_label)
        card_lay.addWidget(bottom)

        # 크기 조절 손잡이: 레이아웃 밖에서 카드 오른쪽 아래 모서리에 붙인다
        self.grip = QSizeGrip(self.card)
        self.grip.setFixedSize(14, 14)
        self.card.installEventFilter(self)

        for seq, delta in (("Alt+Left", -1), ("Alt+Right", 1)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda d=delta: self.go_day(d))

        manager = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        manager.setContext(Qt.ShortcutContext.WindowShortcut)
        manager.activated.connect(self.open_manager_window)

        self.list_view.undo_requested.connect(self.undo_delete)

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
            #titleSub {{ color: {_rgba(text, 0.5)}; font-size: {max(size - 2, 9)}pt; }}
            #titleButton {{ border: none; background: transparent; border-radius: 14px; }}
            #titleButton:hover {{ background: {_rgba(text, 0.08)}; }}
            #titleButton:checked {{ background: {_rgba(accent, 0.14)}; }}
            #todoList, #rowContainer, #bottomBar {{ background: transparent; }}
            #todoList > QWidget > QWidget {{ background: transparent; }}
            #row {{ border-radius: 10px; }}
            #row:hover {{ background: {_rgba(text, 0.05)}; }}
            #row[selected="true"] {{ background: {_rgba(accent, 0.14)}; }}
            #row QLabel[dim="true"] {{ color: {_rgba(text, 0.38)}; }}
            #row QLabel[strike="true"] {{ text-decoration: line-through; }}
            #rowDelete {{ border: none; background: transparent; }}
            #emptyLabel {{ color: {_rgba(text, 0.35)}; padding: 24px; }}
            #overdueBar {{ background: {_rgba(accent, 0.12)}; border-radius: 10px; }}
            #overdueLabel {{ color: {_rgba(text, 0.75)}; font-size: {max(size - 2, 9)}pt; }}
            #overdueButton {{
                border: none; background: transparent; color: {accent}; font-weight: 700;
                font-size: {max(size - 2, 9)}pt;
            }}
            #dropIndicator {{ background: {accent}; border-radius: 1px; }}
            QLineEdit {{
                background: {_rgba("#767680", 0.12)}; border: none; border-radius: 10px;
                padding: 5px 8px; selection-background-color: {_rgba(accent, 0.35)};
            }}
            #todoInput {{ padding: 7px 6px; }}
            #countLabel {{ color: {_rgba(text, 0.45)}; font-size: {max(size - 2, 9)}pt; }}
            #toast {{
                background: rgba(28, 28, 30, 0.88); border-radius: 12px; margin: 0 8px 6px 8px;
            }}
            #toast QLabel {{ color: #FFFFFF; font-size: {max(size - 2, 9)}pt; }}
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
            QSizeGrip {{ width: 14px; height: 14px; }}
            QToolTip {{
                color: #FFFFFF; background: rgba(28, 28, 30, 0.92); border: none;
                border-radius: 6px; padding: 4px 8px;
            }}
        """)

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
        self.list_view.rebuild()
        day = self.store.current_day
        c = self.store.counts(day)
        self.count_label.setText(f"{c['done']}/{c['all']}")
        self.title_bar.set_title(*day_title(day))
        overdue = len(self.store.overdue()) if day == today_str() else 0
        self.overdue_label.setText(f"지난 날 못 끝낸 일 {overdue}개")
        self.overdue_bar.setVisible(overdue > 0)
        if self.input.level > self._max_input_level():
            self.input.set_level(self.input.level)

    def _max_input_level(self) -> int:
        todos = self.store.todos()
        return todos[-1].indent + 1 if todos else 0

    # ------------------------------------------------------------ 동작

    def _add(self, text: str, level: int) -> None:
        self.store.add(text, level)
        self.list_view.scroll_to_bottom()

    # ------------------------------------------------------------ 날짜 페이지

    def go_day(self, delta: int) -> None:
        self.store.set_day(_shift_day(self.store.current_day, delta))

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
        menu.addAction("마크다운으로 저장…", self.export_markdown_file)
        menu.addAction("마크다운 복사", self.copy_markdown)
        menu.addAction("마크다운 불러오기…", self.import_markdown_file)
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
        day = self.store.current_day
        menu.addAction("수정", lambda: self.list_view.begin_edit(todo_id))
        menu.addAction(
            "완료 취소" if todo.done else "완료 처리",
            lambda: self.store.set_done(todo_id, not todo.done),
        )
        menu.addAction("들여쓰기 (Tab)", lambda: self.store.indent(todo_id))
        menu.addAction("내어쓰기 (Shift+Tab)", lambda: self.store.outdent(todo_id))
        menu.addSeparator()
        later = "내일로 미루기" if day == today else "다음 날로 미루기"
        menu.addAction(later, lambda: self.move_todo(todo_id, _shift_day(day, 1)))
        if day != today:
            menu.addAction("오늘로 옮기기", lambda: self.move_todo(todo_id, today))
        menu.addAction("보관함으로", lambda: self.archive_todo(todo_id))
        menu.addSeparator()
        menu.addAction("삭제", lambda: self.list_view.request_delete(todo_id))
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
        return n

    def page_title(self) -> str:
        """마크다운 제목: '11월 24일 (월) 할 일'."""
        return f"{day_label(self.store.current_day).split(' · ')[-1]} 할 일"

    def markdown_text(self) -> str:
        return markdown_io.export_markdown(self.store.todos(), title=self.page_title())

    def copy_markdown(self) -> None:
        QGuiApplication.clipboard().setText(self.markdown_text())
        self.show_toast("마크다운을 클립보드에 복사했어요")

    def export_markdown_file(self) -> None:
        folder = default_export_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        name = markdown_io.default_filename("할 일", date.fromisoformat(self.store.current_day))
        path, _ = QFileDialog.getSaveFileName(
            self, "마크다운으로 저장", str(folder / name), "Markdown (*.md);;모든 파일 (*)"
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
            "마크다운 불러오기",
            str(folder),
            "Markdown (*.md *.markdown *.txt);;모든 파일 (*)",
        )
        if not path:
            return
        try:
            items = markdown_io.read_markdown_file(Path(path))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "불러오기 실패", f"파일을 읽지 못했어요.\n{e}")
            return
        if not items:
            QMessageBox.information(self, "불러오기", "체크리스트 항목(- [ ] …)을 찾지 못했어요.")
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

    def _restore_geometry(self) -> None:
        s = self.settings
        self.resize(s["window_w"], s["window_h"])
        x, y = s["window_x"], s["window_y"]
        if x is not None and y is not None:
            center = QPoint(x + s["window_w"] // 2, y + 20)
            if QGuiApplication.screenAt(center) is not None:
                self.move(x, y)
                return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - s["window_w"] - 40, area.top() + 40)

    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        self._remember_geometry()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._remember_geometry()

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
        self.settings.set("window_w", g.width())
        self.settings.set("window_h", g.height())
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
