import sys
import os
import json
import csv
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QComboBox, 
                             QMessageBox, QGroupBox, QGridLayout, QScrollArea, QLineEdit)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient

CONFIG_FILE = "counter_config.json"

# ==============================================================================
# 1. SCM-US48I 시리얼 번호와 기계 이름 매핑 테이블
# (실제 보유하신 SCM-US48I의 시리얼 번호로 수정해서 사용하시면 됩니다)
# ==============================================================================
DEVICE_MAP = {
    "FT9X123A": "1번 라인 카운터",
    "FT9X123B": "2번 라인 카운터",
    "FT9X123C": "3번 라인 카운터"
}

class CounterCard(QGroupBox):
    """개별 카운터 장비를 표시하고 제어하는 카드 위젯"""
    def __init__(self, card_id, parent_app, default_name="", default_port=""):
        super().__init__(f"카운터 장비 #{card_id}")
        self.card_id = card_id
        self.parent_app = parent_app
        
        self.client = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_counter)
        
        self.initUI(default_name, default_port)

    def initUI(self, default_name, default_port):
        layout = QVBoxLayout()
        
        # 1. 장비 이름 및 포트 설정
        top_layout = QHBoxLayout()
        self.txt_name = QLineEdit(default_name if default_name else f"카운터_{self.card_id}")
        self.txt_name.setPlaceholderText("장비 이름")
        
        self.port_combo = QComboBox()
        self.refresh_ports()
        
        # 이전 저장 포트 선택
        if default_port:
            index = self.port_combo.findData(default_port)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

        top_layout.addWidget(QLabel("이름:"))
        top_layout.addWidget(self.txt_name)
        top_layout.addWidget(QLabel("포트:"))
        top_layout.addWidget(self.port_combo)
        
        # 2. 실시간 카운트 수 표시
        self.lbl_count = QLabel("0")
        self.lbl_count.setAlignment(Qt.AlignCenter)
        self.lbl_count.setFont(QFont("Arial", 36, QFont.Bold))
        self.lbl_count.setStyleSheet("color: #007ACC; background-color: #F8F9FA; border: 1px solid #DEE2E6; border-radius: 8px; padding: 10px;")
        
        # 3. 제어 버튼들
        btn_layout = QHBoxLayout()
        self.btn_connect = QPushButton("연결 시작")
        self.btn_connect.clicked.connect(self.toggle_connection)
        
        self.btn_reset = QPushButton("0 리셋")
        self.btn_reset.setStyleSheet("background-color: #E74C3C; color: white; font-weight: bold;")
        self.btn_reset.clicked.connect(self.reset_counter)
        
        self.btn_delete = QPushButton("삭제")
        self.btn_delete.clicked.connect(lambda: self.parent_app.remove_counter_card(self))
        
        btn_layout.addWidget(self.btn_connect)
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addWidget(self.btn_delete)
        
        # 4. 상태 표시
        self.lbl_status = QLabel("상태: 대기 중")
        self.lbl_status.setStyleSheet("color: #6C757D; font-size: 11px;")
        
        layout.addLayout(top_layout)
        layout.addWidget(self.lbl_count)
        layout.addLayout(btn_layout)
        layout.addWidget(self.lbl_status)
        self.setLayout(layout)

    def refresh_ports(self):
        """COM 포트 검색 및 DEVICE_MAP 기반 이름 자동 매핑"""
        current_data = self.port_combo.currentData()
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        
        for p in ports:
            sn = p.serial_number  # USB 고유 시리얼 번호
            matched_name = None
            
            # DEVICE_MAP에 등록된 시리얼 번호인지 검사
            if sn:
                for target_sn, dev_name in DEVICE_MAP.items():
                    if target_sn in sn:
                        matched_name = dev_name
                        break
            
            # 표시 텍스트 생성
            if matched_name:
                display_text = f"[{matched_name}] {p.device}"
            else:
                display_text = f"{p.device} ({p.description})"
                
            self.port_combo.addItem(display_text, p.device)
            
            # 시리얼 번호로 감지된 이름이 있다면 장비 이름 입력창에 자동 반영 (초기값 설정용)
            if matched_name and not self.txt_name.text().strip():
                self.txt_name.setText(matched_name)
            
        if current_data:
            index = self.port_combo.findData(current_data)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def toggle_connection(self):
        if self.timer.isActive():
            self.stop_connection()
        else:
            self.start_connection()

    def start_connection(self):
        port = self.port_combo.currentData()
        if not port:
            self.lbl_status.setText("상태: COM 포트 없음")
            return False
            
        # CT6Y 기본 통신 설정 (9600, Even, Data 8, Stop 1)
        self.client = ModbusSerialClient(
            port=port,
            baudrate=9600,
            parity='E',
            stopbits=1,
            bytesize=8,
            timeout=1
        )
        
        if self.client.connect():
            self.timer.start(1000) # 1초 주기 데이터 읽기
            self.btn_connect.setText("연결 끊기")
            self.btn_connect.setStyleSheet("background-color: #2ECC71; color: white;")
            self.lbl_status.setText(f"상태: {port} 연결됨 (수집 중)")
            self.port_combo.setEnabled(False)
            self.txt_name.setEnabled(False)
            return True
        else:
            self.lbl_status.setText(f"상태: {port} 연결 실패")
            return False

    def stop_connection(self):
        self.timer.stop()
        if self.client:
            self.client.close()
        self.btn_connect.setText("연결 시작")
        self.btn_connect.setStyleSheet("")
        self.lbl_status.setText("상태: 연결 해제됨")
        self.port_combo.setEnabled(True)
        self.txt_name.setEnabled(True)

    def update_counter(self):
        if not self.client:
            return
            
        try:
            # Holding Register 0000번지부터 2개 읽기 (32비트 카운트 값, Slave ID=1)
            response = self.client.read_holding_registers(address=0, count=2, slave=1)
            if not response.isError():
                high = response.registers[0]
                low = response.registers[1]
                count_value = (high << 16) | low
                
                self.lbl_count.setText(f"{count_value:,}")
                self.save_to_csv(count_value)
                self.lbl_status.setText(f"상태: 수집 중 ({datetime.now().strftime('%H:%M:%S')})")
            else:
                self.lbl_status.setText("상태: 데이터 응답 오류")
        except Exception as e:
            self.lbl_status.setText(f"오류: {str(e)}")

    def save_to_csv(self, count):
        device_name = self.txt_name.text().strip().replace(" ", "_")
        filename = f"counter_log_{device_name}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        file_exists = os.path.exists(filename)
        with open(filename, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["일시", "장비명", "COM포트", "카운트 수"])
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.txt_name.text(), self.port_combo.currentData(), count])

    def reset_counter(self):
        if not self.client or not self.client.connected:
            QMessageBox.warning(self, "경고", "연결된 상태에서만 리셋이 가능합니다.")
            return
            
        reply = QMessageBox.question(self, '확인', f"[{self.txt_name.text()}] 카운트 수치를 0으로 초기화하시겠습니까?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # Coil 0001번지(1)에 True 전송 -> RESET 실행
            response = self.client.write_coil(address=1, value=True, slave=1)
            if not response.isError():
                QMessageBox.information(self, "성공", "0으로 리셋되었습니다.")
            else:
                QMessageBox.critical(self, "오류", "리셋 전송에 실패했습니다.")

    def get_config(self):
        return {
            "name": self.txt_name.text(),
            "port": self.port_combo.currentData(),
            "auto_connect": self.timer.isActive()
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.next_card_id = 1
        self.initUI()
        self.load_config_and_autoconnect()

    def initUI(self):
        self.setWindowTitle("오토닉스 다중 카운터 실시간 모니터링 프로그램")
        self.resize(900, 600)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        
        # 상단 제어바
        top_bar = QHBoxLayout()
        btn_add = QPushButton("+ 카운터 장비 추가")
        btn_add.setFont(QFont("맑은 고딕", 10, QFont.Bold))
        btn_add.setStyleSheet("background-color: #3498DB; color: white; padding: 8px;")
        btn_add.clicked.connect(self.add_counter_card)
        
        btn_refresh_all = QPushButton("포트 목록 전체 새로고침")
        btn_refresh_all.clicked.connect(self.refresh_all_ports)
        
        top_bar.addWidget(btn_add)
        top_bar.addWidget(btn_refresh_all)
        top_bar.addStretch()
        
        # 스크롤 가능한 카드 배치 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.grid_layout = QGridLayout(self.scroll_content)
        scroll.setWidget(self.scroll_content)
        
        main_layout.addLayout(top_bar)
        main_layout.addWidget(scroll)

    def add_counter_card(self, name="", port="", auto_connect=False):
        card = CounterCard(self.next_card_id, self, default_name=name, default_port=port)
        self.cards.append(card)
        
        row = (len(self.cards) - 1) // 2
        col = (len(self.cards) - 1) % 2
        self.grid_layout.addWidget(card, row, col)
        
        self.next_card_id += 1
        
        # 이전 상태가 연결 중이었다면 자동 연결 수행
        if auto_connect:
            card.start_connection()

    def remove_counter_card(self, card):
        card.stop_connection()
        self.grid_layout.removeWidget(card)
        self.cards.remove(card)
        card.deleteLater()
        self.rearrange_grid()

    def rearrange_grid(self):
        for i, card in enumerate(self.cards):
            self.grid_layout.removeWidget(card)
            row = i // 2
            col = i % 2
            self.grid_layout.addWidget(card, row, col)

    def refresh_all_ports(self):
        for card in self.cards:
            card.refresh_ports()

    def save_config(self):
        config_data = []
        for card in self.cards:
            config_data.append(card.get_config())
            
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)

    def load_config_and_autoconnect(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    
                for item in config_data:
                    self.add_counter_card(
                        name=item.get("name", ""),
                        port=item.get("port", ""),
                        auto_connect=item.get("auto_connect", False)
                    )
            except Exception as e:
                print(f"설정 로드 오류: {e}")
        
        if not self.cards:
            self.add_counter_card()

    def closeEvent(self, event):
        self.save_config()
        for card in self.cards:
            card.stop_connection()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())