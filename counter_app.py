import sys
import os
import json
import inspect
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QComboBox,
                             QMessageBox, QFrame, QGridLayout, QScrollArea, QLineEdit,
                             QGraphicsDropShadowEffect, QSizePolicy, QStatusBar)
from PyQt5.QtCore import QTimer, Qt, QEvent, QObject, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont, QColor, QDesktopServices
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient

from storage import Settings, StorageWriter, SETTINGS_FILE

# 실행 파일(exe)이 있는 폴더. 설정 파일과 CSV 로그를 이 폴더에 저장한다.
# (부팅 시 자동 실행되면 작업 폴더가 C:\Windows\System32 등으로 달라질 수 있으므로 고정)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = "counter_config.json"

# USB-시리얼 변환기(SCM-US48I) 시리얼 번호 → 장비 이름 표시는 settings.ini [devices] 에서 설정

RETRY_SEC = 10          # 자동 연결 실패 시 재시도 간격 (초)
READ_FAIL_LIMIT = 5     # 연속 읽기 실패가 이 횟수에 도달하면 포트를 다시 열어 재연결


def scan_ports():
    """현재 PC 의 COM 포트 목록 [(포트, 설명, USB 시리얼 번호)]"""
    return [(p.device, p.description, p.serial_number or "") for p in serial.tools.list_ports.comports()]


def serial_of(port):
    return next((sn for dev, _, sn in scan_ports() if dev == port), "")


def find_port_by_serial(serial_no):
    """USB 시리얼 번호로 현재 COM 포트 찾기 (COM 번호가 바뀌어도 같은 변환기를 찾음)"""
    if not serial_no:
        return None
    return next((dev for dev, _, sn in scan_ports() if sn and sn.upper() == serial_no.upper()), None)

# pymodbus 3.10 이상은 'slave' 대신 'device_id' 인자를 사용
_UNIT_KW = ("device_id" if "device_id" in inspect.signature(ModbusSerialClient.read_holding_registers).parameters
            else "slave")
POLL_INTERVAL_MS = 1000  # 데이터 읽기(화면 갱신) 주기 (settings.ini 의 poll_interval_ms 로 변경)
SAVE_INTERVAL_SEC = 30   # 저장 주기 (settings.ini 의 save_interval_sec 로 변경)
SAVE_ONLY_ON_CHANGE = True  # 값이 바뀌었을 때만 저장 (save_only_on_change)
KEEPALIVE_SEC = 3600     # 값이 그대로여도 이 간격마다 1건 저장, 0 = 사용 안 함 (keepalive_minutes)


def collecting_text(port):
    save = (f"{SAVE_INTERVAL_SEC}초 단위로 변화 시 저장" if SAVE_ONLY_ON_CHANGE
            else f"{SAVE_INTERVAL_SEC}초마다 저장")
    return f"{port} 연결됨 · {POLL_INTERVAL_MS / 1000:g}초 주기로 수집 중 · {save}"

CARD_MIN_WIDTH = 360

# ==============================================================================
# 2. 화면 스타일 (색상 / 폰트)
# ==============================================================================
COLORS = {
    "bg": "#F1F4F8",
    "card": "#FFFFFF",
    "border": "#E3E8EF",
    "text": "#1F2937",
    "muted": "#6B7280",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "success": "#16A34A",
    "success_bg": "#DCFCE7",
    "danger": "#DC2626",
    "danger_hover": "#B91C1C",
    "danger_bg": "#FEE2E2",
    "idle_bg": "#F3F4F6",
    "warn": "#D97706",
    "warn_bg": "#FEF3C7",
}

# 상태별 (배지 텍스트, 글자색, 배경색, 카운트 숫자색)
STATUS_STYLES = {
    "idle":      ("● 대기",   COLORS["muted"],   COLORS["idle_bg"],    "#9CA3AF"),
    "connecting": ("● 연결 중", COLORS["primary"], "#DBEAFE",           "#9CA3AF"),
    "connected": ("● 수집 중", COLORS["success"], COLORS["success_bg"], COLORS["primary"]),
    "warning":   ("● 응답 오류", COLORS["warn"],  COLORS["warn_bg"],    COLORS["warn"]),
    "error":     ("● 연결 실패", COLORS["danger"], COLORS["danger_bg"], "#9CA3AF"),
}

APP_STYLE = f"""
QMainWindow, QWidget#Central, QWidget#ScrollContent {{
    background-color: {COLORS['bg']};
}}
QWidget {{
    color: {COLORS['text']};
    font-size: 13px;
}}
QScrollArea {{ border: none; background: transparent; }}

QFrame#Card {{
    background-color: {COLORS['card']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
}}
QLabel#CardId {{ color: {COLORS['muted']}; font-size: 11px; font-weight: bold; }}
QLabel#FieldLabel {{ color: {COLORS['muted']}; font-size: 12px; }}
QLabel#Count {{
    font-family: "Consolas", "D2Coding", "Courier New", monospace;
    font-size: 46px;
    font-weight: bold;
    border-radius: 10px;
    padding: 14px 8px;
    background-color: #F8FAFC;
    border: 1px solid {COLORS['border']};
}}
QLabel#CountUnit {{ color: {COLORS['muted']}; font-size: 11px; }}
QLabel#StatusText {{ color: {COLORS['muted']}; font-size: 11px; }}
QLabel#Title {{ font-size: 20px; font-weight: bold; }}
QLabel#Subtitle {{ color: {COLORS['muted']}; font-size: 12px; }}
QLabel#Summary {{
    background-color: {COLORS['card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 6px 12px;
    color: {COLORS['text']};
}}

QLineEdit, QComboBox {{
    background-color: #FFFFFF;
    border: 1px solid #D1D5DB;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {COLORS['primary']}; }}
QLineEdit:disabled, QComboBox:disabled {{ background-color: #F3F4F6; color: {COLORS['muted']}; }}
QLineEdit#NameEdit {{
    font-size: 15px;
    font-weight: bold;
    border: 1px solid transparent;
    background: transparent;
    padding: 4px 4px;
}}
QLineEdit#NameEdit:hover {{ border: 1px solid #D1D5DB; background: #FFFFFF; }}
QLineEdit#NameEdit:focus {{ border: 1px solid {COLORS['primary']}; background: #FFFFFF; }}
QLineEdit#NameEdit:disabled {{ background: transparent; color: {COLORS['text']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

QPushButton {{
    background-color: #FFFFFF;
    border: 1px solid #D1D5DB;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: bold;
}}
QPushButton:hover {{ background-color: #F9FAFB; border-color: #9CA3AF; }}
QPushButton:pressed {{ background-color: #E5E7EB; }}
QPushButton:disabled {{ color: #9CA3AF; background-color: #F3F4F6; border-color: #E5E7EB; }}

QPushButton#Primary {{
    background-color: {COLORS['primary']}; color: white; border: none;
}}
QPushButton#Primary:hover {{ background-color: {COLORS['primary_hover']}; }}

QPushButton#Connect {{
    background-color: {COLORS['primary']}; color: white; border: none;
}}
QPushButton#Connect:hover {{ background-color: {COLORS['primary_hover']}; }}
QPushButton#Connect[connected="true"] {{
    background-color: #FFFFFF; color: {COLORS['text']}; border: 1px solid #D1D5DB;
}}
QPushButton#Connect[connected="true"]:hover {{ background-color: #F9FAFB; }}

QPushButton#Danger {{
    background-color: #FFFFFF; color: {COLORS['danger']}; border: 1px solid #FCA5A5;
}}
QPushButton#Danger:hover {{ background-color: {COLORS['danger_bg']}; }}
QPushButton#Danger:disabled {{ color: #D1A1A1; border-color: #F3D4D4; background-color: #FFFFFF; }}

QPushButton#Icon {{
    padding: 4px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
    border: 1px solid transparent; background: transparent; color: {COLORS['muted']};
    font-size: 14px;
}}
QPushButton#Icon:hover {{ background-color: {COLORS['idle_bg']}; color: {COLORS['text']}; }}
QPushButton#IconDanger {{
    padding: 4px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
    border: 1px solid transparent; background: transparent; color: {COLORS['muted']};
    font-size: 14px;
}}
QPushButton#IconDanger:hover {{ background-color: {COLORS['danger_bg']}; color: {COLORS['danger']}; }}

QStatusBar {{ background-color: {COLORS['card']}; border-top: 1px solid {COLORS['border']}; color: {COLORS['muted']}; }}
QToolTip {{ background-color: {COLORS['text']}; color: white; border: none; padding: 4px 6px; }}
"""


def repolish(widget):
    """동적 속성 변경 후 스타일 재적용"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ModbusWorker(QObject):
    """별도 스레드에서 Modbus 통신(연결/주기 읽기/리셋)을 담당.
    UI 스레드와는 시그널로만 주고받으므로 통신 지연이 화면을 멈추지 않는다."""
    connect_result = pyqtSignal(bool, str)   # 성공 여부, 포트
    count_read = pyqtSignal(object)  # Qt int(32비트)로는 큰 값이 넘쳐서 object 사용
    read_failed = pyqtSignal(str)
    reset_result = pyqtSignal(bool)

    def __init__(self, settings):
        super().__init__()
        self.cfg = settings
        self.client = None
        self.timer = None

    def comm_text(self):
        c = self.cfg
        return f"{c.mb_baudrate}/{c.mb_parity}/8/{c.mb_stopbits}, 국번 {c.mb_slave_id}"

    @pyqtSlot(str)
    def open_port(self, port):
        # 타이머는 워커 스레드 안에서 생성해야 해당 스레드에서 동작함
        if self.timer is None:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.poll)
        self.timer.stop()
        self.close_client()

        # 통신 설정은 settings.ini [modbus] (CT 시리즈 기본: 9600, Even, Data 8, Stop 1)
        client = ModbusSerialClient(
            port=port,
            baudrate=self.cfg.mb_baudrate,
            parity=self.cfg.mb_parity,
            stopbits=self.cfg.mb_stopbits,
            bytesize=8,
            timeout=1
        )
        try:
            ok = client.connect()
        except Exception:
            ok = False

        if ok:
            self.client = client
            self.connect_result.emit(True, port)
            self.poll()
            self.timer.start(POLL_INTERVAL_MS)
        else:
            client.close()
            self.connect_result.emit(False, port)

    @pyqtSlot()
    def poll(self):
        if not self.client:
            return
        try:
            # 현재값(PV) 레지스터 2개 읽기 (32비트). 주소/종류/워드 순서는 settings.ini [modbus]
            c = self.cfg
            read = (self.client.read_input_registers if c.mb_register_type == "input"
                    else self.client.read_holding_registers)
            response = read(address=c.mb_pv_address, count=2, **{_UNIT_KW: c.mb_slave_id})
            if not response.isError():
                r0, r1 = response.registers[0], response.registers[1]
                value = (r1 << 16) | r0 if c.mb_word_order == "low_first" else (r0 << 16) | r1
                if value >= 0x80000000:  # 32비트 부호 있는 값 (UP/DOWN 카운트 시 음수 가능)
                    value -= 0x100000000
                self.count_read.emit(value)
            else:
                self.read_failed.emit(f"장비 응답 오류 — 통신 설정({self.comm_text()})과 "
                                      f"레지스터 주소를 확인하세요.")
        except Exception as e:
            self.read_failed.emit(f"오류: {str(e)}")

    @pyqtSlot()
    def reset(self):
        if not self.client:
            self.reset_result.emit(False)
            return
        try:
            # 리셋 Coil 에 ON 전송 -> RESET 실행 (주소는 settings.ini [modbus] reset_coil_address)
            response = self.client.write_coil(address=self.cfg.mb_reset_coil, value=True,
                                              **{_UNIT_KW: self.cfg.mb_slave_id})
            ok = not response.isError()
        except Exception:
            ok = False
        self.reset_result.emit(ok)
        if ok:
            self.poll()

    @pyqtSlot()
    def close_port(self):
        if self.timer:
            self.timer.stop()
        self.close_client()

    @pyqtSlot()
    def stop(self):
        """포트를 닫고 워커 스레드의 이벤트 루프 종료 (앞서 요청된 작업이 끝난 뒤 실행됨)"""
        if self.timer:
            self.timer.stop()
        self.close_client()
        self.thread().quit()

    def close_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None


class CounterCard(QFrame):
    """개별 카운터 장비를 표시하고 제어하는 카드 위젯"""
    # 워커 스레드로 작업을 요청하는 시그널 (큐 연결로 워커 스레드에서 실행됨)
    request_open = pyqtSignal(str)
    request_reset = pyqtSignal()
    request_close = pyqtSignal()
    request_stop = pyqtSignal()

    def __init__(self, card_id, parent_app, default_name="", default_port="", serial_no=""):
        super().__init__()
        self.setObjectName("Card")
        self.card_id = card_id
        self.parent_app = parent_app
        self.state = "idle"
        self.running = False     # 연결되어 수집 중인지 여부
        self.connecting = False  # 연결 시도 중 여부
        self.want_connect = False  # 사용자가 연결을 원하는 상태인지 (다음 실행 시 자동 연결 여부)
        self.port = None
        self.save_slot = None    # 마지막으로 확인한 저장 주기 구간 번호
        self.last_saved = None   # 마지막으로 저장한 (값, 시각)
        self.last_read = None    # 마지막으로 읽은 (값, 시각)
        self.serial = serial_no or ""  # 마지막으로 연결에 성공한 USB 변환기 시리얼 번호
        self.auto_retry = False  # 연결 실패/끊김 시 자동 재연결 여부 (자동 연결이거나 한 번 연결에 성공한 경우)
        self.read_fails = 0      # 연속 읽기 실패 횟수
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.retry_connect)

        # 저장된 시리얼 번호의 변환기가 다른 COM 번호로 잡혀 있으면 그 포트를 선택
        found = find_port_by_serial(self.serial)
        if found and default_port and found != default_port:
            QTimer.singleShot(0, lambda: self.parent_app.show_message(
                f"[{self.txt_name.text()}] COM 번호 변경 감지: {default_port} → {found} (S/N {self.serial})", 15000))
        self.initUI(default_name, found or default_port)
        self.init_worker()

    def init_worker(self):
        # 카드가 먼저 삭제되어도 스레드가 안전하게 끝날 수 있도록 메인 창을 부모로 둠
        self.thread = QThread(self.parent_app)
        self.worker = ModbusWorker(self.parent_app.settings)
        self.worker.moveToThread(self.thread)

        self.request_open.connect(self.worker.open_port)
        self.request_reset.connect(self.worker.reset)
        self.request_close.connect(self.worker.close_port)
        self.request_stop.connect(self.worker.stop)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.connect_result.connect(self.on_connect_result)
        self.worker.count_read.connect(self.on_count_read)
        self.worker.read_failed.connect(self.on_read_failed)
        self.worker.reset_result.connect(self.on_reset_result)

        self.thread.start()

    def shutdown(self):
        """통신 스레드 종료 요청 (진행 중인 통신이 끝나면 포트를 닫고 스스로 종료)"""
        self.running = False
        self.connecting = False
        self.retry_timer.stop()
        self.request_stop.emit()

    def initUI(self, default_name, default_port):
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(15, 23, 42, 25))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        # 1. 헤더: 장비 번호 / 이름 / 상태 배지 / 삭제
        header = QHBoxLayout()
        header.setSpacing(8)
        lbl_id = QLabel(f"#{self.card_id}")
        lbl_id.setObjectName("CardId")

        self.txt_name = QLineEdit(default_name if default_name else f"카운터_{self.card_id}")
        self.txt_name.setObjectName("NameEdit")
        self.txt_name.setPlaceholderText("장비 이름")
        self.txt_name.setToolTip("클릭하여 장비 이름 수정")

        self.lbl_badge = QLabel()
        self.lbl_badge.setAlignment(Qt.AlignCenter)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setObjectName("IconDanger")
        self.btn_delete.setToolTip("이 카운터 삭제")
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.clicked.connect(self.confirm_delete)

        header.addWidget(lbl_id)
        header.addWidget(self.txt_name, 1)
        header.addWidget(self.lbl_badge)
        header.addWidget(self.btn_delete)

        # 2. 포트 선택
        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        lbl_port = QLabel("COM 포트")
        lbl_port.setObjectName("FieldLabel")

        self.port_combo = QComboBox()
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.refresh_ports(prefer=default_port)  # 이전 저장 포트 선택

        self.btn_refresh = QPushButton("⟳")
        self.btn_refresh.setObjectName("Icon")
        self.btn_refresh.setToolTip("포트 목록 새로고침")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(lambda: self.refresh_ports())

        port_row.addWidget(lbl_port)
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.btn_refresh)

        # 3. 실시간 카운트 수 표시
        count_box = QVBoxLayout()
        count_box.setSpacing(4)
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("Count")
        self.lbl_count.setAlignment(Qt.AlignCenter)
        self.lbl_count.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.lbl_updated = QLabel("마지막 수신: -")
        self.lbl_updated.setObjectName("CountUnit")
        self.lbl_updated.setAlignment(Qt.AlignRight)
        count_box.addWidget(self.lbl_count)
        count_box.addWidget(self.lbl_updated)

        # 4. 제어 버튼들
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        self.btn_connect = QPushButton("▶  연결 시작")
        self.btn_connect.setObjectName("Connect")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.clicked.connect(self.toggle_connection)

        self.btn_reset = QPushButton("↺  0 리셋")
        self.btn_reset.setObjectName("Danger")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setToolTip("카운터 값을 0으로 초기화 (연결 중에만 가능)")
        self.btn_reset.clicked.connect(self.reset_counter)

        btn_layout.addWidget(self.btn_connect, 2)
        btn_layout.addWidget(self.btn_reset, 1)

        # 5. 상태 표시
        self.lbl_status = QLabel("대기 중")
        self.lbl_status.setObjectName("StatusText")
        self.lbl_status.setWordWrap(True)

        layout.addLayout(header)
        layout.addLayout(port_row)
        layout.addLayout(count_box)
        layout.addLayout(btn_layout)
        layout.addWidget(self.lbl_status)

        self.set_state("idle", "대기 중")

    def set_state(self, state, message=None):
        """상태 배지 / 카운트 색상 / 버튼 활성화 상태를 일괄 갱신"""
        self.state = state
        text, fg, bg, count_color = STATUS_STYLES[state]
        self.lbl_badge.setText(text)
        self.lbl_badge.setStyleSheet(
            f"color: {fg}; background-color: {bg}; border-radius: 10px; "
            f"padding: 3px 10px; font-size: 11px; font-weight: bold;")
        self.lbl_count.setStyleSheet(f"color: {count_color};")
        if message is not None:
            self.lbl_status.setText(message)

        running = self.running
        active = running or self.retry_timer.isActive()  # 수집 중 또는 재연결 대기 중
        locked = active or self.connecting
        self.btn_reset.setEnabled(running)
        self.btn_connect.setEnabled(not self.connecting)
        self.port_combo.setEnabled(not locked)
        self.btn_refresh.setEnabled(not locked)
        self.txt_name.setEnabled(not locked)
        self.btn_connect.setText("■  연결 끊기" if active else "▶  연결 시작")
        self.btn_connect.setProperty("connected", "true" if active else "false")
        repolish(self.btn_connect)

        self.parent_app.update_summary()

    def refresh_ports(self, prefer=None):
        """COM 포트 검색. settings.ini [devices] 에 등록된 시리얼 번호는 장비 이름으로 표시"""
        target = prefer if prefer else self.port_combo.currentData()
        self.port_combo.clear()
        devices = self.parent_app.settings.devices

        for dev, desc, sn in scan_ports():
            # [devices] 에 등록된 시리얼 번호인지 검사
            matched_name = next((name for key, name in devices.items() if sn and key in sn.upper()), None)
            display_text = f"[{matched_name}] {dev}" if matched_name else f"{dev} ({desc})"
            if self.serial and sn and sn.upper() == self.serial.upper():
                display_text += "  ★"  # 이 카드가 기억하는 변환기

            self.port_combo.addItem(display_text, dev)
            self.port_combo.setItemData(self.port_combo.count() - 1,
                                        f"{dev}\n{desc}\nS/N: {sn or '-'}", Qt.ToolTipRole)

            # 시리얼 번호로 감지된 이름이 있다면 장비 이름 입력창에 자동 반영 (초기값 설정용)
            if matched_name and not self.txt_name.text().strip():
                self.txt_name.setText(matched_name)

        # 저장된 포트가 지금 없으면 (USB 미연결 등) 목록에 표시만 해 두고 선택 유지
        if target and self.port_combo.findData(target) < 0:
            self.port_combo.addItem(f"{target} (현재 연결 안 됨)", target)

        if self.port_combo.count() == 0:
            self.port_combo.addItem("감지된 COM 포트 없음", None)

        if target:
            self.port_combo.setCurrentIndex(self.port_combo.findData(target))

    def toggle_connection(self):
        if self.running or self.retry_timer.isActive():
            self.stop_connection()
        elif not self.connecting:
            self.start_connection()

    def start_connection(self, auto=False):
        """auto=True: 프로그램 시작 시 자동 연결 / 자동 재연결.
        이때는 기억해 둔 시리얼 번호로 COM 포트를 다시 찾고, 실패하면 RETRY_SEC 후 재시도한다."""
        self.retry_timer.stop()
        if auto:
            self.want_connect = True
            found = find_port_by_serial(self.serial)
            current = self.port_combo.currentData()
            if found and found != current:
                self.refresh_ports(prefer=found)
                self.parent_app.show_message(
                    f"[{self.txt_name.text()}] COM 번호 변경 감지: {current} → {found} (S/N {self.serial})", 15000)
            else:
                self.refresh_ports(prefer=current)
        self.auto_retry = auto

        port = self.port_combo.currentData()
        if not port:
            self.fail_or_retry("COM 포트를 선택해 주세요.")
            return False
        # 하나의 COM 포트는 한 카드만 사용할 수 있음
        other = next((c for c in self.parent_app.cards
                      if c is not self and c.port == port and (c.running or c.connecting)), None)
        if other:
            self.fail_or_retry(f"{port} 는 #{other.card_id} [{other.txt_name.text()}] 카드가 사용 중입니다. "
                               f"다른 COM 포트를 선택하세요.")
            return False

        self.port = port
        self.read_fails = 0
        # 연결 후 첫 값은 바로 저장
        self.save_slot = None
        self.last_saved = None
        self.last_read = None
        self.want_connect = True
        self.connecting = True
        self.set_state("connecting", f"{port} 연결 시도 중...")
        self.request_open.emit(port)
        return True

    def fail_or_retry(self, message):
        """연결 실패 표시. 자동 재연결 대상이면 RETRY_SEC 후 다시 시도"""
        if self.auto_retry and self.want_connect:
            self.retry_timer.start(RETRY_SEC * 1000)
            message += f"  ({RETRY_SEC}초 후 다시 시도)"
        self.set_state("error", message)

    def retry_connect(self):
        if self.want_connect and not (self.running or self.connecting):
            self.start_connection(auto=True)

    def stop_connection(self):
        self.save_last_read()
        was_active = self.running or self.connecting or self.retry_timer.isActive()
        self.retry_timer.stop()
        self.auto_retry = False
        self.want_connect = False
        self.running = False
        self.connecting = False
        if was_active:
            self.request_close.emit()
        self.set_state("idle", "연결 해제됨")
        if was_active:
            self.parent_app.save_config()

    @pyqtSlot(bool, str)
    def on_connect_result(self, ok, port):
        if not self.connecting or port != self.port:
            # 사용자가 연결 시도 중에 취소한 경우 등 - 결과 무시
            return
        self.connecting = False
        if ok:
            self.running = True
            self.auto_retry = True  # 한 번 연결에 성공하면 이후 끊겨도 자동 재연결
            self.serial = serial_of(port) or self.serial  # 이 카드의 변환기 기억
            self.refresh_ports(prefer=port)
            self.set_state("connected", collecting_text(port))
            self.parent_app.save_config()
        else:
            self.fail_or_retry(f"{port} 연결 실패 — 케이블/포트 사용 여부를 확인하세요.")

    @pyqtSlot(object)
    def on_count_read(self, count_value):
        if not self.running:
            return  # 연결 해제 직후 도착한 이전 결과 무시
        now = datetime.now()
        self.read_fails = 0
        self.lbl_count.setText(f"{count_value:,}")
        self.lbl_updated.setText(f"마지막 수신: {now.strftime('%H:%M:%S')}")
        # 저장 주기 구간(예: 30초 → 매 분 0초~29초, 30초~59초)이 바뀔 때 저장 여부 판단
        self.last_read = (count_value, now)
        slot = int(now.timestamp()) // SAVE_INTERVAL_SEC
        if slot != self.save_slot:
            self.save_slot = slot
            if self.should_save(count_value, now):
                self.save_record(count_value, now)
        if self.state != "connected":
            self.set_state("connected")
        self.lbl_status.setText(collecting_text(self.port))

    def should_save(self, count_value, now):
        if not SAVE_ONLY_ON_CHANGE or self.last_saved is None:
            return True
        last_value, last_ts = self.last_saved
        if count_value != last_value:  # 증가 또는 리셋으로 감소
            return True
        # 값이 그대로여도 일정 시간마다 1건 저장 (생존 신호)
        return KEEPALIVE_SEC > 0 and (now - last_ts).total_seconds() >= KEEPALIVE_SEC

    def save_record(self, count_value, ts):
        # 실제 저장(CSV / DB)은 저장 스레드에서 처리
        self.parent_app.storage.put(self.txt_name.text().strip(), self.port, count_value, ts)
        self.last_saved = (count_value, ts)

    def save_last_read(self):
        """연결 해제·종료 시 마지막으로 읽은 값을 저장 (수집 종료 시점 기록)"""
        if self.last_read and self.port and (self.last_saved is None or self.last_read[1] > self.last_saved[1]):
            self.save_record(*self.last_read)
        self.last_read = None

    @pyqtSlot(str)
    def on_read_failed(self, message):
        if not self.running:
            return
        self.read_fails += 1
        if self.read_fails >= READ_FAIL_LIMIT and self.auto_retry:
            # USB 분리 등으로 계속 실패하면 포트를 닫고 다시 연결 (COM 번호가 바뀌었으면 시리얼 번호로 찾음)
            self.save_last_read()
            self.running = False
            self.read_fails = 0
            self.request_close.emit()
            self.retry_timer.start(3000)
            self.set_state("error", f"응답 없음 {READ_FAIL_LIMIT}회 — 포트를 다시 열어 재연결합니다.")
            return
        self.set_state("warning", message)

    def reset_counter(self):
        if not self.running:
            QMessageBox.warning(self, "경고", "연결된 상태에서만 리셋이 가능합니다.")
            return

        reply = QMessageBox.question(self, '확인', f"[{self.txt_name.text()}] 카운트 수치를 0으로 초기화하시겠습니까?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes and self.running:
            self.btn_reset.setEnabled(False)
            self.request_reset.emit()

    @pyqtSlot(bool)
    def on_reset_result(self, ok):
        self.btn_reset.setEnabled(self.running)
        if ok:
            self.parent_app.show_message(f"[{self.txt_name.text()}] 0으로 리셋되었습니다.")
        else:
            QMessageBox.critical(self, "오류", "리셋 전송에 실패했습니다.")

    def confirm_delete(self):
        reply = QMessageBox.question(self, '삭제 확인', f"[{self.txt_name.text()}] 카운터를 목록에서 삭제하시겠습니까?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.parent_app.remove_counter_card(self)

    def get_config(self):
        return {
            "name": self.txt_name.text(),
            "port": self.port_combo.currentData(),
            "serial": self.serial,
            "auto_connect": self.want_connect
        }


class MainWindow(QMainWindow):
    # 저장 스레드 → 화면 상태 표시 (텍스트, 수준: ok / warn / error)
    storage_status = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        global POLL_INTERVAL_MS, SAVE_INTERVAL_SEC, SAVE_ONLY_ON_CHANGE, KEEPALIVE_SEC
        self.settings = Settings()
        POLL_INTERVAL_MS = self.settings.poll_interval_ms
        SAVE_INTERVAL_SEC = self.settings.save_interval_sec
        SAVE_ONLY_ON_CHANGE = self.settings.save_only_on_change
        KEEPALIVE_SEC = self.settings.keepalive_minutes * 60
        self.storage = StorageWriter(self.settings, on_status=self.storage_status.emit)

        self.cards = []
        self.next_card_id = 1
        self.loading = True  # 설정 불러오는 중에는 중간 저장 금지
        self.columns = 2
        self.initUI()
        self.storage_status.connect(self.on_storage_status)
        self.storage.start()
        self.load_config_and_autoconnect()

        # 상단 시계 갱신
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()

    def initUI(self):
        self.setWindowTitle("오토닉스 다중 카운터 실시간 모니터링 프로그램")
        self.resize(980, 680)
        self.setMinimumSize(460, 420)

        main_widget = QWidget()
        main_widget.setObjectName("Central")
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(20, 18, 20, 10)
        main_layout.setSpacing(14)

        # 상단 헤더: 제목 / 요약 / 제어 버튼
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_title = QLabel("카운터 모니터링")
        lbl_title.setObjectName("Title")
        self.lbl_clock = QLabel()
        self.lbl_clock.setObjectName("Subtitle")
        title_box.addWidget(lbl_title)

        self.lbl_summary = QLabel()
        self.lbl_summary.setObjectName("Summary")

        btn_refresh_all = QPushButton("⟳  포트 새로고침")
        btn_refresh_all.setCursor(Qt.PointingHandCursor)
        btn_refresh_all.setToolTip("연결되지 않은 모든 카드의 COM 포트 목록을 다시 검색합니다.")
        btn_refresh_all.clicked.connect(self.refresh_all_ports)

        btn_settings = QPushButton("⚙  환경설정")
        btn_settings.setCursor(Qt.PointingHandCursor)
        btn_settings.setToolTip(f"{SETTINGS_FILE} 파일을 엽니다. (저장 방식 CSV/DB, DB 접속 정보 등)\n"
                                "수정 후 프로그램을 다시 시작해야 적용됩니다.")
        btn_settings.clicked.connect(self.open_settings)

        btn_add = QPushButton("+  카운터 추가")
        btn_add.setObjectName("Primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(lambda: self.add_counter_card())

        top_bar.addLayout(title_box)
        top_bar.addStretch()
        top_bar.addWidget(btn_settings)
        top_bar.addWidget(btn_refresh_all)
        top_bar.addWidget(btn_add)

        # 요약 바: 현재 시각 / 연결 현황
        info_bar = QHBoxLayout()
        info_bar.addWidget(self.lbl_clock)
        info_bar.addStretch()
        info_bar.addWidget(self.lbl_summary)

        # 스크롤 가능한 카드 배치 영역
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("ScrollContent")
        self.grid_layout = QGridLayout(self.scroll_content)
        self.grid_layout.setContentsMargins(6, 6, 6, 6)
        self.grid_layout.setSpacing(16)
        self.grid_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.scroll_content)

        self.scroll.viewport().installEventFilter(self)

        main_layout.addLayout(top_bar)
        main_layout.addLayout(info_bar)
        main_layout.addWidget(self.scroll)

        self.setStatusBar(QStatusBar())
        self.lbl_storage = QLabel()
        self.lbl_storage.setObjectName("StorageStatus")
        self.statusBar().addWidget(self.lbl_storage, 1)
        self.lbl_station = QLabel(f"PC: {self.settings.station}")
        self.lbl_station.setObjectName("StatusText")
        self.statusBar().addPermanentWidget(self.lbl_station)
        if self.settings.errors:
            self.on_storage_status("설정 오류: " + " / ".join(self.settings.errors), "error")
        if self.settings.added:
            names = ", ".join(self.settings.added)
            QTimer.singleShot(0, lambda: self.show_message(
                f"{SETTINGS_FILE} 에 새 설정 항목을 추가했습니다 (기본값): {names}", 20000))

    @pyqtSlot(str, str)
    def on_storage_status(self, text, level):
        color = {"ok": COLORS["muted"], "warn": COLORS["warn"], "error": COLORS["danger"]}[level]
        if self.settings.errors and not text.startswith("설정 오류"):
            text = "설정 오류: " + " / ".join(self.settings.errors) + "   |   " + text
            color = COLORS["danger"]
        self.lbl_storage.setStyleSheet(f"color: {color}; padding: 0 4px;")
        self.lbl_storage.setText(text)
        self.lbl_storage.setToolTip(text)

    def open_settings(self):
        path = os.path.abspath(SETTINGS_FILE)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.information(self, "환경설정", f"설정 파일 위치:\n{path}")
        self.show_message("환경설정을 수정한 뒤 프로그램을 다시 시작해야 적용됩니다.", 10000)

    def update_clock(self):
        self.lbl_clock.setText(datetime.now().strftime("%Y-%m-%d (%a) %H:%M:%S"))

    def update_summary(self):
        if not hasattr(self, "lbl_summary"):
            return
        total = len(self.cards)
        connected = sum(1 for c in self.cards if c.state == "connected")
        problem = sum(1 for c in self.cards if c.state in ("warning", "error"))
        text = f"<span style='color:{COLORS['success']}'>●</span> 수집 중 <b>{connected}</b> / 전체 <b>{total}</b>"
        if problem:
            text += f"&nbsp;&nbsp;<span style='color:{COLORS['danger']}'>●</span> 이상 <b>{problem}</b>"
        self.lbl_summary.setText(text)

    def show_message(self, text, timeout=5000):
        self.statusBar().showMessage(text, timeout)

    def add_counter_card(self, name="", port="", auto_connect=False, serial_no=""):
        card = CounterCard(self.next_card_id, self, default_name=name, default_port=port, serial_no=serial_no)
        self.cards.append(card)
        self.next_card_id += 1
        self.rearrange_grid()
        self.update_summary()
        self.save_config()

        # 이전 상태가 연결 중이었다면 자동 연결 수행
        if auto_connect:
            card.start_connection(auto=True)

    def remove_counter_card(self, card):
        card.stop_connection()
        card.shutdown()  # 스레드는 백그라운드에서 정리되므로 화면이 멈추지 않음
        self.grid_layout.removeWidget(card)
        self.cards.remove(card)
        card.deleteLater()
        self.rearrange_grid()
        self.update_summary()
        self.save_config()

    def rearrange_grid(self):
        for card in self.cards:
            self.grid_layout.removeWidget(card)
        for c in range(self.grid_layout.columnCount()):
            self.grid_layout.setColumnStretch(c, 0)
        for i, card in enumerate(self.cards):
            self.grid_layout.addWidget(card, i // self.columns, i % self.columns)
        for c in range(self.columns):
            self.grid_layout.setColumnStretch(c, 1)

    def eventFilter(self, obj, event):
        # 창 너비에 맞춰 한 줄에 표시할 카드 수를 자동 조절
        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            self.update_columns()
        return super().eventFilter(obj, event)

    def update_columns(self):
        available = self.scroll.viewport().width() - 8
        spacing = self.grid_layout.spacing()
        columns = max(1, (available + spacing) // (CARD_MIN_WIDTH + spacing))
        if columns != self.columns:
            self.columns = columns
            self.rearrange_grid()

    def refresh_all_ports(self):
        for card in self.cards:
            if not (card.running or card.connecting):
                card.refresh_ports()
        self.show_message("COM 포트 목록을 새로고침했습니다.")

    def save_config(self):
        # 카드 추가/삭제, 연결/해제 시마다 저장 → 강제 종료·정전 후 재부팅해도 마지막 상태로 복구
        if self.loading:
            return
        config_data = []
        for card in self.cards:
            config_data.append(card.get_config())

        try:
            tmp_file = CONFIG_FILE + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_file, CONFIG_FILE)
        except OSError as e:
            self.show_message(f"설정 저장 실패: {e}")

    def load_config_and_autoconnect(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)

                for item in config_data:
                    self.add_counter_card(
                        name=item.get("name", ""),
                        port=item.get("port", ""),
                        auto_connect=item.get("auto_connect", False),
                        serial_no=item.get("serial", "")
                    )
            except Exception as e:
                print(f"설정 로드 오류: {e}")

        self.loading = False
        if not self.cards:
            self.add_counter_card()

    def closeEvent(self, event):
        self.save_config()
        self.clock_timer.stop()
        # 모든 스레드에 종료를 먼저 요청한 뒤 한꺼번에 대기 (종료 시간 단축)
        for card in self.cards:
            card.save_last_read()
            card.shutdown()
        # 삭제된 카드의 정리 중인 스레드까지 포함해 모든 통신 스레드 종료 대기
        for thread in self.findChildren(QThread):
            thread.wait(8000)
        # 남은 수집 데이터 저장 후 저장 스레드 종료
        self.storage.stop()
        event.accept()

if __name__ == "__main__":
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    os.chdir(APP_DIR)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("맑은 고딕", 10))
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
