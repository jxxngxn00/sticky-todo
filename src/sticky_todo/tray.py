"""시스템 트레이 아이콘. 노트가 작업표시줄에 안 보이므로 다시 꺼내는 입구가 된다."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import icons

if TYPE_CHECKING:
    from .note_widget import NoteWidget


class Tray(QSystemTrayIcon):
    def __init__(self, note: NoteWidget) -> None:
        super().__init__(icons.app_icon(), note)
        self.note = note
        self.setToolTip("Sticky Todo")
        self._hint_shown = False

        self.menu = QMenu()
        self.toggle_action = self.menu.addAction("노트 보이기", note.toggle_visible)
        self.menu.addAction("오늘로 이동", self._show_today)
        self.menu.addAction("전체 목록 보기", note.open_manager_window)
        self.menu.addAction("설정…", note.open_settings_window)
        self.menu.addSeparator()
        self.auto_action = self.menu.addAction("시작 시 자동 실행")
        self.auto_action.setCheckable(True)
        self.auto_action.toggled.connect(note.set_autostart)
        self.menu.addSeparator()
        self.menu.addAction("종료", note.quit_app)
        self.menu.aboutToShow.connect(self._refresh_menu)
        self.setContextMenu(self.menu)

        self.activated.connect(self._on_activated)
        note.hidden_to_tray.connect(self._on_hidden)

    def _refresh_menu(self) -> None:
        self.toggle_action.setText("노트 숨기기" if self.note.isVisible() else "노트 보이기")
        auto = self.note.autostart
        self.auto_action.setEnabled(auto is not None)
        self.auto_action.blockSignals(True)
        self.auto_action.setChecked(auto is not None and auto.is_enabled())
        self.auto_action.blockSignals(False)

    def _show_today(self) -> None:
        self.note.go_today()
        if not self.note.isVisible():
            self.note.toggle_visible()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # 왼쪽 클릭
            self.note.toggle_visible()

    def _on_hidden(self) -> None:
        if not self._hint_shown:
            self._hint_shown = True
            self.showMessage(
                "Sticky Todo",
                "노트를 숨겼어요. 작업표시줄 오른쪽 트레이 아이콘을 누르면 다시 보여요.",
                icons.app_icon(),
                4000,
            )
