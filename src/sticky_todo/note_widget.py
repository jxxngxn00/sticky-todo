"""바탕화면 스티키 노트 위젯."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QFontDatabase,
    QGuiApplication,
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

from . import markdown_io
from .backdrop import CORNER_RADIUS_PX, disable_glass, enable_glass
from .paths import default_export_dir
from .settings import PRESETS, Settings, contrast_text
from .storage import Todo, TodoStore

INDENT_PX = 20
MIME_TODO = "application/x-sticky-todo-id"
FALLBACK_FONT = "Malgun Gothic"
PRESET_LABELS = {"yellow": "노랑", "pink": "분홍", "sky": "하늘", "mint": "민트"}
TOAST_MS = 5000
SHADOW_PX = 10
GLASS_FALLBACK_MIN_ALPHA = 0.75
GLASS_MIN_ALPHA = 0.05


def _rgba(color: str, alpha: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


def resolve_font_family(name: str) -> str:
    return name if name in QFontDatabase.families() else FALLBACK_FONT


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
        self.shape = "square"  # square / circle / bullet
        self.color = QColor("#333333")
        self.fill_text = QColor("#FFFFFF")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(18, 18)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 4
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pen = QPen(self.color, 1.6)
        checked = self.isChecked()

        if self.shape == "bullet":
            p.setPen(Qt.PenStyle.NoPen)
            if checked:
                self._draw_check(p, r, self.color)
            else:
                p.setBrush(self.color)
                d = side * 0.32
                p.drawEllipse(r.center(), d / 2, d / 2)
            return

        p.setPen(pen)
        p.setBrush(self.color if checked else Qt.BrushStyle.NoBrush)
        if self.shape == "circle":
            p.drawEllipse(r)
        else:
            p.drawRoundedRect(r, 3, 3)
        if checked:
            self._draw_check(p, r, self.fill_text)

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
    drag_requested = Signal(str, QPoint)
    indent_requested = Signal(str, int)  # 편집 중 Tab / Shift+Tab

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
        lay.setContentsMargins(6 + todo.indent * INDENT_PX, pad, 4, pad)
        lay.setSpacing(6)

        self.check = CheckButton(self)
        self.check.shape = style["check_style"]
        self.check.color = QColor(style["text_color"])
        self.check.fill_text = QColor(style["bg_color"])
        self.check.setChecked(todo.done)
        self.check.setFixedSize(18, 18)
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
        self.delete_btn.setText("✕")
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
        self.editor.setFocus()
        self.editor.selectAll()

    def finish_edit(self, commit: bool) -> None:
        if not self._editing:
            return
        self._editing = False
        text = " ".join(self.editor.text().split())
        self.editor.hide()
        self.label.show()
        if commit and text and text != self.todo.text:
            self.edited.emit(self.todo.id, text)

    @property
    def editing(self) -> bool:
        return self._editing

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.finish_edit(commit=False)
                self.parentWidget().setFocus()
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

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.begin_edit()
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
            row.drag_requested.connect(self._start_drag)
            row.indent_requested.connect(self._on_editor_indent)
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
    renamed = Signal(str)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 4, 4)
        lay.setSpacing(2)

        self.label = QLabel(title, self)
        self.label.setObjectName("titleLabel")
        # 좁은 창에서도 줄어들 수 있게 (넘치는 글자는 잘림)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setMinimumWidth(20)
        self.editor = QLineEdit(self)
        self.editor.setMaxLength(50)
        self.editor.hide()
        self.editor.editingFinished.connect(self._finish_rename)
        self.editor.installEventFilter(self)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.editor, 1)

        self.pin_btn = self._button("📌", "항상 위에 고정")
        self.pin_btn.setCheckable(True)
        self.menu_btn = self._button("☰", "메뉴")
        self.close_btn = self._button("✕", "닫기")
        for b in (self.pin_btn, self.menu_btn, self.close_btn):
            lay.addWidget(b)

    def _button(self, text: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("titleButton")
        b.setText(text)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def set_title(self, title: str) -> None:
        self.label.setText(title)

    def _finish_rename(self) -> None:
        if not self.editor.isVisible():
            return
        text = " ".join(self.editor.text().split())
        self.editor.hide()
        self.label.show()
        if text and text != self.label.text():
            self.renamed.emit(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            obj is self.editor
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.editor.setText(self.label.text())
            self._finish_rename()
            return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and not self.editor.isVisible():
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()  # 윈도우 스냅 등 기본 이동 동작
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self.label.geometry().contains(e.position().toPoint()):
            self.editor.setText(self.label.text())
            self.label.hide()
            self.editor.show()
            self.editor.setFocus()
            self.editor.selectAll()
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
        self._update_look()

    def set_level(self, level: int) -> None:
        self.level = max(0, min(level, self._max_level()))
        self._update_look()

    def _update_look(self) -> None:
        self.setTextMargins(self.level * INDENT_PX, 0, 0, 0)
        self.setPlaceholderText("↳ 하위 할 일 추가" if self.level else "할 일 추가 (Enter)")

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
    open_manager = Signal()

    def __init__(
        self, store: TodoStore, settings: Settings, settings_path: Path, parent=None
    ) -> None:
        super().__init__(parent)
        self.store = store
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
        self._glass_on = False
        self._settings_win = None
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

        self.title_bar = TitleBar(settings["title"], self.card)
        self.title_bar.pin_btn.setChecked(settings["always_on_top"])
        self.title_bar.pin_btn.toggled.connect(self.set_always_on_top)
        self.title_bar.menu_btn.clicked.connect(self._show_menu)
        self.title_bar.close_btn.clicked.connect(self.close)
        self.title_bar.renamed.connect(self._rename)
        card_lay.addWidget(self.title_bar)

        self.list_view = TodoListView(store, self.card)
        self.list_view.delete_done.connect(lambda n: self.show_toast(f"{n}개 삭제됨", undo=True))
        card_lay.addWidget(self.list_view, 1)

        # 알림 (삭제 되돌리기 등)
        self.toast = QFrame(self.card)
        self.toast.setObjectName("toast")
        t_lay = QHBoxLayout(self.toast)
        t_lay.setContentsMargins(10, 4, 6, 4)
        self.toast_label = QLabel(self.toast)
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
        b_lay.setContentsMargins(8, 4, 0, 0)
        b_lay.setSpacing(6)
        self.input = TodoInput(self._max_input_level, bottom)
        self.input.submitted.connect(self._add)
        self.count_label = QLabel(bottom)
        self.count_label.setObjectName("countLabel")
        grip_box = QVBoxLayout()
        grip_box.setContentsMargins(0, 0, 0, 0)
        grip_box.addStretch(1)
        self.grip = QSizeGrip(bottom)
        grip_box.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        b_lay.addWidget(self.input, 1)
        b_lay.addWidget(self.count_label)
        b_lay.addLayout(grip_box)
        card_lay.addWidget(bottom)

        undo = QShortcut(QKeySequence.StandardKey.Undo, self.list_view)
        undo.setContext(Qt.ShortcutContext.WidgetShortcut)
        undo.activated.connect(self.undo_delete)

        store.changed.connect(self._on_store_changed)
        store.save_failed.connect(lambda msg: self.show_toast(f"저장 실패: {msg}"))

        self.apply_settings()
        self._restore_geometry()
        self._restoring = False

    # ------------------------------------------------------------ 설정 반영

    def _apply_window_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.settings["always_on_top"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def note_palette(self) -> dict:
        """현재 모드에서 노트를 그릴 색들."""
        s = self.settings
        if s["style"] == "glass":
            tint = s["glass_tint"]
            alpha = (100 - s["glass_transparency"]) / 100
            if not self._glass_on:
                # 블러가 없으면 너무 투명할 때 뒤 화면과 글자가 섞여 안 읽힌다
                alpha = max(alpha, GLASS_FALLBACK_MIN_ALPHA)
            else:
                # 완전히 투명한 부분은 클릭이 뒤 창으로 통과해버린다 (레이어드 창 특성)
                alpha = max(alpha, GLASS_MIN_ALPHA)
            text = contrast_text(tint)
            edge = 0.30 if text == "#FFFFFF" else 0.65
            return {
                "card": _rgba(tint, alpha),
                "title": _rgba(tint, min(1.0, alpha + 0.15)),
                "text": text,
                "solid": tint,
                "border": f"1px solid {_rgba('#FFFFFF', edge)}",  # 유리 가장자리 하이라이트
                "radius": CORNER_RADIUS_PX[s["glass_corner"]],
                "shadow": False,  # 글래스는 윈도우 기본 그림자
            }
        return {
            "card": s["bg_color"],
            "title": s["title_color"],
            "text": s["text_color"],
            "solid": s["bg_color"],
            "border": "none",
            "radius": s["corner_radius"],
            "shadow": s["shadow"],
        }

    def apply_settings(self) -> None:
        self._update_backdrop()
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
        }
        self.title_bar.set_title(s["title"])
        self._update_title_visibility()
        self._on_store_changed()

    def _update_backdrop(self) -> bool:
        """글래스 모드면 Acrylic을 켜고 아니면 끈다. 켜짐 상태가 바뀌면 True."""
        was = self._glass_on
        if not self.isVisible():
            return False
        hwnd = int(self.winId())
        if self.settings["style"] == "glass":
            self._glass_on = enable_glass(hwnd, corner=self.settings["glass_corner"])
        else:
            if was:
                disable_glass(hwnd)
            self._glass_on = False
        return was != self._glass_on

    @property
    def glass_active(self) -> bool:
        """글래스 모드이고 실제로 블러가 켜졌는지 (False면 반투명 대체)."""
        return self._glass_on

    def _apply_style(self) -> None:
        s = self.settings
        family = resolve_font_family(s["font_family"])
        font = QFont(family)
        font.setPointSize(s["font_size"])
        self.setFont(font)

        pal = self.note_palette()
        card, title, text, solid = pal["card"], pal["title"], pal["text"], pal["solid"]
        r = pal["radius"]
        field_base = "#FFFFFF" if text != "#FFFFFF" else "#000000"  # 입력창 바탕
        # 카드 안쪽에만 적용한다. 노트 창 전체에 걸면 메뉴·알림창까지 노트 글자색을 물려받아
        # 다크모드(검은 배경)에서 글자가 안 보인다.
        self.card.setStyleSheet(f"""
            QWidget {{
                color: {text}; font-family: "{family}"; font-size: {s["font_size"]}pt;
            }}
            #card {{ background: {card}; border-radius: {r}px; border: {pal["border"]}; }}
            #titleBar {{
                background: {title};
                border-top-left-radius: {r}px; border-top-right-radius: {r}px;
            }}
            #titleLabel {{ font-weight: bold; }}
            #titleButton {{
                border: none; background: transparent; padding: 2px 5px; border-radius: 4px;
            }}
            #titleButton:hover {{ background: {_rgba(text, 0.12)}; }}
            #titleButton:checked {{ background: {_rgba(text, 0.20)}; }}
            #todoList, #rowContainer, #bottomBar {{ background: transparent; }}
            #todoList > QWidget > QWidget {{ background: transparent; }}
            #row {{ border-radius: 4px; }}
            #row:hover {{ background: {_rgba(text, 0.07)}; }}
            #row[selected="true"] {{ background: {_rgba(text, 0.14)}; }}
            #row QLabel[dim="true"] {{ color: {_rgba(text, 0.45)}; }}
            #row QLabel[strike="true"] {{ text-decoration: line-through; }}
            #rowDelete {{ border: none; background: transparent; color: {_rgba(text, 0.5)}; }}
            #rowDelete:hover {{ color: #E53935; }}
            #emptyLabel {{ color: {_rgba(text, 0.45)}; padding: 20px; }}
            #dropIndicator {{ background: {text}; }}
            QLineEdit {{
                background: {_rgba(field_base, 0.45)}; border: 1px solid {_rgba(text, 0.2)};
                border-radius: 4px; padding: 3px 6px;
                selection-background-color: {_rgba(text, 0.3)};
            }}
            #todoInput {{ background: {_rgba(field_base, 0.30)}; }}
            #countLabel {{ color: {_rgba(text, 0.6)}; }}
            #toast {{ background: {_rgba(text, 0.85)}; }}
            #toast QLabel {{ color: {solid}; }}
            #toastButton {{
                border: none; background: transparent; color: {solid};
                font-weight: bold; text-decoration: underline;
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
            QScrollBar::handle:vertical {{
                background: {_rgba(text, 0.25)}; border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            QSizeGrip {{ width: 14px; height: 14px; }}
            QToolTip {{
                color: {text}; background: {solid}; border: 1px solid {_rgba(text, 0.3)};
                padding: 2px 4px;
            }}
        """)

        # 그림자는 paintEvent에서 카드 바깥 여백에 직접 그린다.
        # (QGraphicsDropShadowEffect는 카드를 그림으로 캐시해서 색 변경이 안 보이고
        #  글자 렌더링도 틀어지는 문제가 있었다)
        m = SHADOW_PX if pal["shadow"] else 0
        self._outer.setContentsMargins(m, m, m, m)
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
        # 바깥에서 안쪽으로 옅은 둥근 사각형을 겹쳐 그려 부드러운 그림자를 만든다
        for i in range(SHADOW_PX, 0, -1):
            p.setBrush(QColor(0, 0, 0, 6))
            r = rect.adjusted(-i, -i + 2, i, i + 2)
            p.drawRoundedRect(r, radius + i, radius + i)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        # 창이 처음 뜨거나 (항상 위 전환으로) 다시 만들어질 때 글래스를 다시 건다
        if self._update_backdrop() or self.settings["style"] == "glass":
            self._apply_style()

    # 제목 바: 항상 표시 / 마우스 올릴 때만

    def _update_title_visibility(self, hovered: bool | None = None) -> None:
        if hovered is None:
            hovered = self.isVisible() and self.geometry().contains(QCursor.pos())
        show = (
            self.settings["title_bar_mode"] == "always"
            or hovered
            or self.title_bar.editor.isVisible()
        )
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
        c = self.store.counts()
        self.count_label.setText(f"{c['done']}/{c['all']}")
        if self.input.level > self._max_input_level():
            self.input.set_level(self.input.level)

    def _max_input_level(self) -> int:
        todos = self.store.todos()
        return todos[-1].indent + 1 if todos else 0

    # ------------------------------------------------------------ 동작

    def _add(self, text: str, level: int) -> None:
        self.store.add(text, level)
        self.list_view.scroll_to_bottom()

    def _rename(self, title: str) -> None:
        self.settings.set("title", title)
        self.title_bar.set_title(self.settings["title"])
        self.schedule_settings_save()

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
        act = menu.addAction("전체 목록 보기", self.open_manager.emit)
        act.setShortcut(QKeySequence("Ctrl+Shift+L"))
        act.setEnabled(False)  # 4단계에서 연결
        colors = menu.addMenu("색상")
        for key, label in PRESET_LABELS.items():
            a = colors.addAction(label, lambda k=key: self.apply_preset(k))
            a.setCheckable(True)
            a.setChecked(all(self.settings[f] == v.upper() for f, v in PRESETS[key].items()))
        clear = menu.addAction("완료 항목 지우기", self.clear_completed)
        clear.setEnabled(self.store.counts()["done"] > 0)
        menu.addSeparator()
        menu.addAction("설정…", self.open_settings_window)
        auto = menu.addAction("시작 시 자동 실행")  # 5단계
        auto.setCheckable(True)
        auto.setEnabled(False)
        menu.addSeparator()
        menu.addAction("종료", self.close)
        return menu

    def open_settings_window(self) -> None:
        from .settings_window import SettingsWindow

        if self._settings_win is None:
            self._settings_win = SettingsWindow(self)
            self._settings_win.destroyed.connect(self._forget_settings_window)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _forget_settings_window(self) -> None:
        self._settings_win = None

    def clear_completed(self) -> None:
        n = self.store.clear_completed()
        if n:
            self.show_toast(f"완료 {n}개 지움", undo=True)

    def markdown_text(self) -> str:
        return markdown_io.export_markdown(self.store.todos(), title=self.settings["title"])

    def copy_markdown(self) -> None:
        QGuiApplication.clipboard().setText(self.markdown_text())
        self.show_toast("마크다운을 클립보드에 복사했어요")

    def export_markdown_file(self) -> None:
        folder = default_export_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        name = markdown_io.default_filename(self.settings["title"])
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
        n = self.store.add_many(items)
        self.list_view.scroll_to_bottom()
        self.show_toast(f"{n}개 불러옴")

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

    def closeEvent(self, e) -> None:  # noqa: N802
        self.save_settings()
        self.store.flush()
        super().closeEvent(e)
        QApplication.quit()
