"""PyInstaller 진입점 (패키지 상대 import를 쓰기 위해 바깥에서 main을 부른다)."""

import sys

from sticky_todo.main import main

sys.exit(main())
