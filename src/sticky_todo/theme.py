"""사용자 테마: 노트의 아이콘 자리와 배경을 사용자 그림으로 바꾼다.

- 글꼴·색은 테마에 넣지 않는다 (설정 그대로).
- 테마 폴더(데이터 폴더/themes/current)에 theme.json과 PNG가 들어 있다.
  그림 이름은 자리마다 정해져 있고(pin.png 등), theme.json이 다른 이름을 가리키면 무시한다.
- 그림을 넣을 때 정리한다: 아이콘은 투명 여백을 잘라내고 128×128 정사각형 가운데에,
  배경은 긴 쪽 2000px 이하로. 그래서 칸 가운데와 그림 가운데가 늘 같다.
- 다른 사람이 만든 그림 파일을 여는 기능이라, 열기 전에 파일 크기·형식·가로세로 크기부터 본다.
"""

from __future__ import annotations

import json
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from .fileio import atomic_write_text

FORMAT = 1
# 자리 → 테마 폴더 안 파일 이름
FILES = {
    "pin": "pin.png",  # 고정 핀 버튼
    "pin_on": "pin-on.png",  # 고정됐을 때 (선택)
    "todo": "todo.png",  # 할 일 앞 아이콘 (안 한 일)
    "done": "done.png",  # 끝낸 일 아이콘
    "add": "add.png",  # 입력칸 추가 버튼
    "today": "today.png",  # 월간 달력 오늘 표시
    "background": "background.png",
}
ICON_SLOTS = ("pin", "pin_on", "todo", "done", "add", "today")
SLOT_NAMES = {
    "pin": "고정 핀 버튼",
    "pin_on": "고정됐을 때 그림",
    "todo": "할 일 앞 아이콘",
    "done": "끝낸 일 아이콘",
    "add": "추가 버튼",
    "today": "오늘 표시",
    "background": "배경 그림",
}

ICON_PX = 128
BG_MAX_PX = 2000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SIDE_PX = 8000  # 이보다 큰 그림은 열지 않는다 (메모리 폭탄 방지)
ALLOWED_FORMATS = {b"png", b"jpeg", b"jpg", b"webp"}
OFFSET_MAX = 8.0  # 오늘 표시 그림 위치 조정 (오늘 칸 28px 기준 ±8px)
EXAMPLE_DIR = Path(__file__).resolve().parent / "example_theme"


class ThemeImageError(ValueError):
    """그림을 넣을 수 없을 때 (사용자에게 보여줄 문장)."""


@dataclass
class Background:
    x: float = 0.75  # 그림 가운데의 위치 (노트 안 비율 0~1)
    y: float = 0.8
    width: int = 200  # px (노트가 넓어져도 커지지 않게)
    opacity: int = 40  # % (글씨 대비 4.5:1을 넘지 않게 그릴 때 더 낮춰짐)


@dataclass
class Theme:
    name: str = ""
    folder: Path | None = None
    images: dict[str, Path] = field(default_factory=dict)  # 자리 → 실제 파일
    today_number: bool = True  # 오늘 표시 그림 위에 날짜 숫자
    today_offset: tuple[float, float] = (0.0, 0.0)  # 오늘 표시 그림 위치 조정 (px)
    background: Background = field(default_factory=Background)

    def image(self, slot: str) -> Path | None:
        return self.images.get(slot)

    @property
    def is_empty(self) -> bool:
        return not self.images


# ------------------------------------------------------------ 읽기·쓰기


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return min(hi, max(lo, float(v)))


def _ref(folder: Path, slot: str, value: Any) -> Path | None:
    """theme.json이 가리키는 그림. 정해진 이름이고 실제 파일일 때만."""
    if value != FILES[slot]:
        return None
    path = folder / FILES[slot]
    return path if path.is_file() else None


def load_theme(folder: Path) -> Theme:
    """폴더에서 테마를 읽는다. 없거나 깨졌으면 빈 테마 (기본 모양)."""
    folder = Path(folder)
    try:
        data = json.loads((folder / "theme.json").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return Theme(folder=folder)
    if not isinstance(data, dict):
        return Theme(folder=folder)

    def obj(key: str) -> dict:
        v = data.get(key)
        return v if isinstance(v, dict) else {}

    name = data.get("name")
    theme = Theme(name=name.strip()[:50] if isinstance(name, str) else "", folder=folder)
    pin, check, today, bg = obj("pin"), obj("check"), obj("today"), obj("background")
    refs = {
        "pin": pin.get("off"),
        "pin_on": pin.get("on"),
        "todo": check.get("todo"),
        "done": check.get("done"),
        "add": data.get("add"),
        "today": today.get("image"),
        "background": bg.get("image"),
    }
    for slot, value in refs.items():
        path = _ref(folder, slot, value)
        if path is not None:
            theme.images[slot] = path
    if isinstance(today.get("show_number"), bool):
        theme.today_number = today["show_number"]
    off = today.get("icon_offset")
    if isinstance(off, list) and len(off) == 2:
        theme.today_offset = (
            _num(off[0], -OFFSET_MAX, OFFSET_MAX, 0.0),
            _num(off[1], -OFFSET_MAX, OFFSET_MAX, 0.0),
        )
    d = Background()
    theme.background = Background(
        x=_num(bg.get("x"), 0.0, 1.0, d.x),
        y=_num(bg.get("y"), 0.0, 1.0, d.y),
        width=round(_num(bg.get("width"), 40, 800, d.width)),
        opacity=round(_num(bg.get("opacity"), 5, 100, d.opacity)),
    )
    return theme


def theme_json(theme: Theme) -> dict:
    has = theme.images.__contains__

    def ref(slot: str) -> str | None:
        return FILES[slot] if has(slot) else None

    bg = theme.background
    return {
        "format": FORMAT,
        "name": theme.name,
        "pin": {"off": ref("pin"), "on": ref("pin_on")},
        "check": {"todo": ref("todo"), "done": ref("done")},
        "add": ref("add"),
        "today": {
            "image": ref("today"),
            "show_number": theme.today_number,
            "icon_offset": list(theme.today_offset),
        },
        "background": {
            "image": ref("background"),
            "x": round(bg.x, 3),
            "y": round(bg.y, 3),
            "width": bg.width,
            "opacity": bg.opacity,
        },
    }


def save_theme(theme: Theme) -> None:
    assert theme.folder is not None
    theme.folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(theme_json(theme), ensure_ascii=False, indent=2)
    atomic_write_text(theme.folder / "theme.json", text)


def copy_theme(theme: Theme, folder: Path) -> Theme:
    """다른 폴더로 통째로 복사 (설정 창의 임시 폴더용). 폴더는 비우고 시작한다."""
    folder = Path(folder)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    images = {}
    for slot, src in theme.images.items():
        dst = folder / FILES[slot]
        shutil.copyfile(src, dst)
        images[slot] = dst
    new = replace(theme, folder=folder, images=images, background=replace(theme.background))
    save_theme(new)
    return new


def replace_folder(src: Path, dst: Path) -> None:
    """임시 폴더(src)를 현재 테마(dst)로 바꾼다. 중간에 실패해도 이전 테마가 남게."""
    old = dst.with_name(dst.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if dst.exists():
        dst.rename(old)
    try:
        src.rename(dst)
    except OSError:
        if old.exists():
            old.rename(dst)
        raise
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


def load_example(folder: Path) -> Theme:
    """앱에 들어 있는 예시 테마(귤 친구)를 folder로 복사해서 돌려준다."""
    return copy_theme(load_theme(EXAMPLE_DIR), folder)


# ------------------------------------------------------------ 그림 넣기


def read_image(path: Path) -> QImage:
    """그림 파일을 안전하게 읽는다. 문제가 있으면 ThemeImageError."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as e:
        raise ThemeImageError("파일을 열 수 없어요.") from e
    if size > MAX_FILE_BYTES:
        raise ThemeImageError("그림 파일이 너무 커요 (20MB까지).")
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)  # 확장자가 아니라 내용으로 판단
    fmt = bytes(reader.format()).lower()
    if fmt not in ALLOWED_FORMATS:
        raise ThemeImageError("PNG · JPG · WebP 그림만 넣을 수 있어요.")
    dims = reader.size()
    if not dims.isValid() or dims.width() <= 0 or dims.height() <= 0:
        raise ThemeImageError("그림을 읽을 수 없어요.")
    if max(dims.width(), dims.height()) > MAX_SIDE_PX:
        raise ThemeImageError(f"그림이 너무 커요 (가로·세로 {MAX_SIDE_PX}px까지).")
    reader.setAllocationLimit(512)  # MB
    img = reader.read()
    if img.isNull():
        raise ThemeImageError("그림을 읽을 수 없어요.")
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _fit(img: QImage, longest: int) -> QImage:
    if max(img.width(), img.height()) <= longest:
        return img
    return img.scaled(
        QSize(longest, longest),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def alpha_bounds(img: QImage, threshold: int = 8) -> QRect:
    """투명하지 않은 부분의 범위. 전부 투명하면 빈 QRect."""
    alpha = img.convertToFormat(QImage.Format.Format_Alpha8)
    w, h, bpl = alpha.width(), alpha.height(), alpha.bytesPerLine()
    data = bytes(alpha.constBits())[: bpl * h]
    # 진하기가 threshold보다 큰 곳은 1, 아니면 0으로 바꿔서 줄마다·열마다 겹친다 (C 속도)
    table = bytes(1 if i > threshold else 0 for i in range(256))
    cols = 0
    top = bottom = -1
    for y in range(h):
        line = data[y * bpl : y * bpl + w].translate(table)
        bits = int.from_bytes(line, "big")
        if bits:
            if top < 0:
                top = y
            bottom = y
            cols |= bits
    if top < 0:
        return QRect()
    mask = cols.to_bytes(w, "big")
    left = mask.index(1)
    right = w - 1 - mask[::-1].index(1)
    return QRect(left, top, right - left + 1, bottom - top + 1)


def tidy_icon(img: QImage, size: int = ICON_PX) -> QImage:
    """투명 여백을 잘라내고, 비율을 지킨 채 size×size 정사각형 한가운데에."""
    img = _fit(img, 1024)
    box = alpha_bounds(img)
    if box.isEmpty():
        box = img.rect()
    cropped = img.copy(box)
    scaled = cropped.scaled(
        QSize(size, size),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return out


def import_image(src: Path, slot: str, folder: Path) -> Path:
    """그림을 읽어 정리한 뒤 테마 폴더에 정해진 이름으로 저장한다."""
    if slot not in FILES:
        raise ValueError(slot)
    img = read_image(src)
    out = tidy_icon(img) if slot in ICON_SLOTS else _fit(img, BG_MAX_PX)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / FILES[slot]
    if not out.save(str(dst), "PNG"):
        raise ThemeImageError("그림을 저장할 수 없어요.")
    return dst


# ------------------------------------------------------------ 배경 진하기 상한


def _lum(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def _contrast(a: QColor, b: QColor) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def extreme_colors(img: QImage) -> tuple[QColor, QColor]:
    """그림에서 가장 어두운 색과 가장 밝은 색 (거의 투명한 곳은 뺀다)."""
    small = img.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio)
    small = small.convertToFormat(QImage.Format.Format_ARGB32)
    dark = light = None
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            if c.alpha() < 128:
                continue
            c.setAlpha(255)
            if dark is None or _lum(c) < _lum(dark):
                dark = c
            if light is None or _lum(c) > _lum(light):
                light = c
    if dark is None or light is None:
        return QColor("#808080"), QColor("#808080")
    return dark, light


def max_opacity(img: QImage, card: str, text: str) -> int:
    """배경을 이 진하기(%)까지 그려도 글씨 대비가 4.5:1 이상인 최대값."""
    dark, light = extreme_colors(img)
    bg, fg = QColor(card), QColor(text)

    def blended(c: QColor, t: float) -> QColor:
        return QColor.fromRgbF(
            c.redF() * t + bg.redF() * (1 - t),
            c.greenF() * t + bg.greenF() * (1 - t),
            c.blueF() * t + bg.blueF() * (1 - t),
        )

    for o in range(100, -1, -1):
        t = o / 100
        if all(_contrast(fg, blended(c, t)) >= 4.5 for c in (dark, light)):
            return o
    return 0


# ------------------------------------------------------------ .stodotheme 파일 (속은 zip)

EXT = ".stodotheme"
MAX_PACK_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 20
MAX_RATIO = 100  # 압축을 풀면 100배 넘게 커지는 파일은 압축 폭탄으로 본다
MAX_JSON_BYTES = 64 * 1024
PACK_NAMES = {"theme.json", *FILES.values()}


class ThemeFileError(ValueError):
    """테마 파일을 불러올 수 없을 때 (사용자에게 보여줄 문장)."""


def export_theme(theme: Theme, dest: Path) -> Path:
    """theme.json과 쓰는 그림만 묶어서 dest(.stodotheme)로.

    다 쓴 뒤에 이름을 바꿔서 반쯤 쓴 파일이 남지 않게 한다.
    """
    dest = Path(dest)
    if dest.suffix.lower() != EXT:
        dest = dest.with_name(dest.name + EXT)
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("theme.json", json.dumps(theme_json(theme), ensure_ascii=False, indent=2))
        for slot, path in theme.images.items():
            zf.write(path, FILES[slot])
    tmp.replace(dest)
    return dest


def _check_entries(infos: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > MAX_ENTRIES:
        raise ThemeFileError("테마 파일 안에 파일이 너무 많아요.")
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise ThemeFileError("테마 파일을 풀면 너무 커요 (50MB까지).")
    found: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if "/" in name or "\\" in name or ".." in name or ":" in name or name.startswith("."):
            raise ThemeFileError("테마 파일 안에 이상한 경로가 있어요.")
        if info.flag_bits & 0x1:
            raise ThemeFileError("암호가 걸린 테마 파일은 열 수 없어요.")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ThemeFileError("테마 파일이 비정상적으로 압축돼 있어요.")
        if name in PACK_NAMES:  # 그 밖의 파일은 쓰지 않고 무시
            found[name] = info
    if "theme.json" not in found:
        raise ThemeFileError("테마 파일에 theme.json이 없어요.")
    return found


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with zf.open(info) as f:
        data = f.read(limit + 1)  # 적어 둔 크기를 믿지 않고 실제로 읽은 만큼 센다
    if len(data) > limit:
        raise ThemeFileError("테마 파일 안의 파일이 너무 커요.")
    return data


def import_theme(src: Path, folder: Path) -> Theme:
    """테마 파일을 검사하고 풀어서 folder에 넣는다. 하나라도 걸리면 folder는 그대로.

    그림은 '내 그림…'과 똑같이 다시 검사·정리한다 (테마 파일 안의 그림이라고 믿지 않는다).
    """
    src, folder = Path(src), Path(folder)
    try:
        size = src.stat().st_size
    except OSError as e:
        raise ThemeFileError("파일을 열 수 없어요.") from e
    if size > MAX_PACK_BYTES:
        raise ThemeFileError("테마 파일이 너무 커요 (20MB까지).")
    work = folder.with_name(folder.name + ".import")
    raw = folder.with_name(folder.name + ".raw")
    for d in (work, raw):
        shutil.rmtree(d, ignore_errors=True)
    try:
        try:
            with zipfile.ZipFile(src) as zf:
                found = _check_entries(zf.infolist())
                data = _read_entry(zf, found.pop("theme.json"), MAX_JSON_BYTES)
                raw.mkdir(parents=True)
                for name, info in found.items():
                    (raw / name).write_bytes(_read_entry(zf, info, MAX_FILE_BYTES))
        except ThemeFileError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError,
                NotImplementedError, RuntimeError, ValueError) as e:  # fmt: skip
            raise ThemeFileError("테마 파일이 망가졌거나 테마 파일이 아니에요.") from e
        work.mkdir(parents=True)
        (work / "theme.json").write_bytes(data)
        for slot, name in FILES.items():
            if (raw / name).is_file():
                try:
                    import_image(raw / name, slot, work)
                except ThemeImageError as e:
                    raise ThemeFileError(f"{SLOT_NAMES[slot]} 그림: {e}") from e
        theme = load_theme(work)
        theme.folder = folder
        theme.images = {s: folder / FILES[s] for s in theme.images}
        if folder.exists():
            shutil.rmtree(folder)
        work.rename(folder)
        return theme
    finally:
        shutil.rmtree(raw, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
