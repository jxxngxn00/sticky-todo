from sticky_todo.paths import default_export_dir, resolve_paths


def test_appdata_mode(tmp_path):
    p = resolve_paths(base_dir=tmp_path / "app", appdata=str(tmp_path / "Roaming"))
    assert not p.portable
    assert p.data_dir == tmp_path / "Roaming" / "StickyTodo"
    assert p.todos_file.name == "todos.json"
    assert p.settings_file.name == "settings.json"
    assert p.backups_dir == p.data_dir / "backups"
    assert p.themes_dir == p.data_dir / "themes"


def test_portable_mode(tmp_path):
    (tmp_path / "portable").write_text("", encoding="utf-8")
    p = resolve_paths(base_dir=tmp_path, appdata=str(tmp_path / "Roaming"))
    assert p.portable
    assert p.data_dir == tmp_path / "data"


def test_portable_marker_must_be_file(tmp_path):
    (tmp_path / "portable").mkdir()
    assert not resolve_paths(base_dir=tmp_path, appdata=str(tmp_path)).portable


def test_ensure_dirs(tmp_path):
    p = resolve_paths(base_dir=tmp_path, appdata=str(tmp_path / "R"))
    p.ensure_dirs()
    assert p.backups_dir.is_dir()


def test_export_dir():
    d = default_export_dir()
    assert d.name == "StickyTodo"
    assert d.parent.exists()  # 실제 '문서' 폴더
