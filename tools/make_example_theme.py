"""예시 테마 '귤 친구' 그림 만들기 (직접 그린 SVG → PNG).

사용법:  uv run python tools/make_example_theme.py
결과물:  src/sticky_todo/example_theme/ (theme.json + PNG)

그림을 고칠 때만 다시 실행한다. 앱은 만들어진 PNG만 쓴다 (QtSvg 불필요).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QByteArray, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

app = QGuiApplication([])

from sticky_todo.theme import (  # noqa: E402
    EXAMPLE_DIR,
    FILES,
    Background,
    Theme,
    save_theme,
    tidy_icon,
)

EYES = (
    "<circle cx='22' cy='30' r='3.5' fill='#4a2e1a'/>"
    "<circle cx='42' cy='30' r='3.5' fill='#4a2e1a'/>"
)
SMILE_EYES = (
    "<path d='M17 30q5-5 10 0M37 30q5-5 10 0' stroke='#4a2e1a' stroke-width='3' "
    "fill='none' stroke-linecap='round'/>"
)
MOUTH = (
    "<path d='M27 40q5 5 10 0' stroke='#4a2e1a' stroke-width='3' fill='none' "
    "stroke-linecap='round'/>"
)


def face(eyes: str) -> str:
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<circle cx='32' cy='32' r='29' fill='#ffa62b' stroke='#d9741a' stroke-width='3'/>"
        "<path d='M30 6q8-6 15-1-7 5-15 1z' fill='#5fb36b'/>"
        "<circle cx='18' cy='38' r='5' fill='#ff8c6b' opacity='.7'/>"
        "<circle cx='46' cy='38' r='5' fill='#ff8c6b' opacity='.7'/>"
        f"{eyes}{MOUTH}</svg>"
    )


SVGS = {
    "pin": face(EYES),
    "pin_on": face(SMILE_EYES),
    "add": face(EYES),
    "done": face(SMILE_EYES),
    "todo": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<circle cx='32' cy='32' r='27' fill='#ffffff' stroke='#c9a27a' stroke-width='5'/>"
        "<path d='M29 6q8-6 15-1-7 5-15 1z' fill='#8cc995'/></svg>"
    ),
    "today": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<circle cx='32' cy='32' r='29' fill='#ffc46b' stroke='#d9741a' stroke-width='3'/>"
        "<path d='M30 4q8-6 15-1-7 5-15 1z' fill='#5fb36b'/></svg>"
    ),
    "background": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 240'>"
        "<g fill='none' stroke='#8f8a82' stroke-width='5' stroke-linecap='round' "
        "stroke-linejoin='round'>"
        "<path d='M120 40c8-22 34-26 48-16-10 14-30 20-46 18'/>"
        "<path d='M118 44c-6-14-20-18-30-12 6 10 18 14 28 14'/>"
        "<circle cx='120' cy='140' r='92'/>"
        "<circle cx='88' cy='128' r='6' fill='#8f8a82'/>"
        "<circle cx='152' cy='128' r='6' fill='#8f8a82'/>"
        "<path d='M106 150q14 14 28 0'/></g>"
        "<circle cx='70' cy='152' r='12' fill='#ffb3a1' opacity='.55'/>"
        "<circle cx='170' cy='152' r='12' fill='#ffb3a1' opacity='.55'/></svg>"
    ),
}


def render(svg: str, px: int) -> QImage:
    r = QSvgRenderer(QByteArray(svg.encode()))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    return img


def main() -> int:
    EXAMPLE_DIR.mkdir(exist_ok=True)
    images = {}
    for slot, svg in SVGS.items():
        dst = EXAMPLE_DIR / FILES[slot]
        img = render(svg, 480) if slot == "background" else tidy_icon(render(svg, 256))
        img.save(str(dst), "PNG")
        images[slot] = dst
        print(dst.relative_to(ROOT), img.width(), "x", img.height())
    theme = Theme(
        name="귤 친구",
        folder=EXAMPLE_DIR,
        images=images,
        background=Background(x=0.72, y=0.8, width=200, opacity=40),
    )
    save_theme(theme)
    print((EXAMPLE_DIR / "theme.json").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
