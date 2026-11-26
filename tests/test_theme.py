"""사용자 테마: 테마 파일 읽기, 그림 정리, 노트 적용, 설정 창 '테마' 탭."""

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from sticky_todo.note_widget import NoteWidget
from sticky_todo.settings import Settings
from sticky_todo.theme import (
    EXAMPLE_DIR,
    FILES,
    ICON_PX,
    Background,
    Theme,
    ThemeImageError,
    alpha_bounds,
    copy_theme,
    import_image,
    load_example,
    load_theme,
    max_opacity,
    read_image,
    replace_folder,
    save_theme,
)

# ------------------------------------------------------------ 도우미


def png(path, w, h, fill=None, rect=None, color="#FF8800"):
    """투명 바탕에 색 사각형 하나 (rect=(x, y, w, h))."""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill) if fill else Qt.GlobalColor.transparent)
    if rect:
        p = QPainter(img)
        p.fillRect(*rect, QColor(color))
        p.end()
    assert img.save(str(path), "PNG")
    return path


def center_of(rect):
    return (rect.x() + (rect.width() - 1) / 2, rect.y() + (rect.height() - 1) / 2)


# ------------------------------------------------------------ 테마 파일


def test_missing_or_broken_theme_is_empty(qapp, tmp_path):
    assert load_theme(tmp_path / "none").is_empty
    (tmp_path / "theme.json").write_text("{깨짐", encoding="utf-8")
    assert load_theme(tmp_path).is_empty
    (tmp_path / "theme.json").write_text("[1, 2]", encoding="utf-8")
    assert load_theme(tmp_path).is_empty


def test_only_fixed_file_names_inside_the_folder(qapp, tmp_path):
    png(tmp_path / "pin.png", 8, 8, "#000000")
    png(tmp_path / "other.png", 8, 8, "#000000")
    (tmp_path / "theme.json").write_text(
        json.dumps(
            {
                "pin": {"off": "pin.png", "on": "../../secret.png"},
                "check": {"todo": "other.png", "done": "C:/Windows/x.png"},
                "add": "add.png",  # 파일 없음
                "today": {"image": 3, "show_number": "yes", "icon_offset": [100, "a"]},
                "background": {"image": None, "x": 9, "y": -1, "width": True, "opacity": 0},
            }
        ),
        encoding="utf-8",
    )
    t = load_theme(tmp_path)
    assert list(t.images) == ["pin"]
    assert t.today_number is True and t.today_offset == (8.0, 0.0)  # 범위로 자름
    assert (t.background.x, t.background.y) == (1.0, 0.0)
    assert t.background.width == Background().width and t.background.opacity == 5


def test_save_load_roundtrip(qapp, tmp_path):
    png(tmp_path / "today.png", 8, 8, "#000000")
    t = Theme(name="내 테마", folder=tmp_path, images={"today": tmp_path / "today.png"},
              today_number=False, today_offset=(1.5, -2.0),
              background=Background(x=0.3, y=0.6, width=150, opacity=55))  # fmt: skip
    save_theme(t)
    back = load_theme(tmp_path)
    assert back.name == "내 테마" and back.images == {"today": tmp_path / "today.png"}
    assert back.today_number is False and back.today_offset == (1.5, -2.0)
    assert back.background == Background(x=0.3, y=0.6, width=150, opacity=55)


def test_copy_and_replace_folder(qapp, tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    png(src / "add.png", 8, 8, "#000000")
    t = Theme(name="A", folder=src, images={"add": src / "add.png"})
    save_theme(t)
    draft = copy_theme(t, tmp_path / "draft")
    assert draft.images["add"].parent == tmp_path / "draft"
    current = tmp_path / "current"
    current.mkdir()
    (current / "old.txt").write_text("x")
    replace_folder(tmp_path / "draft", current)
    assert not (tmp_path / "draft").exists() and not (current / "old.txt").exists()
    assert load_theme(current).images == {"add": current / "add.png"}


def test_example_theme_is_complete_and_centered(qapp):
    t = load_theme(EXAMPLE_DIR)
    assert t.name == "귤 친구" and set(t.images) == set(FILES)
    for slot, path in t.images.items():
        img = QImage(str(path))
        if slot == "background":
            continue
        assert (img.width(), img.height()) == (ICON_PX, ICON_PX)
        cx, cy = center_of(alpha_bounds(img))
        assert abs(cx - (ICON_PX - 1) / 2) <= 1 and abs(cy - (ICON_PX - 1) / 2) <= 1, slot


# ------------------------------------------------------------ 그림 넣기


def test_icon_is_trimmed_and_centered(qapp, tmp_path):
    # 오른쪽 아래로 치우친 그림 (투명 여백이 많음)
    src = png(tmp_path / "s.png", 200, 100, rect=(150, 60, 40, 30))
    dst = import_image(src, "pin", tmp_path / "theme")
    assert dst == tmp_path / "theme" / "pin.png"
    img = QImage(str(dst))
    assert (img.width(), img.height()) == (ICON_PX, ICON_PX)
    box = alpha_bounds(img)
    assert box.width() == ICON_PX  # 긴 쪽이 칸을 꽉 채움
    cx, cy = center_of(box)
    assert abs(cx - (ICON_PX - 1) / 2) <= 1 and abs(cy - (ICON_PX - 1) / 2) <= 1


def test_background_is_shrunk_not_trimmed(qapp, tmp_path):
    src = png(tmp_path / "bg.png", 3000, 1000, fill="#336699")
    img = QImage(str(import_image(src, "background", tmp_path / "t")))
    assert (img.width(), img.height()) == (2000, 666)


def test_rejects_bad_files(qapp, tmp_path):
    fake = tmp_path / "fake.png"
    fake.write_text("그림 아님", encoding="utf-8")
    with pytest.raises(ThemeImageError):
        read_image(fake)
    bmp = tmp_path / "pic.png"  # 이름만 png, 내용은 BMP
    QImage(10, 10, QImage.Format.Format_RGB32).save(str(bmp), "BMP")
    with pytest.raises(ThemeImageError, match="PNG"):
        read_image(bmp)
    huge = png(tmp_path / "huge.png", 8001, 2, fill="#000000")
    with pytest.raises(ThemeImageError, match="너무 커요"):
        read_image(huge)
    big = tmp_path / "big.png"
    with open(big, "wb") as f:
        f.seek(20 * 1024 * 1024 + 1)
        f.write(b"\0")
    with pytest.raises(ThemeImageError, match="20MB"):
        read_image(big)
    with pytest.raises(ThemeImageError):
        read_image(tmp_path / "없음.png")


def test_max_opacity_keeps_text_readable(qapp):
    dark = QImage(8, 8, QImage.Format.Format_ARGB32)
    dark.fill(QColor("#000000"))
    cap = max_opacity(dark, "#EBEBEB", "#2F2F32")
    assert 0 < cap < 60
    light = QImage(8, 8, QImage.Format.Format_ARGB32)
    light.fill(QColor("#FFFFFF"))
    assert max_opacity(light, "#EBEBEB", "#2F2F32") == 100


# ------------------------------------------------------------ 노트에 적용


@pytest.fixture
def note(store, tmp_path):
    s = Settings()
    s.apply_preset("ios")
    w = NoteWidget(store, s, tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


@pytest.fixture
def themed(note):
    note.set_theme(load_example(note.themes_dir / "current"))
    return note


def settle():
    for _ in range(3):
        QGuiApplication.processEvents()


def render(widget, scale=4):
    pix = QPixmap(widget.size() * scale)
    pix.setDevicePixelRatio(scale)
    pix.fill(Qt.GlobalColor.white)
    widget.render(pix)
    return pix.toImage()


def bounds(img, pred, area=None):
    x0, y0, x1, y1 = area or (0, 0, img.width(), img.height())
    pts = [(x, y) for y in range(y0, y1) for x in range(x0, x1) if pred(img.pixelColor(x, y))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def mid(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def test_theme_applies_to_icons(themed, store):
    a = store.add("할 일")
    b = store.add("끝낸 일")
    store.set_done(b.id, True)
    rows = {r.todo.id: r for r in themed.list_view.rows}
    assert rows[a.id].check.images[0] is not None and rows[b.id].check.images[1] is not None
    tb = themed.title_bar
    assert tb.pin_images[0] is not None and tb.pin_images[1] is not None
    off = tb.pin_btn.icon().pixmap(22, 22).toImage()
    QTest.mouseClick(tb.pin_btn, Qt.MouseButton.LeftButton)  # 실제로 눌러서 고정
    assert tb.pin_btn.isChecked()
    on = tb.pin_btn.icon().pixmap(22, 22).toImage()
    assert off != on  # 꺼짐(흐린 회색) ↔ 켜짐(또렷)
    QTest.mouseClick(tb.pin_btn, Qt.MouseButton.LeftButton)
    assert themed.input.theme_icon is not None
    assert themed.calendar.today_image is not None
    assert themed.card.bg_image is not None and themed.card.show_bg


def test_board_hides_background(themed):
    themed.set_view_mode("week")
    themed.resize(1000, 460)
    settle()
    assert themed.board_active and not themed.card.show_bg
    themed.resize(320, 600)
    settle()
    assert not themed.board_active and themed.card.show_bg


def test_today_number_centered_on_image(themed):
    """오늘 표시 그림의 가운데와 날짜 숫자(글자 모양)의 가운데가 1px 안에서 맞는다."""
    themed.set_view_mode("month")
    themed.resize(340, 720)
    settle()
    cal = themed.calendar
    i = next(i for i, c in enumerate(cal.cells) if c.today)
    r = cal.cell_rect(i)
    img = render(cal)
    area = (int(r.left() * 4), int(r.top() * 4), int(r.right() * 4), int(r.bottom() * 4))
    ring = bounds(img, lambda c: abs(c.red() - 0xD9) < 30 and abs(c.green() - 0x74) < 30
                  and c.blue() < 70, area)  # fmt: skip
    ink = bounds(img, lambda c: c.red() < 90 and c.green() < 90 and c.blue() < 90,
                 (ring[0] + 6, ring[1] + 6, ring[2] - 6, ring[3] - 6))  # fmt: skip
    for a, b in zip(mid(ring), mid(ink), strict=True):
        assert abs(a - b) / 4 <= 1
    # 그림을 옮기면 그림만 움직이고 숫자는 그대로
    themed.theme.today_offset = (4.0, 0.0)
    themed.set_theme(themed.theme, rebuild=False)
    img2 = render(cal)
    ring2 = bounds(img2, lambda c: abs(c.red() - 0xD9) < 30 and abs(c.green() - 0x74) < 30
                   and c.blue() < 70, (area[0], area[1], area[2] + 40, area[3]))  # fmt: skip
    ink2 = bounds(img2, lambda c: c.red() < 90 and c.green() < 90 and c.blue() < 90,
                  (ink[0] - 4, ink[1] - 4, ink[2] + 4, ink[3] + 4))  # fmt: skip
    assert ring2[0] > ring[0] + 8 and ink2 == ink


@pytest.mark.parametrize("which", ["pin", "check"])
def test_icons_centered_in_their_box(themed, store, which):
    store.add("할 일")
    widget = themed.title_bar.pin_btn if which == "pin" else themed.list_view.rows[0].check
    img = render(widget)
    b = bounds(img, lambda c: c.red() + c.green() + c.blue() < 735)  # 흰 바탕이 아닌 곳
    cx, cy = widget.width() * 4 / 2, widget.height() * 4 / 2
    assert abs(mid(b)[0] - cx) / 4 <= 1 and abs(mid(b)[1] - cy) / 4 <= 1


def test_drag_background_on_note(themed):
    moved = []
    themed.set_bg_drag(lambda x, y: moved.append((x, y)))
    overlay = themed._bg_overlay
    assert overlay is not None and overlay.geometry() == themed.card.rect()
    pos = QPoint(overlay.width() // 4, overlay.height() // 4)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseMove(overlay, QPoint(overlay.width() // 2, overlay.height() // 2))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=pos)
    assert len(moved) == 2 and abs(moved[0][0] - 0.25) < 0.02  # 누른 곳 → 끈 곳
    assert abs(themed.card.bg_pos[0] - 0.5) < 0.02 and abs(themed.card.bg_pos[1] - 0.5) < 0.02
    themed.set_bg_drag(None)
    assert themed._bg_overlay is None


# ------------------------------------------------------------ 설정 창 '테마' 탭


@pytest.fixture
def win(note):
    from sticky_todo.settings_window import SettingsWindow

    w = SettingsWindow(note)
    w.show()
    yield w
    if w.isVisible():
        w.reject()


def test_tab_cancel_restores(win, note):
    tab = win.theme_tab
    tab.use_example()
    assert not note.theme.is_empty and note.card.bg_image is not None
    win.reject()
    assert note.theme.is_empty and note.card.bg_image is None
    assert not (note.themes_dir / "draft").exists()
    assert not (note.themes_dir / "current" / "theme.json").exists()


def test_tab_ok_saves_and_reloads(win, note, tmp_path, monkeypatch):
    tab = win.theme_tab
    src = png(tmp_path / "mine.png", 60, 40, rect=(5, 5, 20, 30))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))
    QTest.mouseClick(next(b for b in tab.findChildren(type(tab.example_btn))
                          if b.text() == "내 그림…"), Qt.MouseButton.LeftButton)  # fmt: skip
    assert "pin" in tab.theme.images  # 첫 번째 '내 그림…'은 고정 핀
    tab.name_edit.setText("내 테마")
    win.accept()
    current = note.themes_dir / "current"
    t = load_theme(current)
    assert t.name == "내 테마" and t.images == {"pin": current / "pin.png"}
    assert note.title_bar.pin_images[0] is not None


def test_tab_bad_image_warns(win, tmp_path, monkeypatch, dialogs):
    bad = tmp_path / "bad.png"
    bad.write_text("x", encoding="utf-8")
    assert not win.theme_tab.set_image("add", bad)
    assert "add" not in win.theme_tab.theme.images


def test_tab_controls_show_only_when_used(win):
    tab = win.theme_tab
    assert tab.today_box.isHidden() and tab.bg_box.isHidden()
    tab.use_example()
    assert not tab.today_box.isHidden() and not tab.bg_box.isHidden()
    tab.bg_opacity.setValue(100)  # 너무 진하면 상한까지만
    cap = win.note.bg_opacity_cap
    assert cap < 100 and not tab.bg_warn.isHidden()
    assert win.note.card.bg_opacity == pytest.approx(cap / 100)
    tab.spot_btns[0].click()  # 왼쪽 위
    assert (tab.theme.background.x, tab.theme.background.y) == (0.2, 0.15)
    assert win.note.card.bg_pos == (0.2, 0.15)
    tab.clear_slot("pin")  # 핀을 비우면 '고정됐을 때'도 같이
    assert "pin" not in tab.theme.images and "pin_on" not in tab.theme.images


# ------------------------------------------------------------ .stodotheme 파일

import zipfile  # noqa: E402

from sticky_todo.theme import ThemeFileError, export_theme, import_theme  # noqa: E402


def example_pack(tmp_path):
    t = load_example(tmp_path / "src")
    t.name = "귤 친구 2"
    t.today_offset = (1.5, -1.0)
    return t, export_theme(t, tmp_path / "out" / "귤")


def test_export_import_roundtrip(qapp, tmp_path):
    (tmp_path / "out").mkdir()
    t, pack = example_pack(tmp_path)
    assert pack.name == "귤.stodotheme"  # 확장자를 붙여 준다
    with zipfile.ZipFile(pack) as zf:
        assert sorted(zf.namelist()) == sorted(["theme.json", *FILES.values()])
    back = import_theme(pack, tmp_path / "draft")
    assert back.name == "귤 친구 2" and set(back.images) == set(FILES)
    assert back.today_offset == (1.5, -1.0) and back.background == t.background
    for slot, path in back.images.items():
        assert path.parent == tmp_path / "draft"
        assert QImage(str(path)) == QImage(str(t.images[slot]))  # 다시 정리해도 같은 그림
    assert not (tmp_path / "draft.import").exists() and not (tmp_path / "draft.raw").exists()


def make_zip(path, entries, method=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", method) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def good_png_bytes(tmp_path):
    return png(tmp_path / "g.png", 16, 16, rect=(2, 2, 10, 10)).read_bytes()


THEME_JSON = json.dumps({"name": "x", "add": "add.png"})


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("path", "경로"),
        ("parent", "경로"),
        ("drive", "경로"),
        ("bomb", "압축"),
        ("many", "너무 많아요"),
        ("fake_image", "추가 버튼 그림"),
        ("no_json", "theme.json"),
        ("broken", "망가졌거나"),
    ],
)
def test_import_rejects(qapp, tmp_path, case, message):
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "keep.txt").write_text("원래 것")
    f = tmp_path / f"{case}.stodotheme"
    good = good_png_bytes(tmp_path)
    if case == "path":
        make_zip(f, {"theme.json": THEME_JSON, "img/add.png": good})
    elif case == "parent":
        make_zip(f, {"theme.json": THEME_JSON, "../add.png": good})
    elif case == "drive":
        make_zip(f, {"theme.json": THEME_JSON, "C:add.png": good})
    elif case == "bomb":
        make_zip(f, {"theme.json": THEME_JSON, "add.png": b"\0" * (5 * 1024 * 1024)})
    elif case == "many":
        make_zip(f, {"theme.json": THEME_JSON, **{f"x{i}.txt": b"a" for i in range(25)}})
    elif case == "fake_image":
        make_zip(f, {"theme.json": THEME_JSON, "add.png": b"not an image"})
    elif case == "no_json":
        make_zip(f, {"add.png": good})
    elif case == "broken":
        f.write_bytes(b"PK\x03\x04 broken zip")
    with pytest.raises(ThemeFileError, match=message):
        import_theme(f, draft)
    assert (draft / "keep.txt").read_text() == "원래 것"  # 실패하면 아무것도 안 바뀜


def test_import_rejects_too_big_file(qapp, tmp_path):
    f = tmp_path / "big.stodotheme"
    with open(f, "wb") as out:
        out.seek(20 * 1024 * 1024 + 1)
        out.write(b"\0")
    with pytest.raises(ThemeFileError, match="20MB"):
        import_theme(f, tmp_path / "draft")


def test_import_ignores_unknown_files_and_bad_values(qapp, tmp_path):
    f = make_zip(tmp_path / "ok.stodotheme", {
        "theme.json": json.dumps({"name": "<b>이름</b>", "add": "add.png",
                                  "background": {"x": 5, "opacity": "많이"}}),
        "add.png": good_png_bytes(tmp_path),
        "readme.txt": "무시",
        "script.py": "print(1)",
    })  # fmt: skip
    t = import_theme(f, tmp_path / "draft")
    assert t.name == "<b>이름</b>"  # 글자로만 쓰인다 (QLineEdit)
    assert set(t.images) == {"add"} and t.background.x == 1.0 and t.background.opacity == 40
    assert sorted(p.name for p in (tmp_path / "draft").iterdir()) == ["add.png", "theme.json"]


def test_tab_save_and_load_theme_file(win, note, tmp_path, monkeypatch):
    tab = win.theme_tab
    tab.use_example()
    out = tmp_path / "share" / "내 귤.stodotheme"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    tab.save_btn.click()
    assert out.is_file() and "저장했어요" in note.toast_label.text()
    tab.clear_all()
    assert note.theme.is_empty
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), ""))
    tab.load_btn.click()
    assert set(note.theme.images) == set(FILES) and tab.name_edit.text() == "귤 친구"
    win.reject()  # 취소하면 원래대로 (불러오기 전)
    assert note.theme.is_empty


def test_drop_theme_file_on_note(note, tmp_path, dialogs):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication

    (tmp_path / "out").mkdir()
    _, pack = example_pack(tmp_path)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(pack))])
    pos = QPointF(note.width() / 2, note.height() / 2)
    enter = QDragEnterEvent(pos, Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)  # fmt: skip
    QApplication.sendEvent(note, enter)
    assert enter.isAccepted()
    drop = QDropEvent(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)  # fmt: skip
    QApplication.sendEvent(note, drop)
    win = note._settings_win
    assert win is not None and win.tabs.currentWidget() is win.theme_tab
    assert note.theme.name == "귤 친구 2" and not note.theme.is_empty
    win.accept()
    assert load_theme(note.themes_dir / "current").name == "귤 친구 2"
    # 다른 파일은 받지 않는다
    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(tmp_path / "a.txt"))])
    e2 = QDragEnterEvent(pos, Qt.DropAction.CopyAction, other,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)  # fmt: skip
    e2.ignore()
    QApplication.sendEvent(note, e2)
    assert not e2.isAccepted()
