"""'날짜 고르기…' 창: 월간 보기의 달력을 작은 창으로 띄워 날짜 하나를 고른다.

날짜를 한 번 누르면 바로 그 날로 정해지고 창이 닫힌다. ‹ ›로 달을 넘긴다.
할 일 점과 공휴일(빨간 숫자)이 그대로 보여서 비어 있는 날을 찾기 쉽다.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .month_view import MonthCalendar
from .note_widget import (
    HOLIDAY_RED,
    MIME_TODO,
    _mix,
    calendar_cells,
    contrast,
    ring_alpha,
    shift_month,
    soft_alpha,
)
from .storage import TodoStore, today_str

HINT = "누르면 이 날로 옮겨요"


class DatePicker(QDialog):
    def __init__(
        self,
        store: TodoStore,
        start: str,
        *,
        accent: str = "#FF9F0A",
        title: str = "날짜 고르기",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.store = store
        self.start = start  # 지금 날짜 (달력에서 표시)
        self.shown = start  # 보고 있는 달의 아무 날
        self.selected: str | None = None

        self.prev_btn = QToolButton(self)
        self.prev_btn.setText("‹")
        self.prev_btn.setToolTip("이전 달 (PgUp)")
        self.prev_btn.clicked.connect(lambda: self.go_month(-1))
        self.next_btn = QToolButton(self)
        self.next_btn.setText("›")
        self.next_btn.setToolTip("다음 달 (PgDown)")
        self.next_btn.clicked.connect(lambda: self.go_month(1))
        self.month_label = QLabel(self)
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.month_label.font()
        f.setBold(True)
        self.month_label.setFont(f)
        head = QHBoxLayout()
        head.addWidget(self.prev_btn)
        head.addWidget(self.month_label, 1)
        head.addWidget(self.next_btn)

        self.calendar = MonthCalendar(MIME_TODO, self)
        self.calendar.setAcceptDrops(False)
        self.calendar.day_clicked.connect(self.choose)

        self.today_btn = QPushButton("오늘", self)
        self.today_btn.clicked.connect(self.go_today)
        cancel = QPushButton("취소", self)
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addWidget(self.today_btn)
        foot.addStretch(1)
        foot.addWidget(cancel)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.calendar)
        lay.addLayout(foot)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, lambda: self.go_month(-1))
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, lambda: self.go_month(1))

        self._apply_colors(accent)
        self.refresh()
        self.setMinimumWidth(self.calendar.minimumWidth() + 40)
        self.resize(max(320, self.minimumWidth()), self.sizeHint().height())

    def _apply_colors(self, accent: str) -> None:
        """창은 시스템 색(밝게/어둡게)을 따르고, 달력 글자도 그 배경에 맞춘다."""
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.WindowText).name()
        bg = pal.color(QPalette.ColorRole.Window).name()
        on_accent = max((text, "#FFFFFF", "#1C1C1E"), key=lambda c: contrast(c, accent))
        self.calendar.set_colors(
            text=text,
            soft=_mix(text, bg, soft_alpha(text, bg)),
            accent=accent,
            on_accent=on_accent,
            current_bg=_mix(accent, bg, 0.18),
            drop=accent,
            sunday=HOLIDAY_RED if contrast(HOLIDAY_RED, bg) >= 4.5 else text,
            done_dot=_mix(text, bg, ring_alpha(text, bg)),
        )

    def refresh(self) -> None:
        d = date.fromisoformat(self.shown)
        self.month_label.setText(f"{d.year}년 {d.month}월")
        self.calendar.set_cells(
            calendar_cells(self.store, self.shown, current=self.start, hint=HINT)
        )

    def go_month(self, delta: int) -> None:
        self.shown = shift_month(self.shown, delta)
        self.refresh()

    def go_today(self) -> None:
        self.shown = today_str()
        self.refresh()

    def choose(self, day: str) -> None:
        self.selected = day
        self.accept()


def pick_date(
    store: TodoStore, start: str, *, accent: str = "#FF9F0A", parent: QWidget | None = None
) -> str | None:
    """창을 띄워 날짜를 고르게 한다. 취소하면 None."""
    dlg = DatePicker(store, start, accent=accent, parent=parent)
    try:
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected
        return None
    finally:
        dlg.deleteLater()
