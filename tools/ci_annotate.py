"""CI에서 실패한 테스트를 GitHub annotation으로 남긴다 (로그인 없이 실패 이유를 볼 수 있게).

사용법 (GitHub Actions):  python tools/ci_annotate.py pytest-report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def _escape(text: str) -> str:
    # 워크플로 명령 형식에서 줄바꿈·%는 이스케이프해야 한다
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(path: str) -> int:
    root = ET.parse(path).getroot()
    count = 0
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            count += 1
            name = f"{case.get('classname')}::{case.get('name')}"
            message = (node.get("message") or "").strip()
            detail = (node.text or "").strip().splitlines()[-15:]
            body = _escape(f"{message}\n" + "\n".join(detail))
            print(f"::error title={_escape(name)}::{body[:3000]}")
    print(f"실패한 테스트 {count}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "pytest-report.xml"))
