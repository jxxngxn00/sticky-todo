# Sticky Todo

윈도우 바탕화면에 스티키 노트처럼 떠 있는 할 일 목록 위젯.
날짜별 페이지, 하위 할 일, 완료 항목 보관함, 전체 검색 창, 텍스트 파일 저장/불러오기를 지원한다.
인터넷에 연결하지 않으며, 데이터는 내 PC에만 저장된다.

## 설치

1. **[StickyTodo-Setup.exe 받기](https://github.com/jxxngxn00/sticky-todo/releases/latest/download/StickyTodo-Setup.exe)** 후 실행한다.
   - 브라우저가 "일반적으로 다운로드되지 않는 파일"이라고 하면 **⋯ → 유지**.
   - 파란 **"Windows의 PC 보호"** 창이 뜨면 **추가 정보 → 실행**
     (코드 서명을 하지 않은 프로그램이라 뜨는 경고).
2. **다음 → 설치**. 관리자 권한은 필요 없다.
   - 내 계정 폴더(`%LOCALAPPDATA%\Programs\StickyTodo`)에 설치되고 시작 메뉴에 등록된다.
   - 바탕화면 바로가기, 윈도우 시작 시 자동 실행은 설치 화면에서 고른다.
3. **업데이트**: 새 Setup을 받아 실행하면 덮어써진다 (할 일은 그대로).
4. **삭제**: 설정 → 앱 → Sticky Todo → 제거. 할 일 데이터까지 지울지 물어본다.

설치 없이 쓰고 싶으면 릴리즈의 `StickyTodo.exe`(단독 실행 파일)를 받아 전용 폴더에 두고 실행해도 된다.
받은 파일이 진짜인지는 함께 올라간 `.sha256` 값과 비교해 확인할 수 있다.

```powershell
Get-FileHash .\StickyTodo-Setup.exe -Algorithm SHA256
```

## 사용법

| 하고 싶은 것 | 방법 |
|---|---|
| 할 일 추가 | 아래 입력칸에 쓰고 Enter (Tab: 하위 할 일) |
| 고치기 / 하위 할 일로 | 더블클릭 / Tab (상위로 꺼내기: Shift+Tab) |
| 내일로 미루기·보관·삭제 | 할 일 우클릭 |
| 실수로 지웠을 때 | 아래 알림의 **되돌리기**(10초), ☰ → 방금 지운 것 되돌리기, 또는 Ctrl+Z |
| 날짜 넘기기 | 제목 바 ‹ › 또는 Alt+← / Alt+→, 날짜 더블클릭 = 오늘 |
| 전체 목록·검색 | ☰ → 전체 목록 보기 또는 Ctrl+Shift+L |
| 어디서든 노트 불러오기 | **Ctrl+Alt+T** (다른 프로그램 사용 중에도, 설정 → 목록 동작에서 끌 수 있음) |
| 노트 숨기기 / 다시 보기 | ✕ → 트레이로 숨김, 트레이 아이콘 클릭으로 다시 보기 |
| 종료 | ☰ → 종료, 또는 트레이 아이콘 우클릭 → 종료 |
| 윈도우 켤 때 자동 실행 | ☰ 또는 트레이 메뉴 → 시작 시 자동 실행 |

- 윈도우 11은 새 트레이 아이콘을 작업표시줄의 **^(숨겨진 아이콘)** 안에 넣는다.
  항상 보이게 하려면 아이콘을 작업표시줄로 끌어다 놓거나,
  설정 → 개인 설정 → 작업 표시줄 → 기타 시스템 트레이 아이콘에서 Sticky Todo를 켠다.
- 자동 실행은 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`에 exe 경로를 등록한다
  (관리자 권한 불필요). exe를 다른 폴더로 옮기면 다음 실행 때 경로가 자동으로 고쳐진다.

## 데이터 저장 위치

- 기본: `%APPDATA%\StickyTodo\`
  - `todos.json` 할 일, `settings.json` 설정, `backups\` 이전 버전 (최근 5개)
- 포터블 모드: exe와 같은 폴더에 `portable`이라는 빈 파일을 두면 그 폴더의 `data\`에 저장
- `todos.json`이 손상되면 `backups\`의 최신 정상본으로 자동 복구하고, 손상된 파일은
  `todos.json.corrupt-날짜`로 따로 보관한다.
- 할 일은 **암호화하지 않은 텍스트**로 저장된다. 비밀번호 같은 민감한 정보는 적지 않는다.

## 기능 메모

### 하위 할 일
- 깊이 제한 없음. 부모를 옮기거나 지우면 하위 항목도 같이 움직인다. 완료 체크는 그 항목만 바뀐다.
- 텍스트 파일(체크리스트 `- [ ] 할 일` 형식)로 저장할 때 한 단계당 공백 2칸. 불러올 때는 공백 2칸·4칸·탭을 모두 인식한다.

### 날짜별 페이지
- 내일 페이지에 미리 쓸 수 있고, 자정이 지나면 노트가 새 '오늘'로 넘어간다.
- 지난 날 못 끝낸 일은 그 날짜에 남고, 오늘 페이지 위에 **가져오기** 배너가 뜬다.

### 완료 항목 보관
- ☰ → **완료 항목 보관**, 또는 날짜가 바뀌면 전날까지 완료한 항목을 자동 보관 (설정에서 끌 수 있음).
- 관리 창의 **보관됨** 필터에서 검색·내보내기·노트로 복원·완전 삭제.

### 텍스트 파일 불러오기 (보안)
- `- [ ]`, `- [x]`, `* [ ]` 형식의 줄만 할 일로 읽고, 내용은 글자 그대로만 보여준다 (HTML 해석 안 함).
- 파일은 1MB까지, 한 번에 200개까지 불러온다 (50개 넘으면 먼저 확인).

## 개발

Python은 [uv](https://docs.astral.sh/uv/)로 관리한다.

```powershell
winget install astral-sh.uv   # 최초 1회
uv sync                        # Python 3.12 + 의존성 설치
uv run sticky-todo             # 실행
uv run pytest                  # 테스트
uv run ruff check .            # 린트
uv run ruff format .           # 포맷
uv run pip-audit               # 의존성 취약점 점검
uv run python tools/build.py   # exe·설치 프로그램 빌드 → dist\ (Inno Setup 6 있으면 Setup.exe도)
```

`todos.json` 스키마는 v3 (각 할 일에 `day`, 보관함은 `archive`). v1·v2 파일은 읽을 때 자동 변환된다.

## 라이선스

MIT ([LICENSE](LICENSE)). 함께 묶인 PySide6(LGPL-3.0)·Pretendard(OFL-1.1) 등은 [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt) 참고.
