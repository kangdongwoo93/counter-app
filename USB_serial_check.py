import serial.tools.list_ports

# 연결된 COM 포트와 USB-시리얼 변환기 시리얼 번호를 표시합니다.
# 시리얼 번호는 settings.ini 의 [devices] 에 "시리얼번호 = 장비 이름" 형태로 등록하면
# 프로그램의 포트 목록에 장비 이름이 표시됩니다.
ports = serial.tools.list_ports.comports()
for port in ports:
    print(f"포트: {port.device} | 설명: {port.description} | 시리얼 번호: {port.serial_number}")

usb = [p for p in ports if p.serial_number]
if usb:
    print("\nsettings.ini 의 [devices] 아래에 붙여 넣고 이름을 바꾸세요:")
    for p in usb:
        print(f"{p.serial_number} = {p.device} 장비 이름")
