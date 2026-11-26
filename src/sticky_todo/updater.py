"""새 버전 알림: GitHub의 최신 릴리즈 번호만 물어보고, 지금보다 높으면 알려준다.

- 할 일·설정은 아무것도 보내지 않는다. 받는 것도 버전 번호와 릴리즈 페이지 주소뿐.
- 자동 확인은 하루 한 번 (켜고 10초 뒤, 그 뒤로는 한 시간마다 '24시간 지났나'만 본다).
- 다운로드·설치는 하지 않는다. 릴리즈 페이지를 브라우저로 열어 줄 뿐.
- 응답 속 주소가 이 저장소의 릴리즈 페이지가 아니면 버린다 (엉뚱한 링크를 열지 않게).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from . import __version__
from .settings import Settings

REPO = "jxxngxn00/sticky-todo"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE_PREFIX = f"https://github.com/{REPO}/releases/"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

MAX_BYTES = 256 * 1024  # 릴리즈 정보는 보통 10KB 남짓
TIMEOUT_MS = 10_000
FIRST_DELAY_MS = 10_000  # 켜자마자 접속하지 않고 잠깐 기다린다
TICK_MS = 60 * 60 * 1000  # 한 시간마다 '확인할 때가 됐나'만 본다
CHECK_INTERVAL_S = 24 * 60 * 60

_VERSION = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_SAFE_URL = re.compile(r"^[A-Za-z0-9._~/%-]*$")  # 경로에 쓸 수 있는 글자만

# 응답을 받으면 done(bytes), 실패하면 done(None)을 부르는 함수
Fetch = Callable[[Callable[[bytes | None], None]], None]


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """'v0.3.0' / '0.3.0' → (0, 3, 0). 그 밖의 모양(베타 등)은 None."""
    if not isinstance(text, str):
        return None
    m = _VERSION.match(text.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


@dataclass(frozen=True)
class Release:
    version: str  # 'v' 없이 '0.3.0'
    url: str  # 이 저장소의 릴리즈 페이지


def parse_release(data: bytes) -> Release | None:
    """GitHub API 응답에서 버전과 페이지 주소만 꺼낸다. 조금이라도 이상하면 None."""
    if not data or len(data) > MAX_BYTES:
        return None
    try:
        info = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("draft") or info.get("prerelease"):
        return None
    version = parse_version(info.get("tag_name"))
    url = info.get("html_url")
    if version is None or not _is_release_page(url):
        return None
    return Release(".".join(map(str, version)), url)


def _is_release_page(url: Any) -> bool:
    if not isinstance(url, str) or not url.startswith(RELEASE_PAGE_PREFIX):
        return False
    rest = url[len(RELEASE_PAGE_PREFIX) :]
    return bool(rest) and ".." not in rest and bool(_SAFE_URL.match(rest))


class Status(Enum):
    NEWER = "newer"
    LATEST = "latest"
    FAILED = "failed"


@dataclass(frozen=True)
class CheckResult:
    status: Status
    release: Release | None = None


class UpdateChecker(QObject):
    """하루 한 번 최신 버전을 확인한다. 테스트에서는 fetch·clock을 바꿔 끼운다."""

    found = Signal(object)  # 자동 확인에서 새 버전 발견 (같은 버전은 한 번만): Release
    manual_result = Signal(object)  # '업데이트 확인'을 눌렀을 때의 결과: CheckResult

    def __init__(
        self,
        settings: Settings,
        on_settings_changed: Callable[[], None] = lambda: None,
        *,
        current: str = __version__,
        fetch: Fetch | None = None,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._changed = on_settings_changed
        self.current = current
        self._fetch = fetch if fetch is not None else network_fetch(self)
        self._clock = clock
        self.latest: Release | None = None  # 확인해서 찾은 새 버전 (메뉴에 표시)
        self._busy = False
        self._manual_waiting = False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        QTimer.singleShot(FIRST_DELAY_MS, self, self.tick)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def is_due(self) -> bool:
        last = self.settings["update_last_check"]
        now = self._clock()
        # 시계가 뒤로 갔으면(last가 미래) 그냥 확인할 때로 본다
        return last <= 0 or now - last >= CHECK_INTERVAL_S or now < last

    def tick(self) -> None:
        """자동 확인: 켜져 있고 하루가 지났을 때만 접속한다."""
        if self.settings["update_check"] and self.is_due():
            self._run()

    def check_now(self) -> None:
        """사용자가 직접 누른 확인. 자동 확인을 꺼 둬도 동작한다."""
        self._manual_waiting = True
        self._run()

    def _run(self) -> None:
        if self._busy:  # 이미 물어보는 중이면 그 답을 같이 쓴다
            return
        self._busy = True
        self._fetch(self._on_reply)

    def _on_reply(self, data: bytes | None) -> None:
        self._busy = False
        manual, self._manual_waiting = self._manual_waiting, False
        release = parse_release(data) if data is not None else None
        if release is None:
            result = CheckResult(Status.FAILED)
        else:
            self.settings.set("update_last_check", int(self._clock()))
            if is_newer(release.version, self.current):
                self.latest = release
                result = CheckResult(Status.NEWER, release)
            else:
                self.latest = None
                result = CheckResult(Status.LATEST, release)
        if result.status is Status.NEWER and self.settings["update_notified"] != release.version:
            self.settings.set("update_notified", release.version)
            if not manual:  # 직접 확인했으면 결과 창으로 이미 알렸으니 풍선은 생략
                self.found.emit(release)
        if result.status is not Status.FAILED:
            self._changed()
        if manual:
            self.manual_result.emit(result)


def network_fetch(parent: QObject) -> Fetch:
    """Qt 네트워크로 GitHub API에 GET 한 번. 실패·시간 초과·너무 큰 응답은 None."""
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

    manager = QNetworkAccessManager(parent)

    def fetch(done: Callable[[bytes | None], None]) -> None:
        req = QNetworkRequest(QUrl(RELEASES_API))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", f"sticky-todo/{__version__}".encode())
        req.setTransferTimeout(TIMEOUT_MS)
        reply = manager.get(req)

        def on_progress(received: int, _total: int) -> None:
            if received > MAX_BYTES:
                reply.abort()

        def on_finished() -> None:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            ok = reply.error() == QNetworkReply.NetworkError.NoError and status == 200
            data = bytes(reply.read(MAX_BYTES + 1).data()) if ok else None
            reply.deleteLater()
            done(data if data is not None and len(data) <= MAX_BYTES else None)

        reply.downloadProgress.connect(on_progress)
        reply.finished.connect(on_finished)

    return fetch
