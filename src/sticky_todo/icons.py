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
