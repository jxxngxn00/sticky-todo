"""설정 창의 '테마' 탭.

고르는 동안은 임시 폴더(themes/draft)에서 고치고 노트에 바로 미리 보여준다.
설정 창의 '확인'을 누르면 임시 폴더가 현재 테마(themes/current)가 되고,
'취소'를 누르면 임시 폴더를 지우고 원래 테마로 돌아간다.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .paths import default_export_dir
from .theme import (
    EXT,
    OFFSET_MAX,
    SLOT_NAMES,
    Background,
    ThemeFileError,
    ThemeImageError,
    copy_theme,
    export_theme,
    import_image,
    import_theme,
    load_example,
    load_theme,
    replace_folder,
    save_theme,
)

if TYPE_CHECKING:
    from .note_widget import NoteWidget

SLOT_HELP = {
    "pin": "제목줄의 '항상 위에 고정' 버튼. 꺼져 있으면 흐린 회색으로 보여요",
    "pin_on": "비워 두면 위 그림을 또렷하게 써요",
    "todo": "아직 안 한 일의 체크 자리 (하위 할 일도 같은 그림)",
    "done": "완료하면 이 그림으로 바뀌어요",
    "add": "입력칸 왼쪽 + 버튼",
    "today": "월간 달력에서 오늘 날짜 뒤",
    "background": "목록 뒤에 흐리게. 넓은 보드에서는 보이지 않아요",
}
ORDER = ("pin", "pin_on", "todo", "done", "add", "today", "background")
# 3×3 빠른 위치 (그림 가운데, 노트 안 비율)
SPOTS = [(x, y) for y in (0.15, 0.5, 0.85) for x in (0.2, 0.5, 0.8)]
IMAGE_FILTER = "그림 (*.png *.jpg *.jpeg *.webp)"
THEME_FILTER = "Sticky Todo 테마 (*.stodotheme)"


def _safe_name(name: str) -> str:
    """파일 이름에 쓸 수 없는 글자는 뺀다."""
    return "".join(c for c in name if c not in '\\/:*?"<>|').strip(" .")


THUMB = 32


class ThemeTab(QWidget):
    def __init__(self, note: NoteWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.note = note
        self.original = note.theme
        self.current_dir = note.themes_dir / "current"
        self.draft_dir = note.themes_dir / "draft"
        self.theme = copy_theme(self.original, self.draft_dir)
        self.thumbs: dict[str, QLabel] = {}
        self.clear_btns: dict[str, QPushButton] = {}

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("이름"))
        self.name_edit = QLineEdit(self.theme.name)
        self.name_edit.setMaxLength(50)
        self.name_edit.setPlaceholderText("테마 이름")
        self.name_edit.textChanged.connect(self._rename)
        top.addWidget(self.name_edit, 1)
        lay.addLayout(top)
        row = QHBoxLayout()
        self.example_btn = QPushButton("예시 테마 (귤 친구)")
        self.example_btn.clicked.connect(self.use_example)
        self.clear_all_btn = QPushButton("원래대로")
        self.clear_all_btn.setToolTip("테마를 모두 지우고 기본 모양으로")
        self.clear_all_btn.clicked.connect(self.clear_all)
        row.addWidget(self.example_btn)
        row.addWidget(self.clear_all_btn)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.load_btn = QPushButton("테마 불러오기…")
        self.load_btn.setToolTip(".stodotheme 파일 (노트 위로 끌어다 놓아도 돼요)")
        self.load_btn.clicked.connect(self.pick_theme_file)
        self.save_btn = QPushButton("테마 파일로 저장…")
        self.save_btn.setToolTip("지금 보이는 테마를 .stodotheme 파일 하나로 묶어요")
        self.save_btn.clicked.connect(self.save_theme_file)
        row.addWidget(self.load_btn)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        lay.addLayout(row)

        for slot in ORDER:
            lay.addWidget(self._slot_row(slot))
            if slot == "today":
                self.today_box = self._today_controls()
                lay.addWidget(self.today_box)
            if slot == "background":
                self.bg_box = self._bg_controls()
                lay.addWidget(self.bg_box)
        note_lbl = QLabel(
            "그림은 투명한 여백을 잘라내고 정사각형 가운데에 맞춰 저장해요. "
            "다른 사람이 만든 캐릭터 그림은 저작권을 확인하고 써 주세요."
        )
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(note_lbl)
        lay.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.sync()

    # ------------------------------------------------------------ 화면 만들기

    def _slot_row(self, slot: str) -> QWidget:
        box = QFrame()
        box.setObjectName("themeSlot")
        grid = QGridLayout(box)
        grid.setContentsMargins(20 if slot == "pin_on" else 0, 8, 0, 4)
        grid.setHorizontalSpacing(10)
        thumb = QLabel()
        thumb.setFixedSize(44, 44)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background: rgba(128,128,128,0.15); border-radius: 8px;")
        self.thumbs[slot] = thumb
        title = QLabel(f"<b>{SLOT_NAMES[slot]}</b>" + (" (선택)" if slot == "pin_on" else ""))
        help_lbl = QLabel(SLOT_HELP[slot])
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: palette(placeholder-text);")
        pick = QPushButton("내 그림…")
        pick.clicked.connect(lambda _=False, s=slot: self.pick_image(s))
        clear = QPushButton("비우기" if slot == "pin_on" else "기본")
        clear.clicked.connect(lambda _=False, s=slot: self.clear_slot(s))
        self.clear_btns[slot] = clear
        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addWidget(pick)
        buttons.addStretch(1)
        grid.addWidget(thumb, 0, 0, 3, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(title, 0, 1)
        grid.addWidget(help_lbl, 1, 1)
        grid.addLayout(buttons, 2, 1)
        grid.setColumnStretch(1, 1)
        return box

    def _slider(self, lo: int, hi: int, on_change) -> tuple[QWidget, QSlider, QLabel]:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        lbl = QLabel()
        lbl.setMinimumWidth(52)
        s.valueChanged.connect(on_change)
        lay.addWidget(s, 1)
        lay.addWidget(lbl)
        return w, s, lbl

    def _today_controls(self) -> QWidget:
        box = QWidget()
        lay = QGridLayout(box)
        lay.setContentsMargins(54, 0, 0, 6)
        self.today_number = QCheckBox("그림 위에 날짜 숫자 보이기")
        self.today_number.toggled.connect(self._set_today_number)
        lay.addWidget(self.today_number, 0, 0, 1, 2)
        hint = QLabel(
            "숫자는 칸 가운데에 있어요. 그림의 눈에 보이는 가운데가 다르면 그림을 옮겨요."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(hint, 1, 0, 1, 2)
        steps = int(OFFSET_MAX * 2)  # 0.5px 단위
        w, self.offset_x, self.offset_x_lbl = self._slider(-steps, steps, self._set_offset)
        lay.addWidget(QLabel("좌우"), 2, 0)
        lay.addWidget(w, 2, 1)
        w, self.offset_y, self.offset_y_lbl = self._slider(-steps, steps, self._set_offset)
        lay.addWidget(QLabel("위아래"), 3, 0)
        lay.addWidget(w, 3, 1)
        return box

    def _bg_controls(self) -> QWidget:
        box = QWidget()
        lay = QGridLayout(box)
        lay.setContentsMargins(54, 0, 0, 6)
        spots = QWidget()
        g = QGridLayout(spots)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(3)
        self.spot_btns: list[QToolButton] = []
        for i, (x, y) in enumerate(SPOTS):
            b = QToolButton()
            b.setFixedSize(QSize(20, 20))
            b.setCheckable(True)
            b.setToolTip("배경을 이 자리로")
            b.clicked.connect(lambda _=False, x=x, y=y: self._set_bg_pos(x, y))
            g.addWidget(b, i // 3, i % 3)
            self.spot_btns.append(b)
        self.move_btn = QPushButton("노트에서 끌어서 옮기기")
        self.move_btn.setCheckable(True)
        self.move_btn.toggled.connect(self._toggle_drag)
        place = QHBoxLayout()
        place.addWidget(spots)
        place.addWidget(self.move_btn)
        place.addStretch(1)
        lay.addLayout(place, 0, 0, 1, 2)
        w, self.bg_size, self.bg_size_lbl = self._slider(40, 600, self._set_bg_size)
        lay.addWidget(QLabel("크기"), 1, 0)
        lay.addWidget(w, 1, 1)
        w, self.bg_opacity, self.bg_opacity_lbl = self._slider(5, 100, self._set_bg_opacity)
        lay.addWidget(QLabel("진하기"), 2, 0)
        lay.addWidget(w, 2, 1)
        self.bg_warn = QLabel()
        self.bg_warn.setWordWrap(True)
        lay.addWidget(self.bg_warn, 3, 0, 1, 2)
        return box

    # ------------------------------------------------------------ 화면 ↔ 값

    def sync(self) -> None:
        """테마 값을 화면에 반영 (시그널 없이)."""
        t = self.theme
        dpr = self.devicePixelRatioF()
        for slot, lbl in self.thumbs.items():
            path = t.image(slot)
            if path is None:
                lbl.setPixmap(QPixmap())
                lbl.setText("비움" if slot == "pin_on" else "기본")
            else:
                pix = QPixmap.fromImage(QImage(str(path)))
                side = round(THUMB * dpr)
                pix = pix.scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                pix.setDevicePixelRatio(dpr)
                lbl.setText("")
                lbl.setPixmap(pix)
            self.clear_btns[slot].setEnabled(path is not None)
        with QSignalBlocker(self.name_edit):
            if self.name_edit.text() != t.name:
                self.name_edit.setText(t.name)
        self.today_box.setVisible(t.image("today") is not None)
        with QSignalBlocker(self.today_number):
            self.today_number.setChecked(t.today_number)
        for s, v, lbl in (
            (self.offset_x, t.today_offset[0], self.offset_x_lbl),
            (self.offset_y, t.today_offset[1], self.offset_y_lbl),
        ):
            with QSignalBlocker(s):
                s.setValue(round(v * 2))
            lbl.setText(f"{v:+g}px" if v else "0px")
        bg = t.background
        self.bg_box.setVisible(t.image("background") is not None)
        with QSignalBlocker(self.bg_size):
            self.bg_size.setValue(bg.width)
        self.bg_size_lbl.setText(f"{bg.width}px")
        with QSignalBlocker(self.bg_opacity):
            self.bg_opacity.setValue(bg.opacity)
        cap = getattr(self.note, "bg_opacity_cap", 100)
        self.bg_opacity_lbl.setText(f"{min(bg.opacity, cap)}%")
        self.bg_warn.setVisible(bg.opacity > cap)
        self.bg_warn.setText(f"글씨가 잘 안 읽혀서 {cap}%까지만 진해져요.")
        self._sync_spots()

    def _sync_spots(self) -> None:
        bg = self.theme.background
        for b, (x, y) in zip(self.spot_btns, SPOTS, strict=True):
            with QSignalBlocker(b):
                b.setChecked(abs(bg.x - x) < 0.01 and abs(bg.y - y) < 0.01)

    def _changed(self, rebuild: bool = True) -> None:
        """노트에 바로 미리보기. rebuild=False면 목록은 그대로 두고 그림만 (슬라이더용)."""
        self.note.set_theme(self.theme, rebuild=rebuild)
        self.sync()

    # ------------------------------------------------------------ 바꾸기

    def pick_image(self, slot: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"{SLOT_NAMES[slot]} 고르기", "", IMAGE_FILTER)
        if path:
            self.set_image(slot, Path(path))

    def set_image(self, slot: str, src: Path) -> bool:
        notes: list[str] = []
        try:
            dst = import_image(src, slot, self.draft_dir, notes)
        except (ThemeImageError, OSError) as e:
            QMessageBox.warning(self, "그림을 넣을 수 없어요", str(e))
            return False
        self.theme.images[slot] = dst
        self._changed()
        if notes:  # 넣기는 하되 알려만 준다
            QMessageBox.information(self, "그림이 작아요", notes[0])
        return True

    def clear_slot(self, slot: str) -> None:
        path = self.theme.images.pop(slot, None)
        if path is not None:
            path.unlink(missing_ok=True)
        if slot == "pin":  # 핀이 없으면 '고정됐을 때'도 의미가 없다
            self.clear_slot("pin_on")
            return
        self._changed()

    def clear_all(self) -> None:
        for slot in list(self.theme.images):
            self.theme.images.pop(slot).unlink(missing_ok=True)
        self.theme = replace(
            self.theme, name="", today_number=True, today_offset=(0.0, 0.0),
            background=Background(),
        )  # fmt: skip
        self._changed()

    def pick_theme_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 불러오기", "", THEME_FILTER)
        if path:
            self.load_theme_file(Path(path))

    def load_theme_file(self, path: Path) -> bool:
        """검사를 통과하면 미리보기로 적용 (확인을 눌러야 저장, 취소하면 원래대로)."""
        try:
            theme = import_theme(path, self.draft_dir)
        except (ThemeFileError, OSError) as e:
            QMessageBox.warning(self, "테마를 불러올 수 없어요", str(e))
            return False
        self.theme = theme
        if not self.theme.name:
            self.theme.name = path.stem[:50]
        self._changed()
        return True

    def save_theme_file(self) -> None:
        name = _safe_name(self.theme.name) or "내 테마"
        start = str(default_export_dir() / f"{name}{EXT}")
        path, _ = QFileDialog.getSaveFileName(self, "테마 파일로 저장", start, THEME_FILTER)
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            dest = export_theme(self.theme, Path(path))
        except OSError as e:
            QMessageBox.warning(self, "테마를 저장하지 못했어요", str(e))
            return
        self.note.show_toast(f"'{dest.name}'로 저장했어요")

    def use_example(self) -> None:
        self.theme = load_example(self.draft_dir)
        self._changed()

    def _rename(self, text: str) -> None:
        self.theme.name = text.strip()[:50]

    def _set_today_number(self, on: bool) -> None:
        self.theme.today_number = on
        self._changed(rebuild=False)

    def _set_offset(self, _v: int) -> None:
        self.theme.today_offset = (self.offset_x.value() / 2, self.offset_y.value() / 2)
        self._changed(rebuild=False)

    def _set_bg_pos(self, x: float, y: float) -> None:
        self.theme.background.x, self.theme.background.y = x, y
        self._changed(rebuild=False)

    def _set_bg_size(self, v: int) -> None:
        self.theme.background.width = v
        self._changed(rebuild=False)

    def _set_bg_opacity(self, v: int) -> None:
        self.theme.background.opacity = v
        self._changed(rebuild=False)

    def _toggle_drag(self, on: bool) -> None:
        self.move_btn.setText("옮기기 끝내기" if on else "노트에서 끌어서 옮기기")
        self.note.set_bg_drag(self._dragged if on else None)

    def _dragged(self, x: float, y: float) -> None:
        # 노트는 끌기 판이 직접 다시 그리므로 값만 맞춘다
        self.theme.background.x, self.theme.background.y = x, y
        self._sync_spots()

    # ------------------------------------------------------------ 확인 / 취소

    def commit(self) -> None:
        """'확인': 임시 폴더가 현재 테마가 된다."""
        self.note.set_bg_drag(None)
        try:
            save_theme(self.theme)
            replace_folder(self.draft_dir, self.current_dir)
        except OSError as e:
            QMessageBox.warning(self, "테마를 저장하지 못했어요", str(e))
            self.rollback()
            return
        self.note.set_theme(load_theme(self.current_dir))

    def rollback(self) -> None:
        """'취소': 원래 테마로 돌아가고 임시 폴더는 지운다."""
        self.note.set_bg_drag(None)
        self.note.set_theme(self.original)
        shutil.rmtree(self.draft_dir, ignore_errors=True)
