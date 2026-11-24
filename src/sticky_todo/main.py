"""진입점. 노트 위젯은 2단계에서 연결한다."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication

from .paths import resolve_paths
from .storage import TodoStore


def main() -> int:
    app = QCoreApplication(sys.argv)
    paths = resolve_paths()
    paths.ensure_dirs()
    store = TodoStore(paths.todos_file, paths.backups_dir, parent=app)
    result = store.load()
    counts = store.counts()
    mode = "포터블" if paths.portable else "일반"
    print(f"[{mode}] 데이터 폴더: {paths.data_dir}")
    print(f"로드 상태: {result.status} {result.message}")
    print(f"할 일 {counts['all']}개 (완료 {counts['done']}개)")
    print("노트 위젯은 2단계에서 추가될 예정이야.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
