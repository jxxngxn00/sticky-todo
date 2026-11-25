"""README용 화면 사진을 예시 할 일로 만든다 (실제 데이터는 쓰지 않음).

사용법:  uv run python tools/screenshots.py
결과물:  docs/images/*.png

- 화면을 띄우지 않고(offscreen) 그려서, 실행하는 동안 화면에 창이 뜨지 않는다.
- 배경은 투명 + 노트 그림자 그대로라 GitHub 밝은/어두운 테마 모두에서 자연스럽다.
- 날짜는 실행한 날 기준 (오늘이 들어간 주·달).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "images"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication([])

from sticky_todo import icons, resources  # noqa: E402
from sticky_todo.help_window import HelpWindow  # noqa: E402
from sticky_todo.note_widget import NoteWidget, week_days  # noqa: E402
from sticky_todo.settings import Settings  # noqa: E402
from sticky_todo.storage import TodoStore, today_str  # noqa: E402

# 시안과 같은 예시 할 일: 요일(일=0) → [(내용, 완료, 들여쓰기)]
SAMPLE: dict[int, list[tuple[str, bool, int]]] = {
    0: [("장보기", True, 0), ("빨래", True, 0)],
    1: [("주간 회의 자료 준비", True, 0), ("치과 예약 전화", True, 0)],
    2: [("엄마 생신 선물 고르기", False, 0), ("택배 반품 접수", True, 0)],
    3: [
        ("보고서 초안 보내기", True, 0),
        ("자료 첨부", True, 1),
        ("운동 30분", False, 0),
        ("카드값 확인", False, 0),
    ],
    4: [("건강검진", False, 0)],
    6: [("친구 결혼식", False, 0)],
}
# 달력에 점이 보이도록 이번 달 다른 날에도 몇 개 (일 → [(내용, 완료)])
MONTH_EXTRA: dict[int, list[tuple[str, bool]]] = {
    2: [("도서관 책 반납", True), ("화분 물 주기", True)],
    3: [("세탁소 맡기기", True)],
    5: [("자동차 정기 점검", True), ("보험 서류 정리", True), ("관리비 납부", True)],
    9: [("분리수거", True), ("우체국 들르기", True)],
    12: [("동창 모임", True)],
    16: [("치과 정기 검진", True), ("가계부 정리", True)],
    18: [("재활용 정리", True)],
}


def make_store(tmp: Path) -> TodoStore:
    """오늘이 들어간 주에 시안 할 일을, 오늘 요일 자리에 '오늘' 목록이 오도록 넣는다."""
    store = TodoStore(tmp / "todos.json", tmp / "backups", autosave=False)
    today = today_str()
    days = week_days(today)
    offset = days.index(today) - 3  # 시안은 수요일(3)이 오늘
    for wd, items in SAMPLE.items():
        i = wd + offset
        if 0 <= i < 7:
            _add(store, days[i], items)
    first = date.fromisoformat(today).replace(day=1)
    for d, items in MONTH_EXTRA.items():
        day = first.replace(day=d).isoformat()
        if day not in days and day < today:
            _add(store, day, [(t, done, 0) for t, done in items])
    store.set_day(today)
    return store


def _add(store: TodoStore, day: str, items: list[tuple[str, bool, int]]) -> None:
    store.set_day(day)
    for text, done, indent in items:
        t = store.add(text, indent)
        if done:
            store.set_done(t.id, True)


def settle() -> None:
    for _ in range(4):
        app.processEvents()


def snapshot(widget: QWidget) -> QImage:
    """투명 배경 위에 위젯(그림자 포함)을 그린다."""
    pix = QPixmap(widget.size())
    pix.fill(Qt.GlobalColor.transparent)
    widget.render(pix)
    return pix.toImage()


def note(store: TodoStore, tmp: Path, preset: str = "yellow") -> NoteWidget:
    s = Settings()
    s.apply_preset(preset)
    w = NoteWidget(store, s, tmp / f"settings-{preset}.json")
    w.show()
    return w


def shot(w: NoteWidget, mode: str, size: tuple[int, int], **opts) -> QImage:
    w.set_view_mode(mode)
    for k, v in opts.items():
        w.settings.set(k, v)
    w.resize(*size)
    settle()
    if mode == "month":
        w._apply_month_list()  # 목록을 숨기면 더 줄어들 수 있으니 크기를 한 번 더
        w.resize(*size)
        settle()
    return snapshot(w)


def side_by_side(images: list[QImage], gap: int = 0) -> QImage:
    """위쪽을 맞춰 나란히 (그림자 여백이 있으니 기본 간격 0)."""
    width = sum(i.width() for i in images) + gap * (len(images) - 1)
    height = max(i.height() for i in images)
    out = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    x = 0
    for img in images:
        p.drawImage(QPoint(x, 0), img)
        x += img.width() + gap
    p.end()
    return out


def save(img: QImage, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(str(path), "PNG", 0)  # 0 = 가장 많이 압축
    kb = path.stat().st_size // 1024
    print(f"{path.relative_to(ROOT)}  {img.width()}x{img.height()}  {kb} KB")


def main() -> int:
    resources.load_bundled_fonts()
    tmp = Path(tempfile.mkdtemp())
    save(icons.app_pixmap(96).toImage(), "icon.png")  # README 제목 옆 아이콘
    store = make_store(tmp)

    yellow = note(store, tmp, "yellow")
    ios = note(store, tmp, "ios")

    # 대표 사진: 노랑(주간) + iOS(월간) 나란히
    hero_week = shot(yellow, "week", (340, 720))
    hero_month = shot(ios, "month", (360, 720), month_list=True)
    save(side_by_side([hero_week, hero_month]), "hero.png")

    save(shot(yellow, "day", (320, 400)), "day.png")
    save(hero_week, "week.png")
    save(shot(yellow, "week", (1000, 460)), "board.png")
    save(shot(yellow, "month", (360, 700), month_list=True), "month.png")
    save(shot(yellow, "month", (360, 1), month_list=False), "month-calendar.png")

    # 색 프리셋 5개 (하루 보기)
    presets = []
    for name in ("yellow", "pink", "sky", "mint", "ios"):  # 할 일 세 줄이 보이는 높이
        w = note(store, tmp, name)
        presets.append(shot(w, "day", (260, 316)))
        w._settings_timer.stop()
        w.hide()
    save(side_by_side(presets), "presets.png")

    # 도움말: 단축키 탭 (저장 위치가 나오는 탭은 찍지 않음)
    help_win = HelpWindow(tmp, yellow)
    help_win.show()
    help_win.tabs.setCurrentIndex(0)
    settle()
    save(snapshot(help_win), "help.png")
    help_win.close()

    for w in (yellow, ios):
        w._settings_timer.stop()
        w.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
