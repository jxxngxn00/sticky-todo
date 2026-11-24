"""앱에서 쓰는 SVG 아이콘 (직접 그린 단순한 선 아이콘).

외부 아이콘 세트를 내려받지 않고 코드에 넣어둔다. `{c}` 자리에 색을 채워 그린다.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = 'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'

SVGS: dict[str, str] = {
    # 압정
    "pin": f'<path {_STROKE} d="M9 3h6l-1 6 3.5 3.5h-11L10 9 9 3z"/>'
    f'<path {_STROKE} d="M12 12.5V21"/>',
    # 꽉 찬 압정 (고정됨)
    "pin_fill": '<path fill="{c}" stroke="{c}" stroke-width="2" stroke-linejoin="round" '
    'd="M9 3h6l-1 6 3.5 3.5h-11L10 9 9 3z"/>'
    f'<path {_STROKE} d="M12 12.5V21"/>',
    # 점 세 개 (메뉴)
    "more": '<circle cx="5" cy="12" r="1.8" fill="{c}"/><circle cx="12" cy="12" r="1.8" '
    'fill="{c}"/><circle cx="19" cy="12" r="1.8" fill="{c}"/>',
    # 닫기
    "close": f'<path {_STROKE} d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    # 원 안의 X (줄 삭제)
    "remove": '<circle cx="12" cy="12" r="9" fill="{c}"/>'
    '<path fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" '
    'd="M9 9l6 6M15 9l-6 6"/>',
    # 원 안의 + (할 일 추가)
    "add": '<circle cx="12" cy="12" r="10" fill="{c}"/>'
    '<path fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" '
    'd="M12 7.5v9M7.5 12h9"/>',
    # 아래로 꺾인 화살표 (하위 할 일)
    "subtask": f'<path {_STROKE} d="M7 4v7a3 3 0 0 0 3 3h9"/><path {_STROKE} d="M16 11l3 3-3 3"/>',
    # 이전 / 다음 (날짜 넘기기)
    "chevron_left": f'<path {_STROKE} d="M14.5 6l-6 6 6 6"/>',
    "chevron_right": f'<path {_STROKE} d="M9.5 6l6 6-6 6"/>',
    # 돋보기
    "search": f'<circle {_STROKE} cx="11" cy="11" r="6.5"/><path {_STROKE} d="M16 16l4 4"/>',
}


def svg_source(name: str, color: str) -> str:
    body = SVGS[name].replace("{c}", color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 18, scale: float = 2.0) -> QPixmap:
    """색을 입힌 아이콘 그림. 고해상도 화면에서도 선명하도록 scale배로 그린다."""
    renderer = QSvgRenderer(QByteArray(svg_source(name, color).encode()))
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(scale)
    return px


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color.upper(), size))


def app_pixmap(size: int = 256) -> QPixmap:
    """앱 아이콘: 노란 메모지 + 동그라미 체크 + 줄 두 개."""
    from PySide6.QtGui import QColor, QPainterPath, QPen

    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64  # 64 기준 좌표로 그린다
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFD60A"))
    p.drawRoundedRect(QRectF(4 * s, 4 * s, 56 * s, 56 * s), 14 * s, 14 * s)
    # 체크 동그라미
    p.setBrush(QColor("#FF9F0A"))
    p.drawEllipse(QRectF(12 * s, 14 * s, 16 * s, 16 * s))
    check = QPainterPath()
    check.moveTo(15.5 * s, 22.5 * s)
    check.lineTo(19 * s, 26 * s)
    check.lineTo(25 * s, 18.5 * s)
    pen = QPen(QColor("#FFFFFF"), 2.6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(check)
    # 할 일 줄
    line = QPen(QColor("#3A3A3C"), 4 * s)
    line.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(line)
    p.drawLine(int(33 * s), int(22 * s), int(50 * s), int(22 * s))
    gray = QPen(QColor("#8E8E93"), 4 * s)
    gray.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(gray)
    p.drawLine(int(14 * s), int(40 * s), int(50 * s), int(40 * s))
    p.drawLine(int(14 * s), int(50 * s), int(38 * s), int(50 * s))
    p.end()
    return px


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon
