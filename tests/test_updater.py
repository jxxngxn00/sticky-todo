"""새 버전 알림. 실제 인터넷에는 접속하지 않고 가짜 응답으로 확인한다."""

import json

import pytest
from PySide6.QtWidgets import QMessageBox

from sticky_todo import updater
from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.tray import Tray
from sticky_todo.updater import (
    CHECK_INTERVAL_S,
    MAX_BYTES,
    Release,
    Status,
    UpdateChecker,
    is_newer,
    parse_release,
    parse_version,
)

PAGE = "https://github.com/jxxngxn00/sticky-todo/releases/tag/v0.3.0"


def body(tag="v0.3.0", url=PAGE, **extra) -> bytes:
    return json.dumps({"tag_name": tag, "html_url": url, **extra}).encode()


class FakeFetch:
    """GitHub 대신 정해 둔 응답을 돌려준다. hold=True면 답을 미뤄 둔다."""

    def __init__(self, data: bytes | None = None, hold: bool = False):
        self.data = data
        self.hold = hold
        self.calls = 0
        self.pending = []

    def __call__(self, done):
        self.calls += 1
        if self.hold:
            self.pending.append(done)
        else:
            done(self.data)

    def answer(self):
        for done in self.pending:
            done(self.data)
        self.pending.clear()


class Clock:
    def __init__(self, now=1_800_000_000):
        self.now = now

    def __call__(self):
        return self.now


def make_checker(qapp, data=None, *, current="0.2.0", settings=None, **kw):
    fetch = kw.pop("fetch", None) or FakeFetch(data)
    clock = kw.pop("clock", None) or Clock()
    saved = []
    c = UpdateChecker(
        settings or Settings(),
        lambda: saved.append(1),
        current=current,
        fetch=fetch,
        clock=clock,
    )
    return c, fetch, clock, saved


# ------------------------------------------------------------ 버전·응답 읽기


def test_parse_version():
    assert parse_version("v0.3.0") == (0, 3, 0)
    assert parse_version("1.10.2") == (1, 10, 2)
    for bad in ("v0.3", "v0.3.0-beta", "latest", "", None, 3, "v0.3.0.1"):
        assert parse_version(bad) is None


def test_is_newer_compares_numbers_not_text():
    assert is_newer("v0.10.0", "0.9.9")  # 글자로 비교하면 틀리는 경우
    assert is_newer("v1.0.0", "0.2.0")
    assert not is_newer("v0.2.0", "0.2.0")
    assert not is_newer("v0.1.9", "0.2.0")
    assert not is_newer("beta", "0.2.0")


def test_parse_release_reads_version_and_page():
    assert parse_release(body()) == Release("0.3.0", PAGE)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"\xff\xfe",
        b"[]",
        body(tag="nightly"),
        body(tag=None),
        body(url=None),
        body(draft=True),
        body(prerelease=True),
        body(url="https://evil.example.com/releases/tag/v0.3.0"),
        body(url="http://github.com/jxxngxn00/sticky-todo/releases/tag/v0.3.0"),
        body(url="https://github.com/someone/else/releases/tag/v0.3.0"),
        body(url="https://github.com/jxxngxn00/sticky-todo/releases/"),
        body(url="https://github.com/jxxngxn00/sticky-todo/releases/../../evil"),
        body(url="https://github.com/jxxngxn00/sticky-todo/releases/tag/x?y=1"),
        b" " * (MAX_BYTES + 1),
    ],
)
def test_parse_release_rejects_anything_odd(data):
    assert parse_release(data) is None


# ------------------------------------------------------------ 언제 확인하나


def test_checks_once_a_day(qapp):
    c, fetch, clock, saved = make_checker(qapp, body(tag="v0.2.0"))
    c.tick()
    assert fetch.calls == 1 and saved
    assert c.settings["update_last_check"] == clock.now
    clock.now += CHECK_INTERVAL_S - 60
    c.tick()
    assert fetch.calls == 1  # 아직 하루가 안 지남
    clock.now += 60
    c.tick()
    assert fetch.calls == 2


def test_turned_off_means_no_connection(qapp):
    s = Settings()
    s.set("update_check", False)
    c, fetch, *_ = make_checker(qapp, body(), settings=s)
    c.tick()
    assert fetch.calls == 0
    c.check_now()  # 직접 누른 확인은 꺼져 있어도 동작
    assert fetch.calls == 1


def test_failure_retries_next_tick(qapp):
    c, fetch, clock, saved = make_checker(qapp, None)
    c.tick()
    assert c.settings["update_last_check"] == 0 and not saved
    clock.now += 3600
    c.tick()
    assert fetch.calls == 2


def test_clock_going_backwards_still_checks(qapp):
    s = Settings()
    s.set("update_last_check", 1_900_000_000)  # 미래
    c, fetch, *_ = make_checker(qapp, body(), settings=s)
    c.tick()
    assert fetch.calls == 1


def test_no_second_request_while_waiting(qapp):
    fetch = FakeFetch(body(), hold=True)
    c, *_ = make_checker(qapp, fetch=fetch)
    results = []
    c.manual_result.connect(results.append)
    c.tick()
    c.check_now()
    assert fetch.calls == 1
    fetch.answer()
    assert [r.status for r in results] == [Status.NEWER]  # 직접 누른 쪽도 답을 받는다


# ------------------------------------------------------------ 알림


def test_found_once_per_version(qapp):
    c, fetch, clock, _ = make_checker(qapp, body())
    found = []
    c.found.connect(found.append)
    c.tick()
    assert found == [Release("0.3.0", PAGE)] and c.latest == found[0]
    clock.now += CHECK_INTERVAL_S
    c.tick()
    assert len(found) == 1  # 같은 버전은 다시 풍선을 띄우지 않음
    assert c.latest is not None  # 메뉴 항목은 계속 보임
    fetch.data = body(tag="v0.4.0", url=PAGE.replace("0.3.0", "0.4.0"))
    clock.now += CHECK_INTERVAL_S
    c.tick()
    assert [r.version for r in found] == ["0.3.0", "0.4.0"]


def test_latest_version_clears_menu_item(qapp):
    c, fetch, clock, _ = make_checker(qapp, body())
    c.tick()
    assert c.latest is not None
    fetch.data = body(tag="v0.2.0")  # 그 릴리즈가 내려간 경우
    clock.now += CHECK_INTERVAL_S
    c.tick()
    assert c.latest is None


def test_manual_check_results_and_no_balloon(qapp):
    found, results = [], []
    c, fetch, *_ = make_checker(qapp, body())
    c.found.connect(found.append)
    c.manual_result.connect(results.append)
    c.check_now()
    assert results[-1].status is Status.NEWER and not found
    assert c.settings["update_notified"] == "0.3.0"  # 자동 확인에서도 다시 띄우지 않음
    fetch.data = body(tag="v0.2.0")
    c.check_now()
    assert results[-1].status is Status.LATEST
    fetch.data = None
    c.check_now()
    assert results[-1].status is Status.FAILED


def test_settings_validate_update_keys():
    s = Settings({"update_check": "yes", "update_last_check": -5, "update_notified": 3})
    assert s["update_check"] is True
    assert s["update_last_check"] == 0
    assert s["update_notified"] == ""


# ------------------------------------------------------------ 노트·트레이


@pytest.fixture
def note(store, tmp_path):
    w = NoteWidget(store, Settings(), tmp_path / "s.json")
    w.show()
    yield w
    if w.update_box is not None:
        w.update_box.close()
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(
        "sticky_todo.note_widget.QDesktopServices.openUrl", lambda u: urls.append(u.toString())
    )
    return urls


def attach(note, data):
    c, fetch, *_ = make_checker(None, data, settings=note.settings)
    note.attach_updater(c)
    return c, fetch


def labels(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_menu_without_updater_has_no_update_items(note):
    texts = labels(note.build_menu())
    assert "업데이트 확인" not in texts
    assert not any(t.startswith("새 버전") for t in texts)


def test_menu_shows_new_version_and_opens_page(note, opened):
    c, _ = attach(note, body())
    assert "업데이트 확인" in labels(note.build_menu())
    c.tick()
    menu = note.build_menu()
    first = menu.actions()[0]
    assert first.text() == "새 버전 0.3.0 받기"
    first.trigger()
    assert opened == [PAGE]


def test_manual_check_dialogs(note, opened):
    c, fetch = attach(note, body())
    note.check_updates_now()
    box = note.update_box
    assert box is not None and "0.2.0 → 0.3.0" in box.text()
    go = next(b for b in box.buttons() if b.text() == "받으러 가기")
    go.click()
    assert opened == [PAGE]

    fetch.data = body(tag=f"v{updater.__version__}")
    note.check_updates_now()
    assert "최신 버전" in note.update_box.text()
    fetch.data = None
    note.check_updates_now()
    assert note.update_box.icon() == QMessageBox.Icon.Warning
    assert "확인하지 못했어요" in note.update_box.text()


def test_tray_balloon_and_menu(note, opened, monkeypatch):
    c, _ = attach(note, body())
    tray = Tray(note)
    shown = []
    monkeypatch.setattr(tray, "showMessage", lambda *a: shown.append(a))
    c.found.connect(tray.notify_update)
    tray._refresh_menu()
    assert not tray.update_action.isVisible()
    c.tick()
    assert shown and "0.3.0" in shown[0][1]
    tray.messageClicked.emit()
    assert opened == [PAGE]
    tray.messageClicked.emit()  # 다른 풍선(새 버전 알림이 아닌 것)은 무시
    assert opened == [PAGE]
    tray._refresh_menu()
    assert tray.update_action.isVisible() and tray.update_action.text() == "새 버전 0.3.0 받기"
    tray.hide()
    tray.deleteLater()


def test_settings_window_has_update_toggle(note):
    from sticky_todo.settings_window import SettingsWindow

    win = SettingsWindow(note)
    assert win.update_check.isChecked()
    win.update_check.setChecked(False)
    assert note.settings["update_check"] is False
    win.close()
