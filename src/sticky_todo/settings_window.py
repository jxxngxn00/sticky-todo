"""설정 창. 바꾸는 즉시 노트에 미리보기로 반영하고, 취소하면 열기 전 상태로 되돌린다."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .backdrop import acrylic_supported
from .note_widget import resolve_font_family
from .settings import APPEARANCE_KEYS, GLASS_TINTS, LIST_KEYS, PRESETS

if TYPE_CHECKING:
    from .note_widget import NoteWidget

PRESET_NAMES = {"yellow": "노랑", "pink": "분홍", "sky": "하늘", "mint": "민트"}
TINT_NAMES = {"light": "밝은", "dark": "어두운", "sky": "하늘", "pink": "분홍"}


class ColorButton(QPushButton):
    """누르면 컬러 피커가 열리는 색 견본 버튼."""

    color_picked = Signal(str)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._color = "#FFFFFF"
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{title} 직접 고르기")
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color.upper()
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; border: 1px solid #888;"
            f" border-radius: 4px; }}"
        )

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, self._title)
        if c.isValid():
            self.set_color(c.name())
            self.color_picked.emit(self._color)


def _swatch(fill: str, edge: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setFixedSize(34, 24)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QToolButton {{ background: {fill}; border: 3px solid {edge}; border-radius: 5px; }}"
    )
    return b


class SettingsWindow(QDialog):
    def __init__(self, note: NoteWidget) -> None:
        super().__init__(note)
        self.note = note
        self.settings = note.settings
        self._snapshot = self.settings.as_dict()
        self._radios: dict[str, tuple[QButtonGroup, list[str]]] = {}
        self.setWindowTitle("Sticky Todo 설정")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(420)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_look_tab(), "외형")
        tabs.addTab(self._build_list_tab(), "목록 동작")

        self.reset_btn = QPushButton("기본값으로")
        self.reset_btn.clicked.connect(self.reset_defaults)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("확인")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addLayout(buttons)

        self.load_values()

    # ------------------------------------------------------------ 공통 도구

    def _radio_row(self, key: str, options: list[tuple[str, str]]) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        group = QButtonGroup(box)
        for i, (_, label) in enumerate(options):
            rb = QRadioButton(label)
            group.addButton(rb, i)
            lay.addWidget(rb)
        lay.addStretch(1)
        values = [v for v, _ in options]
        group.idClicked.connect(lambda i, k=key, vs=values: self.change(k, vs[i]))
        self._radios[key] = (group, values)
        return box

    def _slider(self, key: str, lo: int, hi: int, unit: str) -> tuple[QWidget, QSlider]:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        label = QLabel()
        label.setMinimumWidth(44)
        slider.valueChanged.connect(lambda v: label.setText(f"{v}{unit}"))
        slider.valueChanged.connect(lambda v, k=key: self.change(k, v))
        lay.addWidget(slider, 1)
        lay.addWidget(label)
        return box, slider

    # ------------------------------------------------------------ 외형 탭

    def _build_look_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        form.addRow(
            "스타일", self._radio_row("style", [("sticky", "스티키 노트"), ("glass", "글래스")])
        )

        self.font_combo = QFontComboBox()
        # 한글이 깨지지 않는 글꼴만 보여준다
        self.font_combo.setWritingSystem(QFontDatabase.WritingSystem.Korean)
        self.font_combo.currentFontChanged.connect(lambda f: self.change("font_family", f.family()))
        form.addRow("글꼴", self.font_combo)

        size_box, self.font_size = self._slider("font_size", 10, 20, "pt")
        form.addRow("글자 크기", size_box)

        # --- 스티키 노트 모드
        self.sticky_group = QGroupBox("스티키 노트")
        sform = QFormLayout(self.sticky_group)
        presets = QHBoxLayout()
        self.preset_buttons: dict[str, QToolButton] = {}
        for key, name in PRESET_NAMES.items():
            p = PRESETS[key]
            b = _swatch(p["bg_color"], p["title_color"], name)
            b.clicked.connect(lambda _=False, k=key: self.apply_preset(k))
            self.preset_buttons[key] = b
            presets.addWidget(b)
        presets.addStretch(1)
        sform.addRow("프리셋", presets)

        colors = QHBoxLayout()
        self.color_buttons: dict[str, ColorButton] = {}
        for key, name in (("bg_color", "배경"), ("title_color", "제목 바"), ("text_color", "글자")):
            colors.addWidget(QLabel(name))
            cb = ColorButton(f"{name} 색")
            cb.color_picked.connect(lambda c, k=key: self.change(k, c))
            self.color_buttons[key] = cb
            colors.addWidget(cb)
            colors.addSpacing(6)
        colors.addStretch(1)
        sform.addRow("직접 지정", colors)

        radius_box, self.corner_radius = self._slider("corner_radius", 0, 24, "px")
        sform.addRow("모서리 둥글기", radius_box)
        self.shadow_check = QCheckBox("그림자 표시")
        self.shadow_check.toggled.connect(lambda on: self.change("shadow", on))
        sform.addRow("", self.shadow_check)
        form.addRow(self.sticky_group)

        # --- 글래스 모드
        self.glass_group = QGroupBox("글래스")
        gform = QFormLayout(self.glass_group)
        trans_box, self.glass_transparency = self._slider("glass_transparency", 0, 100, "%")
        gform.addRow("유리 투명도", trans_box)
        tints = QHBoxLayout()
        self.tint_buttons: dict[str, QToolButton] = {}
        for key, name in TINT_NAMES.items():
            color = GLASS_TINTS[key]
            b = _swatch(color, "#888888", name)
            b.clicked.connect(lambda _=False, c=color: self.set_tint(c))
            self.tint_buttons[key] = b
            tints.addWidget(b)
        self.tint_custom = ColorButton("유리 색조")
        self.tint_custom.color_picked.connect(lambda c: self.change("glass_tint", c))
        tints.addSpacing(8)
        tints.addWidget(self.tint_custom)
        tints.addStretch(1)
        gform.addRow("유리 색조", tints)
        gform.addRow(
            "모서리",
            self._radio_row(
                "glass_corner", [("none", "없음"), ("small", "작게"), ("round", "보통")]
            ),
        )
        hint = (
            "노트를 클릭해 활성화하면 유리처럼 비쳐 보이고, 다른 창을 쓰는 동안에는 "
            "윈도우가 단색으로 바꿔 그려요."
            if acrylic_supported()
            else "이 PC에서는 블러 효과를 쓸 수 없어서 반투명 배경으로 보여요. "
            "(윈도우 11 22H2 이상 필요)"
        )
        self.glass_hint = QLabel(hint)
        self.glass_hint.setWordWrap(True)
        self.glass_hint.setStyleSheet("color: palette(placeholder-text);")
        gform.addRow(self.glass_hint)
        form.addRow(self.glass_group)

        form.addRow(
            "제목 바",
            self._radio_row(
                "title_bar_mode", [("always", "항상 표시"), ("hover", "마우스 올릴 때만")]
            ),
        )
        return page

    # ------------------------------------------------------------ 목록 동작 탭

    def _build_list_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.addRow(
            "체크 스타일",
            self._radio_row(
                "check_style", [("square", "네모"), ("circle", "동그라미"), ("bullet", "불릿")]
            ),
        )
        form.addRow(
            "완료 항목",
            self._radio_row(
                "done_style",
                [
                    ("strike", "취소선"),
                    ("dim", "흐리게"),
                    ("bottom", "맨 아래로"),
                    ("hide", "숨기기"),
                ],
            ),
        )
        self.done_hint = QLabel()
        self.done_hint.setWordWrap(True)
        self.done_hint.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", self.done_hint)
        form.addRow(
            "줄 간격",
            self._radio_row(
                "line_spacing", [("compact", "촘촘"), ("normal", "보통"), ("relaxed", "여유")]
            ),
        )
        return page

    # ------------------------------------------------------------ 값 ↔ 화면

    def load_values(self) -> None:
        """현재 설정값을 화면에 반영 (시그널 없이)."""
        s = self.settings
        for key, (group, values) in self._radios.items():
            blocker = QSignalBlocker(group)
            btn = group.button(values.index(s[key]))
            btn.setChecked(True)
            del blocker
        widgets: list[tuple[Any, Any]] = [
            (self.font_size, s["font_size"]),
            (self.corner_radius, s["corner_radius"]),
            (self.glass_transparency, s["glass_transparency"]),
        ]
        for slider, value in widgets:
            # 값 라벨은 갱신돼야 하므로 슬라이더 자체 시그널은 막지 않고 change만 건너뛴다
            self._loading = True
            slider.setValue(value)
            slider.valueChanged.emit(value)
            self._loading = False
        with QSignalBlocker(self.font_combo):
            self.font_combo.setCurrentFont(QFont(resolve_font_family(s["font_family"])))
        with QSignalBlocker(self.shadow_check):
            self.shadow_check.setChecked(s["shadow"])
        for key, cb in self.color_buttons.items():
            cb.set_color(s[key])
        self.tint_custom.set_color(s["glass_tint"])
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        glass = self.settings["style"] == "glass"
        self.sticky_group.setVisible(not glass)
        self.glass_group.setVisible(glass)
        self.done_hint.setText(
            "같은 부모 안에서 완료 항목을 아래로 내려요. 이 모드에서는 드래그 정렬이 꺼져요."
            if self.settings["done_style"] == "bottom"
            else "하위 항목까지 모두 완료된 항목만 숨겨요."
            if self.settings["done_style"] == "hide"
            else ""
        )
        self.done_hint.setVisible(bool(self.done_hint.text()))
        self.adjustSize()

    _loading = False

    def change(self, key: str, value: Any) -> None:
        if self._loading:
            return
        if self.settings[key] == value:
            return
        self.settings.set(key, value)
        self.note.apply_settings()
        if key in ("style", "done_style"):
            self._sync_visibility()

    def apply_preset(self, name: str) -> None:
        self.settings.apply_preset(name)
        self.note.apply_settings()
        for key, cb in self.color_buttons.items():
            cb.set_color(self.settings[key])

    def set_tint(self, color: str) -> None:
        self.tint_custom.set_color(color)
        self.change("glass_tint", color.upper())

    def reset_defaults(self) -> None:
        self.settings.reset(APPEARANCE_KEYS + LIST_KEYS)
        self.note.apply_settings()
        self.load_values()

    # ------------------------------------------------------------ 확인/취소

    def accept(self) -> None:
        self.note.save_settings()
        super().accept()

    def reject(self) -> None:
        self.settings.restore(self._snapshot)
        self.note.apply_settings()
        super().reject()
