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

from .note_widget import resolve_font_family
from .settings import APPEARANCE_KEYS, LIST_KEYS, PRESETS

if TYPE_CHECKING:
    from .note_widget import NoteWidget

PRESET_NAMES = {"yellow": "노랑", "pink": "분홍", "sky": "하늘", "mint": "민트", "ios": "iOS"}


def dialog_buttons(
    dialog: QDialog, left: QPushButton | None = None
) -> tuple[QHBoxLayout, QPushButton, QPushButton]:
    """설정 창과 색 선택 창이 함께 쓰는 아래쪽 버튼 줄: [왼쪽 버튼] ……… [취소] [확인]"""
    cancel = QPushButton("취소")
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton("확인")
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    row = QHBoxLayout()
    if left is not None:
        row.addWidget(left)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(ok)
    return row, cancel, ok


class ColorPickerDialog(QDialog):
    """Qt 색 선택 위젯 + 설정 창과 같은 '취소 / 확인' 버튼."""

    def __init__(self, color: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.picker = QColorDialog(QColor(color), self)
        # Qt 기본 OK/Cancel은 숨기고 창 안에 위젯으로 넣는다
        self.picker.setOptions(
            QColorDialog.ColorDialogOption.NoButtons
            | QColorDialog.ColorDialogOption.DontUseNativeDialog
        )
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        if self.picker.layout() is not None:
            self.picker.layout().setContentsMargins(0, 0, 0, 0)
        buttons, self.cancel_btn, self.ok_btn = dialog_buttons(self)
        root = QVBoxLayout(self)
        root.addWidget(self.picker)
        root.addLayout(buttons)

    def selected_color(self) -> str:
        return self.picker.currentColor().name().upper()


class ColorButton(QPushButton):
    """누르면 컬러 피커가 열리는 색 견본 버튼."""

    color_picked = Signal(str)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._color = "#FFFFFF"
        self.setObjectName("colorSwatch")
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{title} 직접 고르기")
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color.upper()
        # 이 견본 버튼에만 적용 (선택자 없이 'QPushButton'으로 쓰면 자식 창의 버튼까지 물든다)
        self.setStyleSheet(
            f"QPushButton#colorSwatch {{ background: {self._color}; border: 1px solid #888;"
            f" border-radius: 4px; }}"
        )

    def _pick(self) -> None:
        # 견본 버튼이 아니라 설정 창을 부모로 연다 (견본 스타일이 번지지 않게)
        dlg = ColorPickerDialog(self._color, self._title, self.window())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_color(dlg.selected_color())
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
        buttons, self.cancel_btn, self.ok_btn = dialog_buttons(self, left=self.reset_btn)

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

        self.font_combo = QFontComboBox()
        # 한글이 깨지지 않는 글꼴만 보여준다
        self.font_combo.setWritingSystem(QFontDatabase.WritingSystem.Korean)
        self.font_combo.currentFontChanged.connect(lambda f: self.change("font_family", f.family()))
        form.addRow("글꼴", self.font_combo)

        size_box, self.font_size = self._slider("font_size", 10, 20, "pt")
        form.addRow("글자 크기", size_box)

        # --- 색상·모양
        self.sticky_group = QGroupBox("노트 색상")
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
        for key, name in (
            ("bg_color", "배경"),
            ("title_color", "제목 바"),
            ("text_color", "글자"),
            ("accent_color", "강조"),
        ):
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
                "check_style", [("square", "네모"), ("circle", "동그라미"), ("bullet", "점")]
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
        self.auto_archive_check = QCheckBox("날짜가 바뀌면 지난 날 완료한 항목을 보관함으로")
        self.auto_archive_check.toggled.connect(lambda on: self.change("auto_archive", on))
        form.addRow("보관", self.auto_archive_check)
        self.hotkey_check = QCheckBox("Ctrl+Alt+T로 어디서든 노트 불러오기")
        self.hotkey_check.toggled.connect(lambda on: self.change("global_hotkey", on))
        form.addRow("단축키", self.hotkey_check)
        self.update_check = QCheckBox("새 버전이 나오면 알려주기")
        self.update_check.setToolTip(
            "하루 한 번 GitHub에 최신 버전 번호만 물어봐요. 할 일 내용은 보내지 않아요."
        )
        self.update_check.toggled.connect(lambda on: self.change("update_check", on))
        form.addRow("업데이트", self.update_check)
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
        with QSignalBlocker(self.auto_archive_check):
            self.auto_archive_check.setChecked(s["auto_archive"])
        with QSignalBlocker(self.hotkey_check):
            self.hotkey_check.setChecked(s["global_hotkey"])
        with QSignalBlocker(self.update_check):
            self.update_check.setChecked(s["update_check"])
        for key, cb in self.color_buttons.items():
            cb.set_color(s[key])
        self._sync_visibility()

    def _sync_visibility(self) -> None:
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
        if key == "done_style":
            self._sync_visibility()

    def apply_preset(self, name: str) -> None:
        self.settings.apply_preset(name)
        self.note.apply_settings()
        for key, cb in self.color_buttons.items():
            cb.set_color(self.settings[key])

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
