"""월간 보기의 달력.

날짜 칸마다 남은 일(강조색 점)·끝낸 일(회색 점)을 최대 3개까지 찍는다.
누르면 그 날을 고르고(아래 목록이 그 날로), 더블클릭하면 그 날 하루 보기로,
할 일을 끌어다 놓으면 그 날로 옮긴다.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QWidget

WEEK_HEADER = "일월화수목금토"
MAX_DOTS = 3
DOT = 5.0
GAP = 5.0  # 숫자와 점 사이
TODAY_BASE = 28.0  # 테마 오늘 표시 위치 조정값의 기준 칸 크기 (설정 창 미리보기와 같은 단위)


def _draw_centered(p: QPainter, rect: QRectF, text: str) -> None:
    """글자 모양(잉크) 기준으로 정확히 가운데에 그린다.

    AlignCenter는 글꼴 줄 높이(위·아래 여백 포함) 기준이라 숫자가 동그라미 안에서 내려가 보인다.
    """
    ink = p.fontMetrics().tightBoundingRect(text)
    x = rect.center().x() - ink.width() / 2 - ink.left()
    y = rect.center().y() - ink.height() / 2 - ink.top()
    p.drawText(QPointF(x, y), text)


@dataclass(frozen=True)
class Cell:
    day: str  # ISO 날짜
    number: int  # 날짜 숫자
    in_month: bool  # 이번 달이면 True (앞뒤 달 날짜는 흐리게)
    today: bool
    current: bool  # 고른 날
    open: int  # 남은 일 수
    done: int  # 끝낸 일 수
    sunday: bool
    tip: str  # 툴팁
    holiday: bool = False  # 공휴일 (일요일처럼 빨간 숫자)


class MonthCalendar(QWidget):
    day_clicked = Signal(str)
    day_double_clicked = Signal(str)
    todo_dropped = Signal(str, str)  # (할 일 id, 날짜)

    def __init__(self, mime_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("monthCalendar")
        self.mime_type = mime_type
        self.cells: list[Cell] = []
        self.colors = {
            "text": QColor("#1C1C1E"),
            "soft": QColor("#6E6E70"),
            "accent": QColor("#FF9F0A"),
            "on_accent": QColor("#1C1C1E"),
            "current_bg": QColor(255, 159, 10, 30),
            "drop": QColor("#FF9F0A"),
            "sunday": QColor("#B3261E"),
            "done_dot": QColor("#8E8E90"),
        }
        # 테마의 '오늘 표시' 그림 (없으면 강조색 동그라미)
        self.today_image: QPixmap | None = None
        self.today_offset: tuple[float, float] = (0.0, 0.0)  # 오늘 칸 28px 기준 px
        self.today_number = True
        self._press: int | None = None
        self._drop: int | None = None  # 끌어다 놓을 칸
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_metrics()

    def _update_metrics(self) -> None:
        """글자 크기에 맞춰 칸 크기를 정한다 (큰 글자에서도 숫자·동그라미·점이 겹치지 않게)."""
        fm = self.fontMetrics()
        self.num = max(24.0, fm.height() + 6.0)  # 날짜 숫자 칸 = 오늘 동그라미 지름
        self.cell_h = round(self.num + GAP + DOT + 10)  # 날짜 칸
        self.head_h = fm.height() + 6  # 요일 줄
        self.legend_h = fm.height() + 8  # 아래 '● 남은 일 ● 끝낸 일'
        self.setMinimumWidth(7 * round(self.num + 8))  # 칸마다 동그라미 + 양옆 여백
        self.setFixedHeight(self.head_h + self.rows * self.cell_h + self.legend_h)

    def changeEvent(self, e) -> None:  # noqa: N802
        if e.type() == e.Type.FontChange:
            self._update_metrics()
            self.update()
        super().changeEvent(e)

    # ------------------------------------------------------------ 데이터

    def set_cells(self, cells: list[Cell]) -> None:
        self.cells = cells
        self._update_metrics()
        self.update()

    def set_colors(self, **colors: str) -> None:
        for k, v in colors.items():
            self.colors[k] = QColor(v)
        self.update()

    @property
    def rows(self) -> int:
        return max(5, len(self.cells) // 7)

    # ------------------------------------------------------------ 위치

    def cell_rect(self, index: int) -> QRectF:
        w = self.width() / 7
        row, col = divmod(index, 7)
        return QRectF(col * w, self.head_h + row * self.cell_h, w, self.cell_h)

    def index_at(self, pos: QPointF) -> int | None:
        if pos.y() < self.head_h or pos.y() >= self.head_h + self.rows * self.cell_h:
            return None
        col = int(pos.x() // (self.width() / 7))
        row = int((pos.y() - self.head_h) // self.cell_h)
        i = row * 7 + col
        return i if 0 <= col < 7 and 0 <= i < len(self.cells) else None

    def index_of(self, day: str) -> int | None:
        return next((i for i, c in enumerate(self.cells) if c.day == day), None)

    # ------------------------------------------------------------ 그리기

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.colors
        base = QFont(self.font())
        small = QFont(base)
        small.setPointSizeF(max(base.pointSizeF() - 2, 8))
        bold = QFont(base)
        bold.setBold(True)

        # 요일
        w = self.width() / 7
        p.setFont(small)
        for col, name in enumerate(WEEK_HEADER):
            p.setPen(c["sunday"] if col == 0 else c["soft"])
            p.drawText(QRectF(col * w, 0, w, self.head_h), Qt.AlignmentFlag.AlignCenter, name)

        for i, cell in enumerate(self.cells):
            r = self.cell_rect(i)
            box = r.adjusted(2, 1, -2, -1)
            if cell.current:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c["current_bg"])
                p.drawRoundedRect(box, 9, 9)
            if i == self._drop:
                p.setPen(QPen(c["drop"], 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 9, 9)

            # 숫자(24) + 간격(GAP) + 점(DOT) 묶음을 칸 세로 가운데에
            top = r.center().y() - (self.num + GAP + DOT) / 2
            num_rect = QRectF(r.center().x() - self.num / 2, top, self.num, self.num)
            if cell.today and self.today_image is not None:
                # 테마 그림: 숫자 칸보다 조금 크게, 맞춘 만큼 옮겨서 (숫자는 칸 가운데 그대로)
                k = self.num / TODAY_BASE
                box = num_rect.adjusted(-4 * k, -4 * k, 4 * k, 4 * k)
                dx, dy = self.today_offset
                box.translate(dx * k, dy * k)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawPixmap(box, self.today_image, QRectF(self.today_image.rect()))
                p.setPen(c["text"] if self.today_number else Qt.GlobalColor.transparent)
                p.setFont(bold)
            elif cell.today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c["accent"])
                p.drawEllipse(num_rect)
                p.setPen(c["on_accent"])
                p.setFont(bold)
            else:
                if not cell.in_month:
                    p.setPen(c["soft"])
                elif cell.sunday or cell.holiday:
                    p.setPen(c["sunday"])
                else:
                    p.setPen(c["text"])
                p.setFont(bold if cell.current else base)
            _draw_centered(p, num_rect, str(cell.number))

            dots = [c["accent"]] * min(cell.open, MAX_DOTS)
            dots += [c["done_dot"]] * min(cell.done, MAX_DOTS - len(dots))
            if dots:
                gap = 3.0
                total = len(dots) * DOT + (len(dots) - 1) * gap
                x = r.center().x() - total / 2
                y = top + self.num + GAP
                p.setPen(Qt.PenStyle.NoPen)
                for color in dots:
                    p.setBrush(color)
                    p.drawEllipse(QRectF(x, y, DOT, DOT))
                    x += DOT + gap

        # 범례
        top = self.head_h + self.rows * self.cell_h
        p.setFont(small)
        x = 8.0
        for color, label in ((c["accent"], "남은 일"), (c["done_dot"], "끝낸 일")):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(x, top + self.legend_h / 2 - 3, 6, 6))
            x += 10
            p.setPen(c["soft"])
            text_w = p.fontMetrics().horizontalAdvance(label)
            p.drawText(
                QRectF(x, top, text_w + 2, self.legend_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )
            x += text_w + 14

    # ------------------------------------------------------------ 마우스

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = self.index_at(e.position())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            i = self.index_at(e.position())
            if i is not None and i == self._press:
                self.day_clicked.emit(self.cells[i].day)
            self._press = None
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        i = self.index_at(e.position())
        if e.button() == Qt.MouseButton.LeftButton and i is not None:
            self.day_double_clicked.emit(self.cells[i].day)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def event(self, e) -> bool:
        if e.type() == e.Type.ToolTip:
            i = self.index_at(QPointF(e.pos()))
            if i is not None:
                QToolTip.showText(e.globalPos(), self.cells[i].tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    # ------------------------------------------------------------ 끌어다 놓기

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(self.mime_type):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if not e.mimeData().hasFormat(self.mime_type):
            return
        i = self.index_at(e.position())
        if i != self._drop:
            self._drop = i
            self.update()
        if i is None:
            e.ignore()
        else:
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self._drop = None
        self.update()

    def dropEvent(self, e) -> None:  # noqa: N802
        i = self.index_at(e.position())
        self._drop = None
        self.update()
        if i is None or not e.mimeData().hasFormat(self.mime_type):
            return
        todo_id = bytes(e.mimeData().data(self.mime_type)).decode()
        e.acceptProposedAction()
        self.todo_dropped.emit(todo_id, self.cells[i].day)
