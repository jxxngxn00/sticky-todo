"""exe 빌드: 아이콘(.ico) 생성 → PyInstaller(--onefile --noconsole) → SHA256 체크섬.

사용법:  uv run python tools/build.py
결과물:  dist/StickyTodo.exe, dist/StickyTodo.exe.sha256, dist/StickyTodo-<버전>.zip (배포용)
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "StickyTodo"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def make_ico(path: Path) -> None:
    """앱 아이콘을 여러 크기 PNG로 그려 .ico 파일 하나로 묶는다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    from sticky_todo import icons

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    images: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        icons.app_pixmap(size).save(buf, "PNG")
        images.append((size, bytes(data)))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # ICO에서 256은 0으로 적는다
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


def run_pyinstaller(icon: Path) -> Path:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--noconsole",
        "--name", NAME,
        "--icon", str(icon),
        "--paths", str(ROOT / "src"),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        "--distpath", str(DIST),
        "--exclude-module", "tkinter",
        # 같이 넣는 글꼴(Pretendard)과 라이선스
        "--add-data", f"{ROOT / 'src' / 'sticky_todo' / 'fonts'}{os.pathsep}sticky_todo/fonts",
        "--add-data", f"{ROOT / 'licenses'}{os.pathsep}licenses",
        str(ROOT / "packaging" / "entry.py"),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, cwd=ROOT)
    return DIST / f"{NAME}.exe"


def write_checksum(exe: Path) -> str:
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    exe.with_name(exe.name + ".sha256").write_text(f"{digest}  {exe.name}\n", encoding="ascii")
    return digest


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def make_zip(exe: Path) -> Path:
    """친구·사용자에게 보낼 묶음: exe + 체크섬 + 설명서 + 라이선스."""
    out = DIST / f"{NAME}-{version()}.zip"
    files = {
        exe: exe.name,
        exe.with_name(exe.name + ".sha256"): exe.name + ".sha256",
        ROOT / "README.md": "README.md",
        ROOT / "THIRD-PARTY-NOTICES.txt": "THIRD-PARTY-NOTICES.txt",
        ROOT / "LICENSE": "LICENSE.txt",
        ROOT / "licenses" / "Pretendard-OFL.txt": "licenses/Pretendard-OFL.txt",
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, name in files.items():
            z.write(src, f"{NAME}/{name}")
    return out


def main() -> int:
    icon = BUILD / "icon.ico"
    make_ico(icon)
    exe = run_pyinstaller(icon)
    digest = write_checksum(exe)
    bundle = make_zip(exe)
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n완료: {exe}  ({size_mb:.1f} MB)\nSHA256: {digest}\n배포용: {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
