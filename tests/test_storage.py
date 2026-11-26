import json
from datetime import datetime

import pytest
from conftest import tree
from PySide6.QtTest import QTest

from sticky_todo import storage
from sticky_todo.storage import (
    CURRENT_VERSION,
    DataError,
    LoadStatus,
    SortKey,
    Status,
    UnsupportedVersionError,
    parse_todos,
    today_str,
)


def build(store, spec):
    """spec: [("A", 0), ("B", 1), ...] → id 딕셔너리."""
    ids = {}
    for text, indent in spec:
        ids[text] = store.add(text, indent).id
    return ids


# ---------------------------------------------------------------- 기본 CRUD


def test_add_and_changed_signal(store):
    hits = []
    store.changed.connect(lambda: hits.append(1))
    t = store.add("  PR   리뷰 2건 ")
    assert t.text == "PR 리뷰 2건"
    assert not t.done and t.completed_at is None
    datetime.fromisoformat(t.created_at)  # 타임존 포함 ISO
    assert datetime.fromisoformat(t.created_at).tzinfo is not None
    assert hits == [1]


def test_add_empty_rejected(store):
    with pytest.raises(ValueError):
        store.add("   ")


def test_update_text_and_done(store):
    t = store.add("a")
    store.update_text(t.id, "b")
    store.set_done(t.id, True)
    got = store.get(t.id)
    assert got.text == "b" and got.done and got.completed_at
    store.set_done(t.id, False)
    assert store.get(t.id).completed_at is None


def test_returned_objects_are_copies(store):
    t = store.add("a")
    store.todos()[0].text = "hacked"
    t.text = "hacked"
    assert store.get(t.id).text == "a"


# ---------------------------------------------------------------- 들여쓰기


def test_indent_is_clamped_on_add(store):
    build(store, [("A", 3), ("B", 5)])
    assert tree(store) == [("A", 0), ("B", 1)]


def test_indent_unlimited_depth(store):
    build(store, [(str(i), i) for i in range(10)])
    assert [d for _, d in tree(store)] == list(range(10))


def test_indent_and_outdent_move_subtree(store):
    ids = build(store, [("A", 0), ("B", 0), ("B1", 1), ("C", 0)])
    assert store.indent(ids["B"])
    assert tree(store) == [("A", 0), ("B", 1), ("B1", 2), ("C", 0)]
    assert store.outdent(ids["B"])
    assert tree(store) == [("A", 0), ("B", 0), ("B1", 1), ("C", 0)]


def test_indent_limits(store):
    ids = build(store, [("A", 0), ("B", 1)])
    assert not store.indent(ids["A"])  # 첫 항목
    assert not store.indent(ids["B"])  # 앞 항목보다 2칸 깊어짐
    assert not store.outdent(ids["A"])


def test_done_does_not_cascade(store):
    ids = build(store, [("P", 0), ("C", 1)])
    store.set_done(ids["P"], True)
    assert store.get(ids["C"]).done is False


# ---------------------------------------------------------------- 이동


def test_move_carries_children(store):
    ids = build(store, [("A", 0), ("A1", 1), ("B", 0)])
    store.move(ids["A"], before_id=None)
    assert tree(store) == [("B", 0), ("A", 0), ("A1", 1)]


def test_move_before(store):
    ids = build(store, [("A", 0), ("B", 0), ("C", 0)])
    store.move(ids["C"], before_id=ids["A"])
    assert [t for t, _ in tree(store)] == ["C", "A", "B"]


def test_move_with_indent_becomes_child(store):
    ids = build(store, [("A", 0), ("B", 0), ("B1", 1)])
    store.move(ids["B"], before_id=None, indent=1)
    assert tree(store) == [("A", 0), ("B", 1), ("B1", 2)]


def test_move_into_own_subtree_rejected(store):
    ids = build(store, [("A", 0), ("A1", 1)])
    with pytest.raises(ValueError):
        store.move(ids["A"], before_id=ids["A1"])


def test_move_in_place_changes_depth(store):
    ids = build(store, [("A", 0), ("B", 0)])
    store.move(ids["B"], before_id=ids["B"], indent=1)
    assert tree(store) == [("A", 0), ("B", 1)]


def test_move_keeps_following_items_valid(store):
    ids = build(store, [("A", 0), ("B", 1), ("C", 2), ("X", 0)])
    store.move(ids["X"], before_id=ids["C"], indent=0)
    # C는 X 뒤에 오므로 X보다 2칸 깊을 수 없음
    assert tree(store) == [("A", 0), ("B", 1), ("X", 0), ("C", 1)]


# ---------------------------------------------------------------- 삭제/되돌리기


def test_delete_cascades_and_undo(store):
    ids = build(store, [("A", 0), ("P", 0), ("P1", 1), ("P11", 2), ("B", 0)])
    assert store.delete([ids["P"]]) == 3
    assert tree(store) == [("A", 0), ("B", 0)]
    assert store.undo_delete() == 3
    assert tree(store) == [("A", 0), ("P", 0), ("P1", 1), ("P11", 2), ("B", 0)]
    assert store.get(ids["P1"]).id == ids["P1"]  # id 유지


def test_delete_multiple_overlapping(store):
    ids = build(store, [("P", 0), ("C", 1), ("D", 0)])
    assert store.delete([ids["P"], ids["C"], "없는id"]) == 2
    assert tree(store) == [("D", 0)]


def test_archive_completed_keeps_children_and_undo_restores_depth(store):
    ids = build(store, [("A", 0), ("P", 1), ("C", 2), ("D", 1)])
    store.set_done(ids["P"], True)
    assert store.archive_completed() == 1
    assert tree(store) == [("A", 0), ("C", 1), ("D", 1)]
    store.undo_delete()
    assert tree(store) == [("A", 0), ("P", 1), ("C", 2), ("D", 1)]


def test_undo_stack(store):
    ids = build(store, [("A", 0), ("B", 0)])
    store.delete([ids["A"]])
    store.delete([ids["B"]])
    assert store.undo_delete() == 1
    assert [t for t, _ in tree(store)] == ["B"]
    assert store.undo_delete() == 1
    assert [t for t, _ in tree(store)] == ["A", "B"]
    assert not store.can_undo() and store.undo_delete() == 0


# ---------------------------------------------------------------- 검색·정렬


def _set_times(store, values):
    for t, (created, completed) in zip(store._todos, values, strict=True):
        t.created_at = created
        if completed:
            t.done, t.completed_at = True, completed


def test_query_filter_and_sort(store):
    build(store, [("다 PR", 0), ("가 회의", 0), ("나 pr 리뷰", 1)])
    _set_times(
        store,
        [
            ("2026-11-20T09:00:00+09:00", None),
            ("2026-11-22T09:00:00+09:00", "2026-11-23T09:00:00+09:00"),
            ("2026-11-21T09:00:00+09:00", "2026-11-24T09:00:00+09:00"),
        ],
    )
    texts = lambda ts: [t.text for t in ts]  # noqa: E731
    assert texts(store.query("pr")) == ["다 PR", "나 pr 리뷰"]  # 대소문자 무시
    assert texts(store.query(status=Status.DONE)) == ["가 회의", "나 pr 리뷰"]
    assert texts(store.query(status="active")) == ["다 PR"]
    assert texts(store.query(sort=SortKey.CREATED_DESC)) == ["가 회의", "나 pr 리뷰", "다 PR"]
    assert texts(store.query(sort=SortKey.CREATED_ASC)) == ["다 PR", "나 pr 리뷰", "가 회의"]
    assert texts(store.query(sort=SortKey.COMPLETED_DESC)) == ["나 pr 리뷰", "가 회의", "다 PR"]
    assert texts(store.query(sort=SortKey.ALPHA)) == ["가 회의", "나 pr 리뷰", "다 PR"]
    assert store.counts() == {Status.ALL: 3, Status.ACTIVE: 1, Status.DONE: 2, Status.ARCHIVED: 0}


# ---------------------------------------------------------------- 저장/로드


def test_save_and_reload_roundtrip(make_store):
    s1 = make_store()
    ids = build(s1, [("A", 0), ("A1", 1)])
    s1.set_done(ids["A1"], True)
    assert s1.flush()
    data = json.loads(s1.todos_file.read_text(encoding="utf-8"))
    assert data["version"] == CURRENT_VERSION
    assert [t["order"] for t in data["todos"]] == [0, 1]

    s2 = make_store()
    assert s2.load().status is LoadStatus.OK
    assert s2.todos() == s1.todos()


def test_debounced_autosave(make_store):
    s = make_store(autosave=True, debounce_ms=50)
    s.add("a")
    s.add("b")
    assert not s.todos_file.exists()  # 아직 디바운스 중
    for _ in range(100):  # 느린 CI에서도 되게, 저장될 때까지 최대 5초
        QTest.qWait(50)
        if s.todos_file.exists():
            break
    assert len(json.loads(s.todos_file.read_text(encoding="utf-8"))["todos"]) == 2


def test_load_missing_file(store):
    assert store.load().status is LoadStatus.NEW
    assert len(store) == 0


def test_atomic_write_leaves_no_temp_files(store, tmp_path):
    store.add("a")
    store.flush()
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_failed_write_keeps_original(store, monkeypatch):
    store.add("원본")
    store.flush()
    original = store.todos_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("디스크 꽉 참")

    monkeypatch.setattr("sticky_todo.fileio.os.replace", boom)
    failures = []
    store.save_failed.connect(failures.append)
    store.add("새것")
    assert store.flush() is False
    assert failures and "디스크" in failures[0]
    assert store.todos_file.read_text(encoding="utf-8") == original
    assert not list(store.todos_file.parent.glob("*.tmp"))


def test_backups_rotate_to_five(make_store):
    s = make_store(backup_interval_sec=0)
    for i in range(9):
        s.add(f"t{i}")
        s.flush()
    backups = s.list_backups()
    assert len(backups) == storage.MAX_BACKUPS
    # 가장 최신 백업 = 마지막 저장 직전 상태 (8개)
    newest = json.loads(backups[0].read_text(encoding="utf-8"))
    assert len(newest["todos"]) == 8


def test_backup_throttled_within_session(make_store):
    s = make_store()  # 기본 10분 간격
    for i in range(4):
        s.add(f"t{i}")
        s.flush()
    assert len(s.list_backups()) == 1


def test_corrupt_file_recovered_from_backup(make_store):
    s = make_store(backup_interval_sec=0)
    s.add("살아남을 항목")
    s.flush()
    s.add("두번째")
    s.flush()  # 이 시점에 백업 = 1개짜리 상태
    s.todos_file.write_text("{ 깨진 json", encoding="utf-8")

    s2 = make_store()
    result = s2.load()
    assert result.status is LoadStatus.RECOVERED
    assert "복구" in result.message
    assert [t.text for t in s2.todos()] == ["살아남을 항목"]
    assert result.corrupt_copy.read_text(encoding="utf-8") == "{ 깨진 json"
    # 복구한 내용이 todos.json에 바로 다시 써짐
    saved = json.loads(s2.todos_file.read_text(encoding="utf-8"))
    assert saved["todos"][0]["text"] == "살아남을 항목"


def test_corrupt_skips_bad_backups(make_store, tmp_path):
    s = make_store()
    (tmp_path / "backups").mkdir()
    good = {"version": 1, "todos": [{"text": "좋은 백업"}]}
    (tmp_path / "backups" / "todos-20260101-000000-000000.json").write_text(
        json.dumps(good), encoding="utf-8"
    )
    (tmp_path / "backups" / "todos-20260102-000000-000000.json").write_text(
        "garbage", encoding="utf-8"
    )
    s.todos_file.write_text("[]", encoding="utf-8")
    result = s.load()
    assert result.status is LoadStatus.RECOVERED
    assert result.backup_used.name.startswith("todos-20260101")


def test_corrupt_without_backup_resets(store):
    store.todos_file.write_text("null", encoding="utf-8")
    result = store.load()
    assert result.status is LoadStatus.RESET
    assert len(store) == 0
    assert result.corrupt_copy.exists()


def test_future_version_refused_and_file_untouched(store):
    raw = json.dumps({"version": 99, "todos": []})
    store.todos_file.write_text(raw, encoding="utf-8")
    with pytest.raises(UnsupportedVersionError):
        store.load()
    assert store.todos_file.read_text(encoding="utf-8") == raw


# ---------------------------------------------------------------- 검증/마이그레이션


def test_parse_tolerates_bad_items():
    data = {
        "version": 1,
        "todos": [
            {
                "id": "x",
                "text": "정상",
                "done": True,
                "created_at": "2026-11-24T10:12:00+09:00",
                "completed_at": "2026-11-24T11:00:00+09:00",
                "order": 2,
            },
            {"id": "x", "text": "id 중복", "created_at": "어제", "order": 1},
            {"text": ""},
            "문자열",
            {"text": 123},
            {"text": "indent 없음", "done": "yes", "indent": -3, "order": 0},
        ],
    }
    todos = parse_todos(data)
    assert [t.text for t in todos] == ["indent 없음", "id 중복", "정상"]
    assert todos[0].done is False and todos[0].indent == 0
    assert len({t.id for t in todos}) == 3
    datetime.fromisoformat(todos[1].created_at)


@pytest.mark.parametrize(
    "bad",
    [[], {"todos": []}, {"version": 1}, {"version": "1", "todos": []}, {"version": 0, "todos": []}],
)
def test_parse_rejects_bad_structure(bad):
    with pytest.raises(DataError):
        parse_todos(bad)


def test_migration_chain(monkeypatch):
    monkeypatch.setattr(storage, "CURRENT_VERSION", 3)

    def v1_to_v2(d):
        return {"version": 2, "items": d["todos"]}

    def v2_to_v3(d):
        return {"version": 3, "todos": [{**t, "text": t["text"].upper()} for t in d["items"]]}

    monkeypatch.setattr(storage, "MIGRATIONS", {1: v1_to_v2, 2: v2_to_v3})
    todos = parse_todos({"version": 1, "todos": [{"text": "abc"}]})
    assert todos[0].text == "ABC"


def test_missing_migration_is_error(monkeypatch):
    monkeypatch.setattr(storage, "CURRENT_VERSION", 2)
    monkeypatch.setattr(storage, "MIGRATIONS", {})
    with pytest.raises(DataError):
        parse_todos({"version": 1, "todos": []})


def test_sort_ties_use_add_order(store):
    for name in ["첫째", "둘째", "셋째"]:
        store.add(name)
    for t in store._todos:
        t.created_at = "2026-11-24T09:00:00+09:00"  # 같은 초
    assert [t.text for t in store.query(sort=SortKey.CREATED_DESC)] == ["셋째", "둘째", "첫째"]
    assert [t.text for t in store.query(sort=SortKey.CREATED_ASC)] == ["첫째", "둘째", "셋째"]


# ---------------------------------------------------------------- 보관함


def test_archive_restore_and_query(store):
    ids = build(store, [("A", 0), ("B", 0), ("C", 0)])
    store.set_done_many([ids["A"], ids["C"]], True)
    assert store.archive_completed() == 2
    assert [t.text for t in store.todos()] == ["B"]
    arch = store.archived()
    assert [t.text for t in arch] == ["A", "C"]
    assert all(t.archived_at and t.done for t in arch)
    assert store.counts()[Status.ARCHIVED] == 2
    assert [t.text for t in store.query(status=Status.ARCHIVED)] == ["A", "C"]
    assert [t.text for t in store.query("c", status=Status.ARCHIVED)] == ["C"]
    assert store.get(ids["A"]).archived_at  # 보관함에서도 찾음
    assert store.restore([ids["C"]]) == 1
    assert [t.text for t in store.todos()] == ["B", "C"]
    assert store.get(ids["C"]).archived_at is None


def test_archive_before_date_only_older(store):
    from datetime import date, datetime

    def local(*args):
        """이 PC 시간대 기준 시각 (CI 서버는 UTC라 +09:00을 박아두면 날짜가 어긋난다)."""
        return datetime(*args).astimezone().isoformat(timespec="seconds")

    ids = build(store, [("어제", 0), ("오늘", 0), ("미완", 0)])
    store.set_done_many([ids["어제"], ids["오늘"]], True)
    store._todos[0].completed_at = local(2026, 11, 23, 23, 59)
    store._todos[1].completed_at = local(2026, 11, 24, 0, 1)
    assert store.archive_completed(before=date(2026, 11, 24)) == 1
    assert [t.text for t in store.todos()] == ["오늘", "미완"]


def test_delete_from_archive_and_undo(store):
    ids = build(store, [("A", 0)])
    store.set_done(ids["A"], True)
    store.archive_completed()
    assert store.delete([ids["A"]]) == 1
    assert store.archived() == []
    assert store.undo_delete() == 1
    assert [t.text for t in store.archived()] == ["A"]


def test_undo_archive_moves_back(store):
    ids = build(store, [("P", 0), ("C", 1)])
    store.set_done(ids["P"], True)
    store.archive_completed()
    assert tree(store) == [("C", 0)]
    store.undo_delete()
    assert tree(store) == [("P", 0), ("C", 1)]
    assert store.archived() == []


def test_archive_saved_and_reloaded(make_store):
    s1 = make_store()
    t = s1.add("보관할 일")
    s1.set_done(t.id, True)
    s1.archive_completed()
    s1.add("남은 일")
    s1.flush()
    data = json.loads(s1.todos_file.read_text(encoding="utf-8"))
    assert data["version"] == CURRENT_VERSION
    assert [x["text"] for x in data["archive"]] == ["보관할 일"]
    s2 = make_store()
    s2.load()
    assert [x.text for x in s2.todos()] == ["남은 일"]
    assert [x.text for x in s2.archived()] == ["보관할 일"]


def test_v1_file_migrates_to_v2(store):
    v1 = {"version": 1, "todos": [{"id": "a", "text": "옛날 데이터", "indent": 0}]}
    store.todos_file.write_text(json.dumps(v1), encoding="utf-8")
    assert store.load().status is LoadStatus.OK
    assert [t.text for t in store.todos()] == ["옛날 데이터"]
    assert store.archived() == []


def test_archive_selected_with_children_and_undo(store):
    ids = build(store, [("P", 0), ("C", 1), ("Q", 0)])
    assert store.archive([ids["P"]]) == 2  # 미완료여도, 하위까지
    assert tree(store) == [("Q", 0)]
    assert [t.text for t in store.archived()] == ["P", "C"]
    store.undo_delete()
    assert tree(store) == [("P", 0), ("C", 1), ("Q", 0)]


# ---------------------------------------------------------------- 날짜 페이지


def test_pages_are_independent(store):
    store.add("오늘 A")
    store.set_day("2026-12-01")
    store.add("미래 B")
    store.add("미래 B1", 1)
    assert [t.text for t in store.todos()] == ["미래 B", "미래 B1"]
    assert [t.day for t in store.todos()] == ["2026-12-01"] * 2
    assert len(store) == 3
    assert store.counts("2026-12-01")[Status.ALL] == 2
    assert store.counts()[Status.ALL] == 3
    assert [t.text for t in store.query("B")] == ["미래 B", "미래 B1"]  # 모든 날짜 검색
    a_id = store.query("오늘")[0].id
    store.set_done(a_id, True)  # 다른 날짜 항목도 id로 수정 가능
    assert store.get(a_id).done


def test_move_to_day_and_overdue(store):
    ids = build(store, [("P", 0), ("C", 1), ("Q", 0)])
    assert store.move_to_day([ids["C"]], "2026-12-01") == 1
    assert store.todos("2026-12-01")[0].indent == 0
    assert store.move_to_day([ids["P"]], "2020-01-01") == 1
    assert [t.text for t in store.overdue()] == ["P"]
    assert store.bring_overdue() == 1
    assert [t.text for t in store.todos()][0] == "P"
    assert store.overdue() == []


def test_drag_to_other_day(store):
    """주간 보기: 다른 날 항목 앞으로 끌어 놓으면 그 날로 옮겨진다 (하위 항목 포함)."""
    a = store.add("A")
    store.add("A1", indent=1)
    store.set_day("2026-12-01")
    b = store.add("B")
    store.set_day(today_str())
    store.move(a.id, before_id=b.id)
    assert store.todos() == []
    moved = store.todos("2026-12-01")
    assert [(t.text, t.indent, t.day) for t in moved] == [
        ("A", 0, "2026-12-01"),
        ("A1", 1, "2026-12-01"),
        ("B", 0, "2026-12-01"),
    ]


def test_drag_to_end_of_other_day(store):
    a = store.add("A")
    store.move(a.id, before_id=None, indent=3, day="2026-12-02")
    assert store.todos() == []
    [t] = store.todos("2026-12-02")
    assert (t.text, t.indent, t.day) == ("A", 0, "2026-12-02")  # 빈 날 맨 앞이라 깊이 0


def test_days_saved_and_reloaded(make_store):
    s1 = make_store()
    s1.add("오늘")
    s1.set_day("2026-12-25")
    s1.add("크리스마스")
    s1.flush()
    s2 = make_store()
    s2.load()
    assert s2.days() == sorted([today_str(), "2026-12-25"])
    assert [t.text for t in s2.todos("2026-12-25")] == ["크리스마스"]


def test_v2_file_migrates_to_today(store):
    v2 = {"version": 2, "todos": [{"id": "a", "text": "기존 할 일"}], "archive": []}
    store.todos_file.write_text(json.dumps(v2), encoding="utf-8")
    store.load()
    assert [t.text for t in store.todos(today_str())] == ["기존 할 일"]


def test_restore_goes_to_today(store):
    store.set_day("2026-12-01")
    t = store.add("미래 일")
    store.archive([t.id])
    store.restore([t.id])
    assert store.get(t.id).day == today_str()
