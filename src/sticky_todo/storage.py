"""할 일 데이터의 유일한 소유자 TodoStore.

- 변경할 때마다 ``changed`` 시그널을 보내고, 300ms 디바운스 후 자동 저장한다.
- 할 일은 **날짜별 페이지**에 속한다 (``Todo.day``, "YYYY-MM-DD"). 노트는 한 번에 한
  날짜(``current_day``)를 보여주고, 관리 창은 모든 날짜를 함께 검색한다.
- 한 페이지 안의 할 일은 화면 순서대로 평평한 리스트로 들고 있고, ``indent``(0, 1, 2…)로
  계층을 표현한다. 규칙: 첫 항목은 0, 각 항목은 바로 앞 항목 indent + 1 이하.
- 어떤 항목의 '하위 항목'은 바로 뒤에 이어지는, indent가 더 큰 항목들이다.
  이동·삭제·들여쓰기는 하위 항목까지 묶어서 처리하고, 완료 체크는 그 항목만 바꾼다.
- 완료 항목을 '보관'하면 페이지에서 빠져 보관함(archive)으로 간다. 지워지지 않고
  관리 창에서 다시 보거나 복원할 수 있다.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .fileio import atomic_write_text

CURRENT_VERSION = 3  # v2: 보관함(archive), v3: 날짜별 페이지(day)
SAVE_DEBOUNCE_MS = 300
MAX_BACKUPS = 5
BACKUP_INTERVAL_SEC = 600  # 같은 세션에서 백업은 최대 10분에 한 번
MAX_UNDO = 20


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return date.today().isoformat()


MAX_TEXT_LEN = 500  # 할 일 한 개의 최대 글자 수 (입력칸과 같음)


def _clean_text(text: str) -> str:
    """공백 정리 + 길이 제한. 아주 긴 글 하나로 화면이 멈추는 걸 막는다."""
    return " ".join(str(text).split())[:MAX_TEXT_LEN]


@dataclass
class Todo:
    id: str
    text: str
    done: bool = False
    created_at: str = field(default_factory=now_iso)
    completed_at: str | None = None
    order: int = 0
    indent: int = 0
    archived_at: str | None = None  # 보관한 시각 (보관함에 있을 때만)
    day: str = field(default_factory=today_str)  # 속한 날짜 페이지

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "done": self.done,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "order": self.order,
            "indent": self.indent,
            "archived_at": self.archived_at,
            "day": self.day,
        }


# ---------------------------------------------------------------- 로드/검증


class DataError(Exception):
    """todos.json 내용이 손상되었거나 형식이 맞지 않음."""


class UnsupportedVersionError(Exception):
    """앱보다 새 버전에서 저장한 파일. 덮어쓰면 데이터가 손실될 수 있어 거부한다."""


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v1 → v2: 보관함(archive) 목록 추가."""
    return {**data, "version": 2, "archive": []}


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """v2 → v3: 날짜별 페이지. 쓰던 할 일은 모두 오늘 페이지로 간다."""
    today = today_str()
    raw = data.get("todos")
    if not isinstance(raw, list):
        return {**data, "version": 3}  # 구조 오류는 parse_document가 잡는다
    todos = [{**t, "day": today} if isinstance(t, dict) else t for t in raw]
    return {**data, "version": 3, "todos": todos}


# from_version -> 다음 버전 dict로 변환하는 함수
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
}


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DataError(f"잘못된 version 값: {version!r}")
    if version > CURRENT_VERSION:
        raise UnsupportedVersionError(
            f"이 파일은 더 새로운 버전(v{version})의 앱에서 저장됐어. 앱을 업데이트해줘."
        )
    while version < CURRENT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise DataError(f"v{version} 마이그레이션 함수가 없음")
        data = step(data)
        version = data["version"]
    return data


def _valid_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_day(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _int_or(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _parse_items(raw: list, seen: set[str], *, archived: bool) -> list[Todo]:
    items: list[Todo] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = _clean_text(item["text"])
        if not text:
            continue
        tid = item.get("id")
        if not isinstance(tid, str) or not tid or tid in seen:
            tid = str(uuid.uuid4())
        seen.add(tid)
        done = item.get("done") is True
        created = item.get("created_at")
        created = created if _valid_iso(created) else now_iso()
        completed = item.get("completed_at")
        archived_at = item.get("archived_at")
        day = item.get("day")
        if not _valid_day(day):
            day = _local_date(created).isoformat() if archived else today_str()
        items.append(
            Todo(
                id=tid,
                text=text,
                done=done,
                created_at=created,
                completed_at=completed if done and _valid_iso(completed) else None,
                order=_int_or(item.get("order"), i),
                indent=0 if archived else max(0, _int_or(item.get("indent"), 0)),
                archived_at=(archived_at if _valid_iso(archived_at) else now_iso())
                if archived
                else None,
                day=day,
            )
        )
    return items


def parse_document(data: Any) -> tuple[list[Todo], list[Todo]]:
    """JSON 객체를 검증해서 (페이지 할 일 전부, 보관함). 전체 구조가 틀리면 DataError.

    할 일은 (날짜, 페이지 안 순서)로 정렬해 돌려준다.
    개별 항목의 사소한 문제(시간 형식 등)는 기본값으로 고치고,
    text가 없는 항목은 건너뛴다.
    """
    if not isinstance(data, dict):
        raise DataError("최상위가 객체가 아님")
    data = migrate(data)
    raw = data.get("todos")
    if not isinstance(raw, list):
        raise DataError("todos가 리스트가 아님")
    raw_archive = data.get("archive", [])
    if not isinstance(raw_archive, list):
        raise DataError("archive가 리스트가 아님")

    seen: set[str] = set()
    todos = _parse_items(raw, seen, archived=False)
    # 원래 리스트 위치를 3차 키로 써서 order가 같아도 순서가 안정적이게
    todos = [t for _, t in sorted(enumerate(todos), key=lambda p: (p[1].day, p[1].order, p[0]))]
    for day_list in _group_by_day(todos).values():
        normalize(day_list)
    archive = _parse_items(raw_archive, seen, archived=True)
    return todos, archive


def parse_todos(data: Any) -> list[Todo]:
    """페이지 할 일만 (테스트·호환용)."""
    return parse_document(data)[0]


def normalize(todos: list[Todo]) -> None:
    """한 페이지의 indent 규칙을 맞추고 order를 0..n-1로 다시 매긴다."""
    prev = -1
    for i, t in enumerate(todos):
        t.indent = max(0, min(t.indent, prev + 1))
        prev = t.indent
        t.order = i


def _group_by_day(todos: Iterable[Todo]) -> dict[str, list[Todo]]:
    days: dict[str, list[Todo]] = {}
    for t in todos:
        days.setdefault(t.day, []).append(t)
    return days


def read_todos_file(path: Path) -> tuple[list[Todo], list[Todo]]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DataError(str(e)) from e
    return parse_document(data)


def _local_date(iso: str | None) -> date | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone().date()


# ---------------------------------------------------------------- 검색·정렬


class Status(StrEnum):
    ALL = "all"  # 모든 날짜 페이지 (보관함 제외)
    ACTIVE = "active"
    DONE = "done"
    ARCHIVED = "archived"  # 보관함


class SortKey(StrEnum):
    CREATED_DESC = "created_desc"  # 최근 추가순
    CREATED_ASC = "created_asc"  # 오래된순
    COMPLETED_DESC = "completed_desc"  # 최근 완료순
    ALPHA = "alpha"  # 가나다순
    MANUAL = "manual"  # 날짜 → 페이지 순서


def _ts(value: str | None) -> float:
    if not value:
        return float("-inf")
    return datetime.fromisoformat(value).timestamp()


# ---------------------------------------------------------------- 로드 결과


class LoadStatus(StrEnum):
    OK = "ok"
    NEW = "new"  # 파일이 없어서 빈 목록으로 시작
    RECOVERED = "recovered"  # 손상 → 백업에서 복구
    RESET = "reset"  # 손상 + 쓸 만한 백업 없음 → 빈 목록


@dataclass
class LoadResult:
    status: LoadStatus
    message: str = ""
    backup_used: Path | None = None
    corrupt_copy: Path | None = None


# ---------------------------------------------------------------- 스토어


@dataclass
class _UndoBatch:
    items: list[tuple[int, Todo]]  # 페이지에서 빠진 (그 페이지에서의 원래 위치, 항목)
    indents: dict[str, int]  # 빼기 직전 그 페이지들의 indent (되돌릴 때 계층 복원용)
    archived: bool = False  # True면 지운 게 아니라 보관함으로 옮긴 것
    archive_items: list[tuple[int, Todo]] = field(default_factory=list)  # 보관함에서 지운 것


class TodoStore(QObject):
    changed = Signal()
    save_failed = Signal(str)

    def __init__(
        self,
        todos_file: Path,
        backups_dir: Path,
        *,
        autosave: bool = True,
        debounce_ms: int = SAVE_DEBOUNCE_MS,
        backup_interval_sec: float = BACKUP_INTERVAL_SEC,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.todos_file = Path(todos_file)
        self.backups_dir = Path(backups_dir)
        self._days: dict[str, list[Todo]] = {}
        self.current_day: str = today_str()
        self._archive: list[Todo] = []  # 보관한 순서대로
        self._undo: list[_UndoBatch] = []
        self._dirty = False
        self._autosave = autosave
        self._backup_interval = backup_interval_sec
        self._last_backup: float | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.flush)

    # ------------------------------------------------------------ 페이지

    @property
    def _todos(self) -> list[Todo]:
        """지금 보고 있는 날짜 페이지의 목록 (원본 참조)."""
        return self._days.setdefault(self.current_day, [])

    def set_day(self, day: str | date) -> None:
        day = day.isoformat() if isinstance(day, date) else day
        date.fromisoformat(day)  # 형식 검사
        if day != self.current_day:
            self.current_day = day
            self.changed.emit()

    def days(self) -> list[str]:
        """할 일이 있는 날짜들 (오름차순)."""
        return sorted(d for d, items in self._days.items() if items)

    # ------------------------------------------------------------ 읽기

    def __len__(self) -> int:
        return sum(len(items) for items in self._days.values())

    def todos(self, day: str | None = None) -> list[Todo]:
        """한 페이지(기본: 지금 보는 날짜)를 순서대로, 복사본으로."""
        items = self._days.get(day or self.current_day, [])
        return [replace(t) for t in items]

    def all_todos(self) -> list[Todo]:
        """모든 페이지를 (날짜, 순서)대로."""
        return [replace(t) for d in sorted(self._days) for t in self._days[d]]

    def archived(self) -> list[Todo]:
        """보관함 (보관한 순서대로) 복사본."""
        return [replace(t) for t in self._archive]

    def get(self, todo_id: str) -> Todo | None:
        loc = self._find(todo_id)
        if loc is not None:
            return replace(self._days[loc[0]][loc[1]])
        a = self._find_archived(todo_id)
        return replace(self._archive[a]) if a is not None else None

    def counts(self, day: str | None = None) -> dict[Status, int]:
        """day를 주면 그 페이지만, 없으면 모든 페이지."""
        items = self._days.get(day, []) if day else [t for v in self._days.values() for t in v]
        done = sum(t.done for t in items)
        return {
            Status.ALL: len(items),
            Status.ACTIVE: len(items) - done,
            Status.DONE: done,
            Status.ARCHIVED: len(self._archive),
        }

    def query(
        self,
        text: str = "",
        status: Status | str = Status.ALL,
        sort: SortKey | str = SortKey.MANUAL,
    ) -> list[Todo]:
        """모든 날짜 페이지(또는 보관함)에서 찾는다."""
        status = Status(status)
        sort = SortKey(sort)
        needle = text.strip().casefold()
        if status is Status.ARCHIVED:
            source = self._archive
        else:
            source = [t for d in sorted(self._days) for t in self._days[d]]
        # 시간은 초 단위라 같은 초에 추가된 항목은 목록 순서(= 추가 순서)로 가른다
        pos = {t.id: i for i, t in enumerate(source)}
        result = [
            replace(t)
            for t in source
            if (not needle or needle in t.text.casefold())
            and (status in (Status.ALL, Status.ARCHIVED) or t.done == (status is Status.DONE))
        ]
        if sort is SortKey.CREATED_DESC:
            result.sort(key=lambda t: (_ts(t.created_at), pos[t.id]), reverse=True)
        elif sort is SortKey.CREATED_ASC:
            result.sort(key=lambda t: (_ts(t.created_at), pos[t.id]))
        elif sort is SortKey.COMPLETED_DESC:
            # 완료 시간이 없는 항목은 맨 뒤로
            result.sort(
                key=lambda t: (t.completed_at is not None, _ts(t.completed_at), pos[t.id]),
                reverse=True,
            )
        elif sort is SortKey.ALPHA:
            result.sort(key=lambda t: (t.text.casefold(), pos[t.id]))
        return result

    def has_children(self, todo_id: str) -> bool:
        day, i = self._require(todo_id)
        return self._block_end(self._days[day], i) > i + 1

    def overdue(self, today: str | None = None) -> list[Todo]:
        """오늘 전 페이지에 남은 안 끝난 할 일."""
        today = today or today_str()
        return [
            replace(t) for d in sorted(self._days) if d < today for t in self._days[d] if not t.done
        ]

    # ------------------------------------------------------------ 변경

    def add(self, text: str, indent: int = 0) -> Todo:
        """지금 보는 페이지 맨 아래에 추가."""
        text = _clean_text(text)
        if not text:
            raise ValueError("빈 할 일은 추가할 수 없음")
        todo = Todo(id=str(uuid.uuid4()), text=text, indent=max(0, indent), day=self.current_day)
        self._todos.append(todo)
        self._commit()
        return replace(todo)

    def add_many(self, items: Iterable[tuple[str, bool, int]]) -> int:
        """(text, done, indent) 묶음을 지금 페이지 맨 뒤에 추가. 마크다운 불러오기용."""
        added = 0
        for text, done, indent in items:
            text = _clean_text(text)
            if not text:
                continue
            ts = now_iso()
            self._todos.append(
                Todo(
                    id=str(uuid.uuid4()),
                    text=text,
                    done=done,
                    created_at=ts,
                    completed_at=ts if done else None,
                    indent=max(0, indent),
                    day=self.current_day,
                )
            )
            added += 1
        if added:
            self._commit()
        return added

    def update_text(self, todo_id: str, text: str) -> None:
        text = _clean_text(text)
        if not text:
            raise ValueError("빈 할 일은 저장할 수 없음")
        t = self._at(todo_id)
        if t.text != text:
            t.text = text
            self._commit()

    def set_done(self, todo_id: str, done: bool) -> None:
        """그 항목만 완료/해제한다 (하위 항목은 그대로)."""
        self.set_done_many([todo_id], done)

    def set_done_many(self, ids: Iterable[str], done: bool) -> None:
        ts = now_iso()
        touched = False
        for tid in ids:
            t = self._at(tid)
            if t.done != done:
                t.done = done
                t.completed_at = ts if done else None
                touched = True
        if touched:
            self._commit()

    def indent(self, todo_id: str) -> bool:
        """하위 항목까지 한 칸 들여쓰기. 바로 위 항목보다 2칸 이상 깊어질 수는 없다."""
        day, i = self._require(todo_id)
        items = self._days[day]
        if i == 0 or items[i].indent + 1 > items[i - 1].indent + 1:
            return False
        for t in items[i : self._block_end(items, i)]:
            t.indent += 1
        self._commit()
        return True

    def outdent(self, todo_id: str) -> bool:
        day, i = self._require(todo_id)
        items = self._days[day]
        if items[i].indent == 0:
            return False
        for t in items[i : self._block_end(items, i)]:
            t.indent -= 1
        self._commit()
        return True

    def move(
        self,
        todo_id: str,
        before_id: str | None = None,
        indent: int | None = None,
        day: str | None = None,
    ) -> None:
        """하위 항목까지 묶어서 before_id 앞으로 옮긴다.

        before_id가 None이면 day 페이지(없으면 원래 페이지) 맨 끝으로.
        before_id가 다른 날짜에 있으면 그 날짜로 옮겨진다 (주간 보기에서 다른 날로 끌기).
        indent를 주면 옮긴 묶음의 최상위 항목 깊이를 그 값으로 맞춘다 (가능한 범위로 자름).
        """
        src, i = self._require(todo_id)
        if day is not None:
            date.fromisoformat(day)
        items = self._days[src]
        end = self._block_end(items, i)
        block = items[i:end]
        if before_id is not None and before_id != todo_id and any(t.id == before_id for t in block):
            raise ValueError("자기 하위 항목 안으로는 옮길 수 없음")
        del items[i:end]
        if before_id is None:
            target = day or src
            items = self._days.setdefault(target, [])
            pos = len(items)
        elif before_id == todo_id:  # 제자리 (깊이만 바꿀 때)
            target, pos = src, i
        else:
            target, pos = self._require(before_id)
            items = self._days[target]
        for t in block:
            t.day = target
        max_indent = items[pos - 1].indent + 1 if pos > 0 else 0
        target = block[0].indent if indent is None else indent
        target = max(0, min(target, max_indent))
        delta = target - block[0].indent
        for t in block:
            t.indent += delta
        items[pos:pos] = block
        self._commit()

    def delete(self, ids: Iterable[str]) -> int:
        """하위 항목까지 같이 지운다. 보관함 항목이면 보관함에서 지운다.

        되돌리기 가능. 지운 개수를 돌려준다.
        """
        picked: dict[str, set[int]] = {}
        archive_idx: set[int] = set()
        for tid in ids:
            loc = self._find(tid)
            if loc is not None:
                day, i = loc
                picked.setdefault(day, set()).update(range(i, self._block_end(self._days[day], i)))
                continue
            a = self._find_archived(tid)
            if a is not None:
                archive_idx.add(a)
        return self._take(picked, archive_idx=archive_idx)

    def archive(self, ids: Iterable[str]) -> int:
        """고른 항목을 하위 항목까지 묶어서 보관함으로 옮긴다 (완료 여부 무관). 되돌리기 가능."""
        picked: dict[str, set[int]] = {}
        for tid in ids:
            loc = self._find(tid)
            if loc is not None:
                day, i = loc
                picked.setdefault(day, set()).update(range(i, self._block_end(self._days[day], i)))
        return self._take(picked, to_archive=True)

    def archive_completed(self, before: date | None = None, day: str | None = None) -> int:
        """완료 항목을 보관함으로 옮긴다. 되돌리기 가능.

        - day를 주면 그 페이지에서만 (노트 메뉴 "완료 항목 보관").
        - before를 주면 그 날짜 전(어제까지)에 완료한 것만 (날짜 바뀔 때 자동 정리).
        하위 항목은 완료가 아니면 페이지에 남기고 위로 당긴다.
        """
        picked: dict[str, set[int]] = {}
        for d, items in self._days.items():
            if day is not None and d != day:
                continue
            idx = {
                i
                for i, t in enumerate(items)
                if t.done and (before is None or (_local_date(t.completed_at) or before) < before)
            }
            if idx:
                picked[d] = idx
        return self._take(picked, to_archive=True)

    def restore(self, ids: Iterable[str], day: str | None = None) -> int:
        """보관함 항목을 페이지(기본: 오늘) 맨 아래로 되돌린다."""
        wanted = set(ids)
        back = [t for t in self._archive if t.id in wanted]
        if not back:
            return 0
        target = day or today_str()
        self._archive = [t for t in self._archive if t.id not in wanted]
        for t in back:
            t.archived_at = None
            t.indent = 0
            t.day = target
        self._days.setdefault(target, []).extend(back)
        self._commit()
        return len(back)

    def move_to_day(self, ids: Iterable[str], day: str) -> int:
        """고른 항목을 하위 항목까지 묶어서 다른 날짜 페이지 맨 아래로 옮긴다."""
        date.fromisoformat(day)
        blocks: list[Todo] = []
        for tid in ids:
            loc = self._find(tid)
            if loc is None or loc[0] == day:
                continue
            src, i = loc
            items = self._days[src]
            end = self._block_end(items, i)
            block = items[i:end]
            del items[i:end]
            base = block[0].indent
            for t in block:
                t.indent -= base
                t.day = day
            blocks += block
        if not blocks:
            return 0
        self._days.setdefault(day, []).extend(blocks)
        self._commit()
        return len(blocks)

    def bring_overdue(self, today: str | None = None) -> int:
        """지난 페이지의 안 끝난 할 일을 오늘 페이지 맨 위로 가져온다 (순서·계층 유지)."""
        today = today or today_str()
        moved: list[Todo] = []
        for d in sorted(self._days):
            if d >= today:
                continue
            keep = [t for t in self._days[d] if t.done]
            moved += [t for t in self._days[d] if not t.done]
            self._days[d] = keep
        if not moved:
            return 0
        for t in moved:
            t.day = today
        target = self._days.setdefault(today, [])
        target[0:0] = moved
        self._commit()
        return len(moved)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo_delete(self) -> int:
        """마지막 삭제 또는 보관을 되돌린다."""
        if not self._undo:
            return 0
        batch = self._undo.pop()
        if batch.archived:
            moved = {t.id for _, t in batch.items}
            self._archive = [t for t in self._archive if t.id not in moved]
        for pos, todo in sorted(batch.items, key=lambda p: p[0]):
            todo.archived_at = None
            items = self._days.setdefault(todo.day, [])
            items.insert(min(pos, len(items)), todo)
        for items in self._days.values():
            for t in items:
                if t.id in batch.indents:
                    t.indent = batch.indents[t.id]
        for pos, todo in sorted(batch.archive_items, key=lambda p: p[0]):
            self._archive.insert(min(pos, len(self._archive)), todo)
        self._commit()
        return len(batch.items) + len(batch.archive_items)

    # ------------------------------------------------------------ 내부

    def _find(self, todo_id: str) -> tuple[str, int] | None:
        for day, items in self._days.items():
            for i, t in enumerate(items):
                if t.id == todo_id:
                    return day, i
        return None

    def _find_archived(self, todo_id: str) -> int | None:
        for i, t in enumerate(self._archive):
            if t.id == todo_id:
                return i
        return None

    def _require(self, todo_id: str) -> tuple[str, int]:
        loc = self._find(todo_id)
        if loc is None:
            raise KeyError(todo_id)
        return loc

    def _at(self, todo_id: str) -> Todo:
        day, i = self._require(todo_id)
        return self._days[day][i]

    @staticmethod
    def _block_end(items: list[Todo], i: int) -> int:
        base = items[i].indent
        j = i + 1
        while j < len(items) and items[j].indent > base:
            j += 1
        return j

    def _take(
        self,
        picked: dict[str, set[int]],
        *,
        to_archive: bool = False,
        archive_idx: set[int] | None = None,
    ) -> int:
        """페이지들(picked: 날짜 → 위치들)·보관함(archive_idx)에서 빼고 되돌리기 기록을 남긴다."""
        archive_idx = archive_idx or set()
        if not any(picked.values()) and not archive_idx:
            return 0
        removed: list[tuple[int, Todo]] = []
        indents: dict[str, int] = {}
        for day, idx in picked.items():
            items = self._days[day]
            indents.update({t.id: t.indent for t in items})
            removed += [(i, items[i]) for i in sorted(idx)]
            self._days[day] = [t for i, t in enumerate(items) if i not in idx]
        from_archive = [(i, self._archive[i]) for i in sorted(archive_idx)]
        self._archive = [t for i, t in enumerate(self._archive) if i not in archive_idx]
        if to_archive:
            ts = now_iso()
            for _, t in removed:
                t.archived_at = ts
                self._archive.append(t)
        self._undo.append(_UndoBatch(removed, indents, to_archive, from_archive))
        del self._undo[:-MAX_UNDO]
        self._commit()
        return len(removed) + len(from_archive)

    def _commit(self) -> None:
        for items in self._days.values():
            normalize(items)
        self._dirty = True
        if self._autosave:
            self._timer.start()
        self.changed.emit()

    # ------------------------------------------------------------ 저장/로드

    def _set_data(self, todos: list[Todo], archive: list[Todo]) -> None:
        self._days = _group_by_day(todos)
        self._archive = archive

    def load(self) -> LoadResult:
        """todos.json을 읽는다. 손상됐으면 백업에서 복구한다.

        더 새 버전 파일이면 UnsupportedVersionError (파일은 건드리지 않음).
        """
        self._timer.stop()
        self._undo.clear()
        self._dirty = False
        if not self.todos_file.exists():
            self._set_data([], [])
            self.changed.emit()
            return LoadResult(LoadStatus.NEW)
        try:
            self._set_data(*read_todos_file(self.todos_file))
            self.changed.emit()
            return LoadResult(LoadStatus.OK)
        except DataError as err:
            reason = str(err)

        # 손상: 원본은 따로 보관해 두고 백업을 찾는다
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt = self.todos_file.with_name(f"{self.todos_file.name}.corrupt-{stamp}")
        shutil.copy2(self.todos_file, corrupt)
        for backup in self.list_backups():
            try:
                data = read_todos_file(backup)
            except DataError:
                continue
            self._set_data(*data)
            self._save(make_backup=False)
            self.changed.emit()
            return LoadResult(
                LoadStatus.RECOVERED,
                f"할 일 파일이 손상돼서 백업({backup.name})에서 복구했어.",
                backup_used=backup,
                corrupt_copy=corrupt,
            )
        self._set_data([], [])
        self._save(make_backup=False)
        self.changed.emit()
        return LoadResult(
            LoadStatus.RESET,
            f"할 일 파일이 손상됐고 쓸 수 있는 백업이 없어. 손상된 파일은 {corrupt.name}로 "
            f"보관했어. ({reason})",
            corrupt_copy=corrupt,
        )

    def flush(self) -> bool:
        """미뤄둔 저장을 바로 한다. 성공하면 True."""
        self._timer.stop()
        if not self._dirty:
            return True
        try:
            self._save(make_backup=True)
        except OSError as e:
            self.save_failed.emit(str(e))
            return False
        return True

    def list_backups(self) -> list[Path]:
        """최신순."""
        if not self.backups_dir.is_dir():
            return []
        return sorted(self.backups_dir.glob("todos-*.json"), reverse=True)

    def _save(self, *, make_backup: bool) -> None:
        if make_backup:
            self._maybe_backup()
        for items in self._days.values():
            normalize(items)
        payload = {
            "version": CURRENT_VERSION,
            "todos": [t.to_dict() for d in sorted(self._days) for t in self._days[d]],
            "archive": [t.to_dict() for t in self._archive],
        }
        atomic_write_text(self.todos_file, json.dumps(payload, ensure_ascii=False, indent=2))
        self._dirty = False

    def _maybe_backup(self) -> None:
        now = time.monotonic()
        if self._last_backup is not None and now - self._last_backup < self._backup_interval:
            return
        if not self.todos_file.exists():
            return
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        # 순번을 항상 붙여서 같은 시각에 여러 번 저장해도 이름순 = 시간순이 되게.
        # 빈 번호를 다시 쓰면 (오래된 백업을 지운 뒤) 새 백업이 가장 오래된 것처럼 정렬되므로
        # 같은 시각에서 지금까지 쓴 가장 큰 번호 + 1을 쓴다.
        used = [
            int(f.stem.rsplit("-", 1)[1])
            for f in self.backups_dir.glob(f"todos-{stamp}-*.json")
            if f.stem.rsplit("-", 1)[1].isdigit()
        ]
        n = max(used, default=-1) + 1
        target = self.backups_dir / f"todos-{stamp}-{n:03d}.json"
        shutil.copy2(self.todos_file, target)
        self._last_backup = now
        for old in self.list_backups()[MAX_BACKUPS:]:
            old.unlink(missing_ok=True)
