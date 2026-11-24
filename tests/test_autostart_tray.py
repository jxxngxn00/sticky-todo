import pytest
from PySide6.QtWidgets import QSystemTrayIcon

from sticky_todo.autostart import VALUE_NAME, Autostart, launch_command
from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.tray import Tray


class FakeRunKey:
    """테스트는 실제 레지스트리를 건드리지 않는다."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_launch_command_is_quoted():
    exe = r"C:\Program Files\Sticky Todo\StickyTodo.exe"
    assert launch_command(frozen=True, executable=exe) == f'"{exe}"'
    dev = launch_command(frozen=False, executable=r"C:\a b\.venv\Scripts\python.exe")
    assert dev.startswith('"') and dev.endswith('" -m sticky_todo')


def test_autostart_enable_disable_refresh():
    key = FakeRunKey()
    old = r'"C:\old\StickyTodo.exe"'
    new = r'"D:\new\StickyTodo.exe"'
    a = Autostart(key, command=old)
    assert not a.is_enabled()
    a.set_enabled(True)
    assert key.values[VALUE_NAME] == old
    moved = Autostart(key, command=new)
    assert moved.is_enabled() and not moved.is_current()
    moved.refresh()
    assert key.values[VALUE_NAME] == new
    moved.set_enabled(False)
    assert VALUE_NAME not in key.values
    moved.refresh()  # 꺼져 있으면 아무것도 안 함
    assert VALUE_NAME not in key.values


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "s.json", autostart=Autostart(FakeRunKey(), "x"))
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def test_note_menu_toggles_autostart(note):
    menu = note.build_menu()
    auto = next(a for a in menu.actions() if a.text() == "시작 시 자동 실행")
    assert auto.isEnabled() and not auto.isChecked()
    auto.trigger()
    assert note.autostart.is_enabled()
    assert "자동" in note.toast_label.text()


def test_close_hides_to_tray_and_quit_really_closes(note, store):
    note.hide_to_tray = True
    store.add("저장돼야 함")
    hidden = []
    note.hidden_to_tray.connect(lambda: hidden.append(1))
    note.close()
    assert not note.isVisible() and hidden == [1]
    assert store.todos_file.exists()  # 숨길 때 바로 저장
    note.toggle_visible()
    assert note.isVisible()


def test_tray_menu(note):
    tray = Tray(note)
    tray._refresh_menu()
    labels = [a.text() for a in tray.menu.actions() if a.text()]
    assert labels[0] == "노트 숨기기"
    assert "종료" in labels and "시작 시 자동 실행" in labels
    note.hide()
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert note.isVisible()
