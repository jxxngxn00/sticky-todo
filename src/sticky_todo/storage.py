"""할 일 데이터의 유일한 소유자 TodoStore.

- 변경할 때마다 ``changed`` 시그널을 보내고, 300ms 디바운스 후 자동 저장한다.
- 할 일은 화면 순서대로 평평한 리스트로 들고 있고, 각 항목의 ``indent``(0, 1, 2…)로
  계층을 표현한다. 규칙: 첫 항목은 0, 각 항목은 바로 앞 항목 indent + 1 이하.
- 어떤 항목의 '하위 항목'은 바로 뒤에 이어지는, indent가 더 큰 항목들이다.
  이동·삭제·들여쓰기는 하위 항목까지 묶어서 처리하고, 완료 체크는 그 항목만 바꾼다.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .fileio import atomic_write_text

CURRENT_VERSION = 1
SAVE_DEBOUNCE_MS = 300
MAX_BACKUPS = 5
BACKUP_INTERVAL_SEC = 600  # 같은 세션에서 백업은 최대 10분에 한 번
MAX_UNDO = 20


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(text: str) -> str:
    return " ".join(str(text).split())


@dataclass
class Todo:
    id: str
    text: str
    done: bool = False
    created_at: str = field(default_factory=now_iso)
    completed_at: str | None = None
    order: int = 0
    indent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "done": self.done,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "order": self.order,
            "indent": self.indent,
        }


# ---------------------------------------------------------------- 로드/검증


class DataError(Exception):
    """todos.json 내용이 손상되었거나 형식이 맞지 않음."""


class UnsupportedVersionError(Exception):
    """앱보다 새 버전에서 저장한 파일. 덮어쓰면 데이터가 손실될 수 있어 거부한다."""


# from_version -> 다음 버전 dict로 변환하는 함수
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


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


def _int_or(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def parse_todos(data: Any) -> list[Todo]:
    """JSON 객체를 검증해서 Todo 리스트로. 전체 구조가 틀리면 DataError.

    개별 항목의 사소한 문제(시간 형식 등)는 기본값으로 고치고,
    text가 없는 항목은 건너뛴다.
    """
    if not isinstance(data, dict):
        raise DataError("최상위가 객체가 아님")
    data = migrate(data)
    raw = data.get("todos")
    if not isinstance(raw, list):
        raise DataError("todos가 리스트가 아님")

    todos: list[Todo] = []
    seen: set[str] = set()
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
        completed = item.get("completed_at")
        todos.append(
            Todo(
                id=tid,
                text=text,
                done=done,
                created_at=created if _valid_iso(created) else now_iso(),
                completed_at=completed if done and _valid_iso(completed) else None,
                order=_int_or(item.get("order"), i),
                indent=max(0, _int_or(item.get("indent"), 0)),
            )
        )
    # 원래 리스트 위치를 2차 키로 써서 order가 같아도 순서가 안정적이게
    todos = [t for _, t in sorted(enumerate(todos), key=lambda p: (p[1].order, p[0]))]
    normalize(todos)
    return todos


def normalize(todos: list[Todo]) -> None:
    """indent 규칙을 맞추고 order를 0..n-1로 다시 매긴다."""
    prev = -1
    for i, t in enumerate(todos):
        t.indent = max(0, min(t.indent, prev + 1))
        prev = t.indent
        t.order = i


def read_todos_file(path: Path) -> list[Todo]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DataError(str(e)) from e
    return parse_todos(data)


# ---------------------------------------------------------------- 검색·정렬


class Status(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    DONE = "done"


class SortKey(StrEnum):
    CREATED_DESC = "created_desc"  # 최근 추가순
    CREATED_ASC = "created_asc"  # 오래된순
    COMPLETED_DESC = "completed_desc"  # 최근 완료순
    ALPHA = "alpha"  # 가나다순
    MANUAL = "manual"  # 노트 순서


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
class _DeleteBatch:
    items: list[tuple[int, Todo]]  # (원래 위치, 항목)
    indents: dict[str, int]  # 지우기 직전 모든 항목의 indent (되돌릴 때 계층 복원용)


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
        self._todos: list[Todo] = []
        self._undo: list[_DeleteBatch] = []
        self._dirty = False
        self._autosave = autosave
        self._backup_interval = backup_interval_sec
        self._last_backup: float | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.flush)

    # ------------------------------------------------------------ 읽기

    def __len__(self) -> int:
        return len(self._todos)

    def todos(self) -> list[Todo]:
        """노트 순서대로 복사본을 돌려준다."""
        return [replace(t) for t in self._todos]

    def get(self, todo_id: str) -> Todo | None:
        i = self._find(todo_id)
        return replace(self._todos[i]) if i is not None else None

    def counts(self) -> dict[Status, int]:
        done = sum(t.done for t in self._todos)
        return {
            Status.ALL: len(self._todos),
            Status.ACTIVE: len(self._todos) - done,
            Status.DONE: done,
        }

    def query(
        self,
        text: str = "",
        status: Status | str = Status.ALL,
        sort: SortKey | str = SortKey.MANUAL,
    ) -> list[Todo]:
        status = Status(status)
        sort = SortKey(sort)
        needle = text.strip().casefold()
        result = [
            replace(t)
            for t in self._todos
            if (not needle or needle in t.text.casefold())
            and (status is Status.ALL or t.done == (status is Status.DONE))
        ]
        if sort is SortKey.CREATED_DESC:
            result.sort(key=lambda t: _ts(t.created_at), reverse=True)
        elif sort is SortKey.CREATED_ASC:
            result.sort(key=lambda t: _ts(t.created_at))
        elif sort is SortKey.COMPLETED_DESC:
            # 완료 시간이 없는 항목은 맨 뒤로
            result.sort(
                key=lambda t: (t.completed_at is not None, _ts(t.completed_at)), reverse=True
            )
        elif sort is SortKey.ALPHA:
            result.sort(key=lambda t: t.text.casefold())
        return result

    def has_children(self, todo_id: str) -> bool:
        i = self._require(todo_id)
        return self._block_end(i) > i + 1

    # ------------------------------------------------------------ 변경

    def add(self, text: str, indent: int = 0) -> Todo:
        text = _clean_text(text)
        if not text:
            raise ValueError("빈 할 일은 추가할 수 없음")
        todo = Todo(id=str(uuid.uuid4()), text=text, indent=max(0, indent))
        self._todos.append(todo)
        self._commit()
        return replace(todo)

    def add_many(self, items: Iterable[tuple[str, bool, int]]) -> int:
        """(text, done, indent) 묶음을 맨 뒤에 추가. 마크다운 불러오기용."""
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
        t = self._todos[self._require(todo_id)]
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
            t = self._todos[self._require(tid)]
            if t.done != done:
                t.done = done
                t.completed_at = ts if done else None
                touched = True
        if touched:
            self._commit()

    def indent(self, todo_id: str) -> bool:
        """하위 항목까지 한 칸 들여쓰기. 바로 위 항목보다 2칸 이상 깊어질 수는 없다."""
        i = self._require(todo_id)
        if i == 0 or self._todos[i].indent + 1 > self._todos[i - 1].indent + 1:
            return False
        for t in self._todos[i : self._block_end(i)]:
            t.indent += 1
        self._commit()
        return True

    def outdent(self, todo_id: str) -> bool:
        i = self._require(todo_id)
        if self._todos[i].indent == 0:
            return False
        for t in self._todos[i : self._block_end(i)]:
            t.indent -= 1
        self._commit()
        return True

    def move(self, todo_id: str, before_id: str | None = None, indent: int | None = None) -> None:
        """하위 항목까지 묶어서 before_id 앞으로 옮긴다 (None이면 맨 끝).

        indent를 주면 옮긴 묶음의 최상위 항목 깊이를 그 값으로 맞춘다 (가능한 범위로 자름).
        """
        i = self._require(todo_id)
        end = self._block_end(i)
        block = self._todos[i:end]
        if before_id is not None and before_id != todo_id and any(t.id == before_id for t in block):
            raise ValueError("자기 하위 항목 안으로는 옮길 수 없음")
        del self._todos[i:end]
        if before_id is None:
            pos = len(self._todos)
        elif before_id == todo_id:  # 제자리 (깊이만 바꿀 때)
            pos = i
        else:
            pos = self._require(before_id)
        max_indent = self._todos[pos - 1].indent + 1 if pos > 0 else 0
        target = block[0].indent if indent is None else indent
        target = max(0, min(target, max_indent))
        delta = target - block[0].indent
        for t in block:
            t.indent += delta
        self._todos[pos:pos] = block
        self._commit()

    def delete(self, ids: Iterable[str]) -> int:
        """하위 항목까지 같이 지운다. 되돌리기 가능. 지운 개수를 돌려준다."""
        indices: set[int] = set()
        for tid in ids:
            i = self._find(tid)
            if i is not None:
                indices.update(range(i, self._block_end(i)))
        return self._remove_indices(indices)

    def clear_completed(self) -> int:
        """완료 항목만 지운다. 하위 항목은 완료가 아니면 남기고 위로 당긴다."""
        return self._remove_indices({i for i, t in enumerate(self._todos) if t.done})

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo_delete(self) -> int:
        if not self._undo:
            return 0
        batch = self._undo.pop()
        for pos, todo in sorted(batch.items, key=lambda p: p[0]):
            self._todos.insert(min(pos, len(self._todos)), todo)
        for t in self._todos:
            if t.id in batch.indents:
                t.indent = batch.indents[t.id]
        self._commit()
        return len(batch.items)

    # ------------------------------------------------------------ 내부

    def _find(self, todo_id: str) -> int | None:
        for i, t in enumerate(self._todos):
            if t.id == todo_id:
                return i
        return None

    def _require(self, todo_id: str) -> int:
        i = self._find(todo_id)
        if i is None:
            raise KeyError(todo_id)
        return i

    def _block_end(self, i: int) -> int:
        base = self._todos[i].indent
        j = i + 1
        while j < len(self._todos) and self._todos[j].indent > base:
            j += 1
        return j

    def _remove_indices(self, indices: set[int]) -> int:
        if not indices:
            return 0
        removed = [(i, self._todos[i]) for i in sorted(indices)]
        indents = {t.id: t.indent for t in self._todos}
        self._todos = [t for i, t in enumerate(self._todos) if i not in indices]
        self._undo.append(_DeleteBatch(removed, indents))
        del self._undo[:-MAX_UNDO]
        self._commit()
        return len(removed)

    def _commit(self) -> None:
        normalize(self._todos)
        self._dirty = True
        if self._autosave:
            self._timer.start()
        self.changed.emit()

    # ------------------------------------------------------------ 저장/로드

    def load(self) -> LoadResult:
        """todos.json을 읽는다. 손상됐으면 백업에서 복구한다.

        더 새 버전 파일이면 UnsupportedVersionError (파일은 건드리지 않음).
        """
        self._timer.stop()
        self._undo.clear()
        self._dirty = False
        if not self.todos_file.exists():
            self._todos = []
            self.changed.emit()
            return LoadResult(LoadStatus.NEW)
        try:
            self._todos = read_todos_file(self.todos_file)
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
                todos = read_todos_file(backup)
            except DataError:
                continue
            self._todos = todos
            self._save(make_backup=False)
            self.changed.emit()
            return LoadResult(
                LoadStatus.RECOVERED,
                f"할 일 파일이 손상돼서 백업({backup.name})에서 복구했어.",
                backup_used=backup,
                corrupt_copy=corrupt,
            )
        self._todos = []
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
        normalize(self._todos)
        payload = {"version": CURRENT_VERSION, "todos": [t.to_dict() for t in self._todos]}
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
        shutil.copy2(self.todos_file, self.backups_dir / f"todos-{stamp}.json")
        self._last_backup = now
        for old in self.list_backups()[MAX_BACKUPS:]:
            old.unlink(missing_ok=True)
