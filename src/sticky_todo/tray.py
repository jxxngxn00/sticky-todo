"""시스템 트레이 아이콘. 노트가 작업표시줄에 안 보이므로 다시 꺼내는 입구가 된다."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import icons

if TYPE_CHECKING:
    from .note_widget import NoteWidget
    from .updater import Release


class Tray(QSystemTrayIcon):
    def __init__(self, note: NoteWidget) -> None:
        super().__init__(icons.app_icon(), note)
        self.note = note
        self.setToolTip("Sticky Todo")

        self.menu = QMenu()
        self.update_action = self.menu.addAction("", note.open_release_page)
        self.update_sep = self.menu.addSeparator()
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
        self.messageClicked.connect(self._on_message_clicked)
        self._update_message = False  # 지금 떠 있는 풍선이 새 버전 알림인지

    def notify_update(self, release: Release) -> None:
        """자동 확인에서 새 버전을 찾았을 때 풍선 알림. 누르면 릴리즈 페이지."""
        self._update_message = True
        self.showMessage(
            "Sticky Todo 새 버전",
            f"{release.version} 버전이 나왔어요. 눌러서 받으러 가기",
            icons.app_icon(),
            15_000,
        )

    def _on_message_clicked(self) -> None:
        if self._update_message:
            self._update_message = False
            self.note.open_release_page()

    def _refresh_menu(self) -> None:
        latest = self.note.updater.latest if self.note.updater is not None else None
        if latest is not None:
            self.update_action.setText(f"새 버전 {latest.version} 받기")
        self.update_action.setVisible(latest is not None)
        self.update_sep.setVisible(latest is not None)
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
