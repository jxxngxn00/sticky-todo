"""도움말 창 (☰ → 도움말 · 단축키, 또는 F1).

설정 창과 같은 모양: 탭 + 아래쪽 버튼 줄. 내용은 글자 그대로만 보여준다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

SHORTCUTS: list[tuple[str, str]] = [
    ("Enter", "입력칸: 할 일 추가 · 목록: 고르고 있는 할 일 고치기"),
    ("Tab / Shift+Tab", "하위 할 일로 / 상위로 꺼내기 (입력칸에서는 새 할 일의 단계)"),
    ("Space", "고른 할 일 완료 / 완료 취소"),
    ("F2", "고른 할 일 고치기"),
    ("Esc", "고치기 취소"),
    ("↑ / ↓", "할 일 고르기"),
    ("Delete", "고른 할 일 삭제 (하위 할 일 포함)"),
    ("Ctrl+Z", "방금 지우거나 보관한 것 되돌리기"),
    ("Alt+← / Alt+→", "이전 / 다음 날짜 (주간: 한 주, 월간: 한 달)"),
    ("Ctrl+1 / 2 / 3", "일 / 주 / 월 보기"),
    ("Ctrl+Shift+L", "전체 목록 보기 (검색·보관함)"),
    ("Ctrl+Alt+T", "어디서든 노트 불러오기 (다른 프로그램을 쓰는 중에도)"),
    ("F1", "이 도움말"),
]

MOUSE: list[tuple[str, str]] = [
    ("더블클릭", "할 일 고치기"),
    ("끌어다 놓기", "순서 바꾸기 · 좌우로 끌면 하위 할 일로 / 꺼내기"),
    ("다른 날로 끌기", "주간 보기의 다른 날, 보드의 다른 칸, 달력의 날짜 칸에 놓으면 그 날로"),
    ("우클릭", "고치기 · 완료 · 내일로 미루기 · 다른 날로 옮기기 · 보관 · 삭제"),
    ("날짜 누르기", "주간·월간: 그 날을 고름 (아래 입력칸이 그 날에 추가)"),
    ("날짜 더블클릭", "그 날 하루 보기로"),
    ("제목 더블클릭", "오늘로 돌아가기"),
    ("제목 바 끌기", "노트 옮기기 · 오른쪽 아래 모서리를 끌면 크기 조절"),
    ("✕", "트레이(작업표시줄 오른쪽 ^ 안)로 숨기기 · 아이콘을 누르면 다시 보임"),
]


def views_and_data(data_dir: Path) -> list[tuple[str, str]]:
    return [
        ("일 보기", "하루치 목록. ‹ ›로 다른 날, 내일 페이지에 미리 써 둘 수 있음"),
        ("주 보기", "일요일~토요일. 노트를 960px 이상으로 넓히면 가로 7칸 보드"),
        ("월 보기", "달력 + 고른 날 목록. 주황 점은 남은 일, 회색 점은 끝낸 일"),
        ("공휴일", "대한민국 공휴일은 달력에 빨간 숫자, 주간·하루 보기에 배지 (2026~2028년)"),
        ("지난 날 못 끝낸 일", "그 날짜에 남아 있고, 오늘 위 배너의 '가져오기'로 한 번에 가져옴"),
        ("완료 항목 보관", "☰ → 완료 항목 보관, 또는 날짜가 바뀌면 자동 (설정에서 끌 수 있음)"),
        ("보관함", "☰ → 전체 목록 보기 → '보관됨'에서 검색·복원·완전 삭제"),
        ("텍스트 파일", "☰ → 저장·복사는 보고 있는 범위 (하루 / 한 주 / 한 달)"),
        ("저장 위치", str(data_dir)),
        ("백업", "같은 폴더의 backups (최근 5개). 파일이 망가지면 자동으로 복구"),
        ("새 버전 알림", "하루 한 번 GitHub에 최신 버전 번호만 확인 (할 일은 안 보냄)"),
        ("업데이트 확인", "☰ → 업데이트 확인. 자동 확인은 설정에서 끌 수 있음"),
    ]


def _table(rows: list[tuple[str, str]], headers: tuple[str, str]) -> QTableWidget:
    t = QTableWidget(len(rows), 2)
    t.setHorizontalHeaderLabels(list(headers))
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setShowGrid(False)
    t.setWordWrap(True)
    t.setAlternatingRowColors(True)
    t.setStyleSheet("QTableWidget::item { padding: 4px 10px; }")  # 두 칸 글자가 붙지 않게
    for r, (key, desc) in enumerate(rows):
        k = QTableWidgetItem(key)
        k.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = k.font()
        f.setBold(True)
        k.setFont(f)
        d = QTableWidgetItem(desc)
        d.setFlags(Qt.ItemFlag.ItemIsEnabled)
        t.setItem(r, 0, k)
        t.setItem(r, 1, d)
    head = t.horizontalHeader()
    head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    t.resizeRowsToContents()
    return t


class HelpWindow(QDialog):
    def __init__(self, data_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sticky Todo 도움말")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(560, 480)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(_table(SHORTCUTS, ("단축키", "하는 일")), "단축키")
        self.tabs.addTab(_table(MOUSE, ("마우스", "하는 일")), "마우스로 하기")
        self.tabs.addTab(_table(views_and_data(data_dir), ("항목", "설명")), "보기와 데이터")

        # 설정 창과 같은 아래쪽 버튼 줄 (닫기만)
        self.close_btn = QPushButton("닫기")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        root.addLayout(buttons)
