import pytest

from sticky_todo import backdrop
from sticky_todo.note_widget import GLASS_FALLBACK_MIN_ALPHA, NoteWidget
from sticky_todo.settings import Settings, contrast_text, luminance


@pytest.mark.parametrize(
    ("build", "ok"), [(19045, False), (21999, False), (22000, True), (26300, True)]
)
def test_acrylic_supported(build, ok):
    assert backdrop.acrylic_supported(build) is ok


def test_enable_glass_unsupported_build(monkeypatch):
    monkeypatch.setattr(backdrop, "windows_build", lambda: 19045)
    assert backdrop.enable_glass(12345) is False


def test_enable_glass_bad_handle_never_raises():
    assert backdrop.enable_glass(0) is False
    # 존재하지 않는 핸들: 윈도우가 실패를 돌려줄 뿐 예외는 없어야 함
    assert backdrop.enable_glass(0x7FFF0000) is False
    backdrop.disable_glass(0x7FFF0000)


def test_enable_glass_dwm_raises(monkeypatch):
    def boom():
        raise OSError("dwmapi 없음")

    monkeypatch.setattr(backdrop, "acrylic_supported", lambda build=None: True)
    monkeypatch.setattr(backdrop, "_user32", boom)
    monkeypatch.setattr(backdrop, "_dwm", boom)
    assert backdrop.enable_glass(1) is False
    backdrop.disable_glass(1)


@pytest.mark.parametrize(
    ("bg", "text"),
    [
        ("#FFFFFF", "#1A1A1A"),
        ("#1E1E1E", "#FFFFFF"),
        ("#9FD3FF", "#1A1A1A"),
        ("#FFB6CF", "#1A1A1A"),
        ("#000000", "#FFFFFF"),
        ("#3949AB", "#FFFFFF"),
    ],
)
def test_contrast_text(bg, text):
    assert contrast_text(bg) == text


def test_luminance_bounds():
    assert luminance("#000000") == 0
    assert luminance("#FFFFFF") == pytest.approx(1)


# ---------------------------------------------------------------- 노트의 글래스 모드


@pytest.fixture
def glass_note(store, tmp_path):
    s = Settings()
    s.set("style", "glass")
    w = NoteWidget(store, s, tmp_path / "settings.json")
    w.show()
    yield w
    w._settings_timer.stop()
    w.hide()
    w.deleteLater()


def test_glass_fallback_when_blur_unavailable(glass_note):
    # 테스트 환경(offscreen)에는 진짜 창이 없어 블러가 켜질 수 없다 → 반투명 대체
    assert glass_note.glass_active is False
    glass_note.settings.set("glass_transparency", 100)
    pal = glass_note.note_palette()
    alpha = float(pal["card"].rsplit(",", 1)[1].rstrip(")"))
    assert alpha == pytest.approx(GLASS_FALLBACK_MIN_ALPHA)
    assert pal["shadow"] is False


def test_glass_palette_when_blur_on(glass_note):
    glass_note._glass_on = True
    glass_note.settings.set("glass_transparency", 70)
    pal = glass_note.note_palette()
    assert pal["card"].endswith("0.300)")
    glass_note.settings.set("glass_transparency", 100)
    assert glass_note.note_palette()["card"].endswith("0.050)")  # 클릭 통과 방지 최소값
    assert "rgba(255, 255, 255" in pal["border"]  # 가장자리 하이라이트


def test_switch_back_to_sticky(glass_note):
    glass_note.settings.set("style", "sticky")
    glass_note.apply_settings()
    assert glass_note.note_palette()["card"] == glass_note.settings["bg_color"]
    assert glass_note.layout().contentsMargins().left() > 0  # 그림자 여백 복귀
