"""앱에 같이 넣은 글꼴·번역을 불러온다."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QFontDatabase

FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def load_bundled_fonts() -> list[str]:
    """Pretendard(OFL) 같이 넣은 글꼴을 등록한다. 설치 안 된 PC에서도 같은 모양으로 보이게."""
    families: list[str] = []
    for path in sorted(FONTS_DIR.glob("*.otf")) + sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id != -1:
            families += QFontDatabase.applicationFontFamilies(font_id)
    return sorted(set(families))


def install_korean_translation(app: QCoreApplication) -> bool:
    """Qt 기본 창(색 선택, 예/아니요 등)의 글자를 한국어로."""
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale(QLocale.Language.Korean), "qtbase", "_", folder):
        app.installTranslator(translator)
        return True
    return False
