"""카운터 Modbus 레지스터 확인 도구

카운터 앞면에 표시된 숫자와 비교해서, 현재값(PV)이 어느 주소에 어떤 워드 순서로
들어 있는지 확인할 때 사용합니다. 결과를 보고 settings.ini [modbus] 를 맞추세요.

사용법 (명령 프롬프트):
    python modbus_check.py                 → COM 포트 목록 표시
    python modbus_check.py COM3            → COM3 의 레지스터 값 표시
    python modbus_check.py COM3 --slave 2 --baud 9600 --parity E

※ counter_app 이 같은 COM 포트에 연결 중이면 포트를 열 수 없으니 먼저 [연결 끊기] 하세요.
"""
import sys
import inspect
import argparse
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient

# pymodbus 3.10 이상은 'slave' 대신 'device_id' 인자를 사용
_UNIT_KW = ("device_id" if "device_id" in inspect.signature(ModbusSerialClient.read_holding_registers).parameters
            else "slave")

# (이름, 읽기 함수 이름, 시작 주소, 개수, 매뉴얼 주소 앞자리)
BLOCKS = [
    ("Input Register (기능코드 04)", "read_input_registers", 1000, 12, 300001),
    ("Holding Register (기능코드 03)", "read_holding_registers", 0, 12, 400001),
]


def to_signed(v):
    return v - 0x100000000 if v >= 0x80000000 else v


def main():
    ap = argparse.ArgumentParser(description="카운터 Modbus 레지스터 확인 도구")
    ap.add_argument("port", nargs="?", help="COM 포트 (예: COM3)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--parity", default="E", choices=["E", "O", "N"])
    ap.add_argument("--stopbits", type=int, default=1, choices=[1, 2])
    ap.add_argument("--slave", type=int, default=1, help="국번 (Slave ID)")
    args = ap.parse_args()

    if not args.port:
        print("사용 가능한 COM 포트:")
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device}  ({p.description})")
        print("\n사용법: python modbus_check.py COM3")
        return

    client = ModbusSerialClient(port=args.port, baudrate=args.baud, parity=args.parity,
                                stopbits=args.stopbits, bytesize=8, timeout=1)
    if not client.connect():
        print(f"{args.port} 를 열 수 없습니다. (다른 프로그램이 사용 중인지 확인)")
        sys.exit(1)

    print(f"{args.port}  {args.baud}/{args.parity}/8/{args.stopbits}  국번 {args.slave}\n")
    print("카운터 앞면에 표시된 현재 숫자와 같은 값이 있는 줄을 찾으세요.")
    print("  → 그 줄의 '주소'가 settings.ini 의 pv_address, 열 이름이 word_order 입니다.\n")
    try:
        for title, func, start, count, manual_base in BLOCKS:
            print(f"■ {title}")
            try:
                resp = getattr(client, func)(address=start, count=count, **{_UNIT_KW: args.slave})
            except Exception as e:
                print(f"  읽기 실패: {e}\n")
                continue
            if resp.isError():
                print(f"  장비 응답 오류: {resp}\n")
                continue
            regs = resp.registers
            print(f"  {'주소':>6} {'매뉴얼주소':>10} {'값(16진)':>9} {'값(10진)':>8} | "
                  f"{'2워드: low_first':>18} {'high_first':>14}")
            for i, v in enumerate(regs):
                addr = start + i
                line = f"  {addr:>6} {manual_base + addr:>10}    0x{v:04X} {v:>8} |"
                if i + 1 < len(regs):
                    nxt = regs[i + 1]
                    low_first = to_signed((nxt << 16) | v)
                    high_first = to_signed((v << 16) | nxt)
                    line += f" {low_first:>18,} {high_first:>14,}"
                print(line)
            print()
    finally:
        client.close()


if __name__ == "__main__":
    main()
