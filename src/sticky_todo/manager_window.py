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
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
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

from . import markdown_io
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
STATUS_OPTIONS = [(Status.ALL, "전체"), (Status.ACTIVE, "진행 중"), (Status.DONE, "완료")]
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

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in (COL_SELECT, COL_DONE):
            f |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == COL_TEXT:
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
            return f"{t.text}\n상위 항목: {parent}" if parent else t.text
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

        self.status_group = QButtonGroup(self)
        self.status_buttons: dict[Status, QPushButton] = {}
        status_box = QHBoxLayout()
        status_box.setSpacing(4)
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
        top.addLayout(status_box)
        top.addWidget(self.sort_combo)

        # --- 표
        self.model = TodoTableModel(store, self)
        self.table = TodoTable()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_TEXT, TextDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
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
        self.toast.setStyleSheet(
            "#managerToast { background: palette(highlight); border-radius: 6px; }"
            "#managerToast QLabel, #managerToast QToolButton {"
            " color: palette(highlighted-text); }"
            "#managerToast QToolButton { border: none; font-weight: bold;"
            " text-decoration: underline; }"
        )
        t_lay = QHBoxLayout(self.toast)
        t_lay.setContentsMargins(10, 4, 6, 4)
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
        self._restore_geometry()
        self.refresh()

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
        all_done = all(t.done for t in todos)
        self.store.set_done_many([t.id for t in todos], not all_done)

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
