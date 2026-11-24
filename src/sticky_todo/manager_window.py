"""전체 할 일 검색·관리 창.

노트와 같은 TodoStore를 구독하므로 두 창은 항상 동기화된다.
계층은 평평하게 보여주고, 하위 항목에는 부모 이름을 흐리게 붙인다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, markdown_io
from .note_widget import CheckButton
from .paths import default_export_dir
from .storage import SortKey, Status, Todo, TodoStore

if TYPE_CHECKING:
    from .settings import Settings

COL_SELECT, COL_DONE, COL_TEXT, COL_CREATED, COL_COMPLETED = range(5)
HEADERS = ["", "완료", "내용", "만든 날", "완료한 날"]
PARENT_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_OPTIONS = [
    (SortKey.CREATED_DESC, "최근 추가순"),
    (SortKey.CREATED_ASC, "오래된순"),
    (SortKey.COMPLETED_DESC, "최근 완료순"),
    (SortKey.ALPHA, "가나다순"),
]
STATUS_OPTIONS = [
    (Status.ALL, "전체"),
    (Status.ACTIVE, "진행 중"),
    (Status.DONE, "완료"),
    (Status.ARCHIVED, "보관됨"),
]
TOAST_MS = 5000
_ROOT = QModelIndex()


def format_time(value: str | None, now: datetime | None = None) -> str:
    if not value:
        return ""
    t = datetime.fromisoformat(value)
    now = now or datetime.now().astimezone()
    if t.year == now.year:
        return t.strftime("%m-%d %H:%M")
    return t.strftime("%Y-%m-%d")


def parent_names(todos: list[Todo]) -> dict[str, str]:
    """id → 바로 위 부모의 내용 (최상위 항목은 없음)."""
    result: dict[str, str] = {}
    stack: list[Todo] = []
    for t in todos:
        while stack and stack[-1].indent >= t.indent:
            stack.pop()
        if stack:
            result[t.id] = stack[-1].text
        stack.append(t)
    return result


# ---------------------------------------------------------------- 모델


class TodoTableModel(QAbstractTableModel):
    def __init__(self, store: TodoStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.rows: list[Todo] = []
        self.parents: dict[str, str] = {}
        self.search = ""
        self.status: Status = Status.ALL
        self.sort: SortKey = SortKey.CREATED_DESC
        self.is_selected = lambda row: False  # 뷰가 연결해 준다

    def refresh(self) -> None:
        self.beginResetModel()
        self.rows = self.store.query(self.search, self.status, self.sort)
        self.parents = parent_names(self.store.todos())
        self.endResetModel()

    def todo_at(self, row: int) -> Todo:
        return self.rows[row]

    def rowCount(self, parent=_ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=_ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    @property
    def archived_view(self) -> bool:
        return self.status is Status.ARCHIVED

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_SELECT:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        # 보관함 항목은 읽기 전용 (복원한 뒤에 고친다)
        if index.column() == COL_DONE and not self.archived_view:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == COL_TEXT and not self.archived_view:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        t = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            if col == COL_SELECT:
                on = self.is_selected(index.row())
                return Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            if col == COL_DONE:
                return Qt.CheckState.Checked if t.done else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == COL_TEXT:
                return t.text
            if col == COL_CREATED:
                return format_time(t.created_at)
            if col == COL_COMPLETED:
                return format_time(t.completed_at)
        if role == PARENT_ROLE and col == COL_TEXT:
            return self.parents.get(t.id)
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_TEXT:
            parent = self.parents.get(t.id)
            tip = f"{t.text}\n상위 항목: {parent}" if parent else t.text
            if t.archived_at:
                tip += f"\n보관: {format_time(t.archived_at)}"
            return tip
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_TEXT and t.done:
            return QColor(128, 128, 128)
        return None

    def setData(self, index: QModelIndex, value: Any, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False
        t = self.rows[index.row()]
        if index.column() == COL_DONE and role == Qt.ItemDataRole.CheckStateRole:
            self.store.set_done(t.id, Qt.CheckState(value) == Qt.CheckState.Checked)
            return True
        if index.column() == COL_TEXT and role == Qt.ItemDataRole.EditRole:
            text = " ".join(str(value).split())
            if text and text != t.text:
                self.store.update_text(t.id, text)
            return bool(text)
        return False


# ---------------------------------------------------------------- 내용 칸 (부모 이름 흐리게)


class TextDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        parent = index.data(PARENT_ROLE)
        super().paint(painter, option, index)
        if not parent:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text_rect = opt.widget.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget
        )
        used = opt.fontMetrics.horizontalAdvance(opt.text) + 12
        rect = QRect(
            text_rect.left() + used, text_rect.top(), text_rect.width() - used, text_rect.height()
        )
        if rect.width() < 30:
            return
        painter.save()
        color = QColor(opt.palette.color(opt.palette.ColorRole.Text))
        color.setAlphaF(0.45)
        painter.setPen(color)
        hint = opt.fontMetrics.elidedText(f"‹ {parent}", Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), hint
        )
        painter.restore()


# ---------------------------------------------------------------- 체크 칸 (노트와 같은 모양)


class CheckDelegate(QStyledItemDelegate):
    """완료 칸은 동그라미, 선택 칸은 둥근 네모. 채움은 강조색."""

    def __init__(self, shape: str, parent=None) -> None:
        super().__init__(parent)
        self.shape = shape
        self.accent = QColor("#0A84FF")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.text = ""
        widget = opt.widget
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        side = 18
        r = QRectF(
            opt.rect.center().x() - side / 2 + 1, opt.rect.center().y() - side / 2 + 1, side, side
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.accent)
        else:
            ring = QColor(opt.palette.color(opt.palette.ColorRole.Text))
            ring.setAlphaF(0.35)
            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = r.adjusted(0.75, 0.75, -0.75, -0.75)
        if self.shape == "circle":
            painter.drawEllipse(r)
        else:
            painter.drawRoundedRect(r, 5, 5)
        if checked:
            CheckButton._draw_check(painter, r, QColor("#FFFFFF"))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if self.shape != "circle":
            return False  # 선택 칸은 표(TodoTable)가 직접 처리
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return True  # 보관함 항목은 체크 변경 불가
        t = event.type()
        if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
            new = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
            model.setData(index, new.value, Qt.ItemDataRole.CheckStateRole)
            return True
        return t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)


# ---------------------------------------------------------------- 표


class TodoTable(QTableView):
    """첫 칸(선택 체크박스)을 누르면 다른 선택을 유지한 채 그 줄만 토글한다.

    Delete / Ctrl+Z는 단축키 대신 키 입력에서 직접 받는다 (창 활성 여부와 무관하게 동작).
    """

    delete_requested = Signal()
    undo_requested = Signal()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.matches(QKeySequence.StandardKey.Delete):
            self.delete_requested.emit()
            return
        if e.matches(QKeySequence.StandardKey.Undo):
            self.undo_requested.emit()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        index = self.indexAt(e.position().toPoint())
        if index.isValid() and index.column() == COL_SELECT:
            self.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            return
        super().mousePressEvent(e)


# ---------------------------------------------------------------- 창


class ManagerWindow(QWidget):
    def __init__(self, store: TodoStore, settings: Settings, on_settings_changed=None) -> None:
        super().__init__(None)  # 독립 창: 작업표시줄에 보인다
        self.store = store
        self.settings = settings
        self._on_settings_changed = on_settings_changed or (lambda: None)
        self.setWindowTitle("Sticky Todo - 전체 할 일")
        self.setMinimumSize(480, 320)

        # --- 상단: 검색 / 상태 / 정렬
        self.search = QLineEdit()
        self.search.setPlaceholderText("검색 (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        self._search_icon = self.search.addAction(
            icons.icon("search", "#8E8E93"), QLineEdit.ActionPosition.LeadingPosition
        )

        self.status_group = QButtonGroup(self)
        self.status_buttons: dict[Status, QPushButton] = {}
        self.segmented = QFrame()
        self.segmented.setObjectName("segmented")
        status_box = QHBoxLayout(self.segmented)
        status_box.setContentsMargins(2, 2, 2, 2)
        status_box.setSpacing(2)
        for i, (status, _) in enumerate(STATUS_OPTIONS):
            b = QPushButton()
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.status_group.addButton(b, i)
            self.status_buttons[status] = b
            status_box.addWidget(b)
        self.status_buttons[Status.ALL].setChecked(True)
        self.status_group.idClicked.connect(self._on_status)

        self.sort_combo = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.currentIndexChanged.connect(self._on_sort)

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.segmented)
        top.addWidget(self.sort_combo)

        # --- 표
        self.model = TodoTableModel(store, self)
        self.table = TodoTable()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_TEXT, TextDelegate(self.table))
        self.select_delegate = CheckDelegate("square", self.table)
        self.done_delegate = CheckDelegate("circle", self.table)
        self.table.setItemDelegateForColumn(COL_SELECT, self.select_delegate)
        self.table.setItemDelegateForColumn(COL_DONE, self.done_delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SELECT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_DONE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CREATED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_COMPLETED, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(COL_SELECT, 32)
        self.table.setColumnWidth(COL_DONE, 44)
        self.model.is_selected = self._row_selected
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.empty_label = QLabel("조건에 맞는 할 일이 없어요")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: palette(placeholder-text); padding: 24px;")
        self.empty_label.hide()

        # --- 알림
        self.toast = QFrame()
        self.toast.setObjectName("managerToast")
        t_lay = QHBoxLayout(self.toast)
        t_lay.setContentsMargins(14, 9, 12, 9)
        self.toast_label = QLabel()
        self.toast_undo = QToolButton()
        self.toast_undo.setText("되돌리기")
        self.toast_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toast_undo.clicked.connect(self.undo_delete)
        t_lay.addWidget(self.toast_label, 1)
        t_lay.addWidget(self.toast_undo)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)

        # --- 하단: 일괄 작업
        self.selection_label = QLabel()
        self.done_btn = QPushButton("완료 처리")
        self.done_btn.clicked.connect(self.toggle_done_selected)
        self.export_btn = QPushButton("마크다운 내보내기")
        self.export_btn.clicked.connect(self.export_selected)
        self.delete_btn = QPushButton("삭제")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self.delete_selected)
        bottom = QHBoxLayout()
        bottom.addWidget(self.selection_label, 1)
        bottom.addWidget(self.done_btn)
        bottom.addWidget(self.export_btn)
        bottom.addWidget(self.delete_btn)

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.table, 1)
        root.addWidget(self.empty_label)
        root.addWidget(self.toast)
        root.addLayout(bottom)

        # --- 단축키
        QShortcut(QKeySequence.StandardKey.Find, self, self.focus_search)
        self.table.delete_requested.connect(self.delete_selected)
        self.table.undo_requested.connect(self.undo_delete)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search, self.search.clear).setContext(
            Qt.ShortcutContext.WidgetShortcut
        )

        store.changed.connect(self.refresh)
        self.apply_style()
        self._restore_geometry()
        self.refresh()

    # ------------------------------------------------------------ 모양 (iOS 느낌)

    def apply_style(self) -> None:
        accent = QColor(self.settings["accent_color"])
        self.select_delegate.accent = accent
        self.done_delegate.accent = accent
        self.table.viewport().update()
        a = f"rgba({accent.red()}, {accent.green()}, {accent.blue()}"
        dark = self.palette().color(self.backgroundRole()).lightness() < 128
        seg_on = "#636366" if dark else "#FFFFFF"
        fill = "rgba(118, 118, 128, 0.24)" if dark else "rgba(118, 118, 128, 0.12)"
        line = "rgba(128, 128, 128, 0.18)"
        self.setStyleSheet(f"""
            QLineEdit {{
                background: {fill}; border: none; border-radius: 10px; padding: 6px 8px;
                selection-background-color: {a}, 0.4);
            }}
            QTableView QLineEdit {{
                background: palette(base); border: 1.5px solid {a}, 1.0); border-radius: 6px;
                padding: 0 4px;
            }}
            #segmented {{ background: {fill}; border-radius: 9px; }}
            #segmented QPushButton {{
                border: none; background: transparent; border-radius: 7px; padding: 5px 12px;
            }}
            #segmented QPushButton:checked {{ background: {seg_on}; font-weight: 600; }}
            QComboBox {{ background: {fill}; border: none; border-radius: 9px; padding: 5px 10px; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QTableView {{
                border: none; background: transparent; outline: 0;
                selection-background-color: {a}, 0.22); selection-color: palette(text);
            }}
            QTableView::item {{ border-bottom: 1px solid {line}; padding: 0 4px; }}
            QTableView::item:selected {{ background: {a}, 0.20); color: palette(text); }}
            QHeaderView::section {{
                background: transparent; border: none; border-bottom: 1px solid {line};
                padding: 6px 4px; font-weight: 600; color: palette(placeholder-text);
            }}
            QPushButton {{
                background: {fill}; border: none; border-radius: 9px; padding: 7px 14px;
                color: {a}, 1.0); font-weight: 600;
            }}
            QPushButton:disabled {{ color: palette(placeholder-text); }}
            QPushButton#danger:enabled {{ color: #FF3B30; }}
            #segmented QPushButton {{ color: palette(text); font-weight: 400; }}
            #managerToast {{ background: rgba(28, 28, 30, 0.92); border-radius: 12px; }}
            #managerToast QLabel {{ color: #FFFFFF; font-size: 9pt; }}
            #managerToast QToolButton {{
                color: {a}, 1.0); border: none; font-weight: 700; background: transparent;
            }}
        """)

    # ------------------------------------------------------------ 갱신

    def refresh(self) -> None:
        keep = set(self.selected_ids())
        self.model.refresh()
        sel = QItemSelection()
        for row, t in enumerate(self.model.rows):
            if t.id in keep:
                left = self.model.index(row, 0)
                right = self.model.index(row, self.model.columnCount() - 1)
                sel.select(left, right)
        self.table.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        counts = self.store.counts()
        for status, label in STATUS_OPTIONS:
            self.status_buttons[status].setText(f"{label} {counts[status]}")
        self.empty_label.setVisible(not self.model.rows)
        self.table.setVisible(bool(self.model.rows))
        self._update_bottom()

    def _on_search(self, text: str) -> None:
        self.model.search = text
        self.refresh()

    def _on_status(self, i: int) -> None:
        self.model.status = STATUS_OPTIONS[i][0]
        self.refresh()

    def _on_sort(self, i: int) -> None:
        self.model.sort = self.sort_combo.itemData(i)
        self.refresh()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------------ 선택

    def _row_selected(self, row: int) -> bool:
        return self.table.selectionModel().isRowSelected(row, QModelIndex())

    def _on_selection_changed(self, *_args) -> None:
        if self.model.rows:
            top = self.model.index(0, COL_SELECT)
            bottom = self.model.index(len(self.model.rows) - 1, COL_SELECT)
            self.model.dataChanged.emit(top, bottom, [Qt.ItemDataRole.CheckStateRole])
        self._update_bottom()

    def selected_ids(self) -> list[str]:
        """선택된 항목 id (화면 순서)."""
        if self.table.model() is None:
            return []
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.model.rows[r].id for r in rows if r < len(self.model.rows)]

    def _selected_todos(self) -> list[Todo]:
        ids = set(self.selected_ids())
        return [t for t in self.model.rows if t.id in ids]

    def _update_bottom(self) -> None:
        todos = self._selected_todos()
        n = len(todos)
        self.selection_label.setText(f"{n}개 선택" if n else "선택한 항목 없음")
        for b in (self.done_btn, self.export_btn, self.delete_btn):
            b.setEnabled(n > 0)
        all_done = n > 0 and all(t.done for t in todos)
        if self.model.archived_view:
            self.done_btn.setText("노트로 복원")
        else:
            self.done_btn.setText("완료 취소" if all_done else "완료 처리")

    def select_ids(self, ids: list[str]) -> None:
        sel = QItemSelection()
        for row, t in enumerate(self.model.rows):
            if t.id in ids:
                sel.select(self.model.index(row, 0), self.model.index(row, len(HEADERS) - 1))
        self.table.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    # ------------------------------------------------------------ 일괄 작업

    def toggle_done_selected(self) -> None:
        todos = self._selected_todos()
        if not todos:
            return
        if self.model.archived_view:
            n = self.store.restore([t.id for t in todos])
            self.show_toast(f"{n}개를 노트로 복원했어요")
            return
        all_done = all(t.done for t in todos)
        self.store.set_done_many([t.id for t in todos], not all_done)

    def archive_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        n = self.store.archive(ids)
        extra = n - len(ids)
        text = f"{n}개 보관함으로" + (f" (하위 {extra}개 포함)" if extra > 0 else "")
        self.show_toast(text, undo=True)

    def build_context_menu(self) -> QMenu:
        """선택한 항목에 대한 메뉴 (우클릭)."""
        menu = QMenu(self)
        todos = self._selected_todos()
        n = len(todos)
        if self.model.archived_view:
            menu.addAction(f"노트로 복원 ({n}개)", self.toggle_done_selected)
            menu.addAction("마크다운으로 내보내기…", self.export_selected)
            menu.addSeparator()
            menu.addAction("완전히 삭제", self.delete_selected)
            return menu
        all_done = all(t.done for t in todos)
        menu.addAction("완료 취소" if all_done else "완료 처리", self.toggle_done_selected)
        if n == 1:
            menu.addAction("내용 수정", self._edit_current)
        menu.addAction(f"보관함으로 ({n}개)", self.archive_selected)
        menu.addAction("마크다운으로 내보내기…", self.export_selected)
        menu.addSeparator()
        menu.addAction("삭제", self.delete_selected)
        return menu

    def _show_context_menu(self, pos: QPoint) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        # 선택 밖의 줄을 우클릭하면 그 줄만 선택
        if not self._row_selected(index.row()):
            self.select_ids([self.model.rows[index.row()].id])
        self.table.setCurrentIndex(self.model.index(index.row(), COL_TEXT))
        self.build_context_menu().exec(self.table.viewport().mapToGlobal(pos))

    def _edit_current(self) -> None:
        self.table.edit(self.model.index(self.table.currentIndex().row(), COL_TEXT))

    def delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        n = self.store.delete(ids)
        extra = n - len(ids)
        text = f"{n}개 삭제됨" + (f" (하위 {extra}개 포함)" if extra > 0 else "")
        self.show_toast(text, undo=True)

    def undo_delete(self) -> None:
        n = self.store.undo_delete()
        if n:
            self.show_toast(f"{n}개 복구됨")

    def show_toast(self, text: str, undo: bool = False) -> None:
        self.toast_label.setText(text)
        self.toast_undo.setVisible(undo)
        self.toast.show()
        self._toast_timer.start(TOAST_MS)

    def export_selected(self) -> None:
        todos = self._selected_todos()
        if not todos:
            return
        folder = default_export_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        title = f"{self.settings['title']} (선택)"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "선택 항목 마크다운으로 저장",
            str(folder / markdown_io.default_filename(title)),
            "Markdown (*.md);;모든 파일 (*)",
        )
        if not path:
            return
        text = markdown_io.export_markdown(todos, title=title, keep_indent=False)
        try:
            markdown_io.save_markdown(Path(path), text)
        except OSError as e:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장하지 못했어요.\n{e}")
            return
        self.show_toast(f"{len(todos)}개를 {Path(path).name}에 저장했어요")

    # ------------------------------------------------------------ 창 위치/크기

    def _restore_geometry(self) -> None:
        s = self.settings
        self.resize(s["manager_w"], s["manager_h"])
        x, y = s["manager_x"], s["manager_y"]
        if x is not None and y is not None and QGuiApplication.screenAt(QPoint(x + 40, y + 20)):
            self.move(x, y)

    def closeEvent(self, e) -> None:  # noqa: N802
        g = self.geometry()
        for key, value in (
            ("manager_x", g.x()),
            ("manager_y", g.y()),
            ("manager_w", g.width()),
            ("manager_h", g.height()),
        ):
            self.settings.set(key, value)
        self._on_settings_changed()
        super().closeEvent(e)
