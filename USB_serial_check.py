import serial.tools.list_ports

ports = serial.tools.list_ports.comports()
for port in ports:
    print(f"포트: {port.device} | 설명: {port.description} | 시리얼 번호: {port.serial_number}")