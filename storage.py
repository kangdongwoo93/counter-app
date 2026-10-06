"""환경설정(settings.ini) 로드 및 수집 데이터 저장(CSV / DB) 모듈

- 저장은 별도 스레드에서 처리하므로 DB가 느리거나 끊겨도 화면/수집이 멈추지 않는다.
- DB 연결이 끊기면 데이터를 db_pending.csv 에 임시 보관하고,
  재연결되면 자동으로 DB에 다시 전송한 뒤 파일을 삭제한다. (프로그램 재시작 후에도 유지)
"""
import os
import re
import csv
import time
import queue
import socket
import threading
import configparser
from datetime import datetime

SETTINGS_FILE = "settings.ini"
PENDING_FILE = "db_pending.csv"

STORAGE_MODES = ("csv", "db", "both")
DB_TYPES = ("sqlite", "mysql", "mssql", "postgresql")

DEFAULT_SETTINGS = """\
; ==============================================================================
;  카운터 모니터링 환경설정 파일
;  - 이 파일을 수정한 뒤에는 프로그램을 다시 시작해야 적용됩니다.
;  - ';' 로 시작하는 줄은 설명(주석)입니다.
; ==============================================================================

[general]
; 이 PC(설비)를 구분하는 이름. 서버에서 여러 PC의 데이터를 합칠 때 구분용으로 저장됩니다.
; 비워 두면 컴퓨터 이름을 사용합니다.
station =

; 카운터 값 읽기 주기 (밀리초, 1000 = 1초) - 화면 숫자가 이 주기로 갱신됩니다.
poll_interval_ms = 1000


[modbus]
; 카운터 통신 설정 (오토닉스 CT 시리즈 기본값: 9600 bps, Even, Stop 1, 국번 1)
baudrate = 9600
; 패리티: E (Even) / O (Odd) / N (None)
parity = E
stopbits = 1
; 카운터의 통신 국번 (Slave ID)
slave_id = 1

; 현재 카운트 값(PV)을 읽을 레지스터 (32비트 = 레지스터 2개)
;   register_type : input   (기능코드 04, 3xxxxx 주소)
;                   holding (기능코드 03, 4xxxxx 주소)
;   pv_address    : 0 부터 시작하는 주소.  예) 301004 → 1003
;   CT 시리즈 현재값: Input Register 301004 (pv_address = 1003, 16진수 03EB)
;   ※ 장비 매뉴얼의 통신 주소표로 확인하세요. modbus_check.py 로 실제 값을 확인할 수 있습니다.
register_type = input
pv_address = 1003

; 32비트 값의 워드 순서
;   low_first  : 하위 워드가 먼저 (CT 시리즈)
;   high_first : 상위 워드가 먼저
word_order = low_first

; [0 리셋] 버튼이 ON 을 보낼 Coil 주소 (0 부터 시작, 기능코드 05)
; ※ 장비 매뉴얼의 리셋 주소와 같은지 확인한 뒤 사용하세요.
reset_coil_address = 1


[devices]
; USB-시리얼 변환기 시리얼 번호 = 표시할 장비 이름
;   포트 목록에 "[장비 이름] COM3" 형태로 표시되어 어느 포트가 어느 장비인지 구분할 수 있습니다.
;   시리얼 번호는 "python USB_serial_check.py" 로 확인합니다.
;   (카드가 기억하는 변환기는 연결 성공 시 자동 저장되므로, 여기 등록은 표시용입니다)
; 예)
; B001T4P1A = A라인 카운터
; B001T4P2B = B라인 카운터


[storage]
; 저장 방식
;   csv  : CSV 파일로만 저장 (기본값)
;   db   : 데이터베이스로만 저장
;   both : CSV 와 데이터베이스 모두 저장
mode = csv

; 저장 주기 (초). 화면 갱신 주기와 별개로, 장비마다 이 주기에 1건씩 저장합니다.
; 매 분 0초/30초처럼 시각에 맞춰 저장되며, 연결 시작·연결 해제·프로그램 종료 시점의 값도 저장합니다.
;   1  = 매초 저장    (장비 1대당 하루 86,400행)
;   30 = 30초마다 저장 (장비 1대당 하루  2,880행)
;   60 = 1분마다 저장  (장비 1대당 하루  1,440행)
save_interval_sec = 30

; 값이 바뀌었을 때만 저장 (true / false)
;   true  : 저장 시점에 카운트 값이 직전 저장값과 같으면 저장하지 않음 (장비가 멈춰 있으면 행이 쌓이지 않음)
;   false : 값 변화와 상관없이 저장 주기마다 항상 저장
save_only_on_change = true

; [save_only_on_change = true 일 때] 값이 그대로여도 이 시간(분)마다 1건은 저장
; → 서버에서 "장비가 멈춘 것"과 "PC/프로그램이 꺼진 것"을 구분하는 생존 신호 역할
; 0 이면 사용 안 함
keepalive_minutes = 60


[csv]
; CSV 저장 폴더. 비워 두면 프로그램(exe)이 있는 폴더에 저장합니다.
; 공유 폴더도 가능합니다. 예) \\\\SERVER\\share\\counter_logs
folder =


[db]
; 데이터베이스 종류: sqlite / mysql / mssql / postgresql
;   sqlite     : 별도 서버 없이 로컬 파일(sqlite_path)에 저장 (테스트용)
;   mysql      : MySQL / MariaDB
;   mssql      : Microsoft SQL Server
;   postgresql : PostgreSQL
type = sqlite

; 서버 주소 / 포트 (포트를 비우면 기본값: mysql 3306, mssql 1433, postgresql 5432)
host = 127.0.0.1
port =

; 데이터베이스 이름 / 계정
; (mssql 은 user 를 비워 두면 Windows 인증으로 접속합니다)
database = counter
user =
password =

; 저장할 테이블 이름 (영문, 숫자, _ 만 사용. 스키마 지정 시 dbo.counter_log 형식)
table = counter_log

; 테이블이 없으면 자동으로 생성 (DB 계정에 테이블 생성 권한이 없으면 false 로 두고 직접 생성)
auto_create_table = true

; DB 연결이 끊겼을 때 재접속 시도 간격 (초)
retry_interval_sec = 10

; [sqlite 전용] DB 파일 경로 (상대 경로는 프로그램 폴더 기준)
sqlite_path = counter_data.db

; [mssql 전용] ODBC 드라이버 이름과 추가 접속 옵션
;   드라이버가 없으면 Microsoft "ODBC Driver 17 for SQL Server" 를 설치하거나
;   Windows 기본 드라이버인 "SQL Server" 로 바꾸세요.
odbc_driver = ODBC Driver 17 for SQL Server
odbc_options =
"""


def _read_text(path):
    # 메모장에서 저장하면 UTF-8(BOM 유무) 또는 ANSI(cp949)일 수 있음
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError("설정 파일 인코딩을 읽을 수 없습니다 (UTF-8 로 저장하세요)")


def _default_blocks():
    """DEFAULT_SETTINGS 를 섹션별 (키, 설명 주석 + 키 줄) 목록으로 분리"""
    sections, section, buf = {}, None, []
    for line in DEFAULT_SETTINGS.splitlines():
        text = line.strip()
        if text.startswith("[") and text.endswith("]"):
            section = text[1:-1]
            sections[section] = []
            buf = []
        elif not text:
            buf = []
        elif text.startswith(";"):
            buf.append(line)
        elif section and "=" in text:
            key = text.split("=", 1)[0].strip().lower()
            sections[section].append((key, buf + [line]))
            buf = []
    return sections


def _default_section_lines(section):
    """DEFAULT_SETTINGS 에서 [section] 헤더부터 다음 섹션 전까지의 줄 (뒤쪽 빈 줄 제외)"""
    out, inside = [], False
    for line in DEFAULT_SETTINGS.splitlines():
        text = line.strip()
        if text.startswith("[") and text.endswith("]"):
            if inside:
                break
            inside = text[1:-1] == section
        if inside:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def add_missing_settings(path):
    """기존 settings.ini 에 없는 (새 버전에서 추가된) 항목을 기본값과 설명과 함께 추가.
    사용자가 수정한 기존 값은 그대로 유지한다. 추가된 항목 이름 목록을 반환."""
    text = _read_text(path)
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(text)
    lines = text.splitlines()
    added = []

    for section, blocks in _default_blocks().items():
        header = next((i for i, l in enumerate(lines) if l.strip().lower() == f"[{section}]"), None)
        if header is None:
            # 섹션 전체가 없으면 설명 주석까지 통째로 추가
            lines += ["", "", "; ↓ 새 버전에서 추가된 설정 (자동 추가됨)"] + _default_section_lines(section)
            added.append(f"[{section}]")
            continue

        missing = [(k, b) for k, b in blocks if not cp.has_option(section, k)]
        if not missing:
            continue
        insert = ["", "; ↓ 새 버전에서 추가된 설정 (자동 추가됨)"]
        for key, block in missing:
            insert += block + [""]
            added.append(key)

        # 해당 섹션의 끝(다음 섹션 시작 전, 뒤쪽 빈 줄 제외) 위치에 삽입
        end = next((i for i in range(header + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        while end > header + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = insert[:-1]

    if added:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\r\n".join(lines) + "\r\n")
    return added


class Settings:
    """settings.ini 를 읽어 속성으로 제공. 파일이 없으면 기본 파일을 만든다."""

    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self.errors = []
        self.added = []  # 기존 설정 파일에 자동 추가된 항목

        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(DEFAULT_SETTINGS.replace("\n", "\r\n"))
            except OSError as e:
                self.errors.append(f"기본 설정 파일 생성 실패: {e}")
        else:
            try:
                self.added = add_missing_settings(path)
            except Exception as e:
                self.errors.append(f"설정 파일에 새 항목 추가 실패: {e}")

        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(DEFAULT_SETTINGS)  # 기본값
        if os.path.exists(path):
            try:
                cp.read_string(_read_text(path))
            except Exception as e:
                self.errors.append(f"설정 파일 오류 (기본값으로 실행): {e}")

        g = cp["general"]
        self.station = g.get("station", "").strip() or socket.gethostname()
        self.poll_interval_ms = self._get_int(g, "poll_interval_ms", 1000, minimum=200)

        # [devices] 시리얼 번호(대문자) → 장비 이름
        self.devices = {k.strip().upper(): v.strip() for k, v in cp["devices"].items() if k.strip() and v.strip()}

        m = cp["modbus"]
        self.mb_baudrate = self._get_int(m, "baudrate", 9600, minimum=1200)
        self.mb_parity = m.get("parity", "E").strip().upper()[:1] or "E"
        if self.mb_parity not in ("E", "O", "N"):
            self.errors.append(f"[modbus] parity 값 '{self.mb_parity}' 이(가) 올바르지 않아 E 를 사용합니다.")
            self.mb_parity = "E"
        self.mb_stopbits = 2 if self._get_int(m, "stopbits", 1) == 2 else 1
        self.mb_slave_id = self._get_int(m, "slave_id", 1, minimum=0)
        self.mb_register_type = m.get("register_type", "input").strip().lower()
        if self.mb_register_type not in ("input", "holding"):
            self.errors.append(f"[modbus] register_type 값 '{self.mb_register_type}' 이(가) 올바르지 않아 input 을 사용합니다.")
            self.mb_register_type = "input"
        self.mb_pv_address = self._get_int(m, "pv_address", 1003, minimum=0)
        self.mb_word_order = m.get("word_order", "low_first").strip().lower()
        if self.mb_word_order not in ("low_first", "high_first"):
            self.errors.append(f"[modbus] word_order 값 '{self.mb_word_order}' 이(가) 올바르지 않아 low_first 를 사용합니다.")
            self.mb_word_order = "low_first"
        self.mb_reset_coil = self._get_int(m, "reset_coil_address", 1, minimum=0)

        st = cp["storage"]
        self.save_interval_sec = self._get_int(st, "save_interval_sec", 30, minimum=1)
        self.save_only_on_change = st.get("save_only_on_change", "true").strip().lower() in ("1", "true", "yes", "on")
        self.keepalive_minutes = self._get_int(st, "keepalive_minutes", 60, minimum=0)
        self.mode = cp["storage"].get("mode", "csv").strip().lower()
        if self.mode not in STORAGE_MODES:
            self.errors.append(f"[storage] mode 값 '{self.mode}' 이(가) 올바르지 않아 csv 로 저장합니다.")
            self.mode = "csv"

        self.csv_folder = cp["csv"].get("folder", "").strip() or "."

        d = cp["db"]
        self.db_type = d.get("type", "sqlite").strip().lower()
        if self.use_db and self.db_type not in DB_TYPES:
            self.errors.append(f"[db] type 값 '{self.db_type}' 이(가) 올바르지 않습니다. ({'/'.join(DB_TYPES)})")
        self.db_host = d.get("host", "").strip()
        self.db_port = self._get_int(d, "port", None)
        self.db_name = d.get("database", "").strip()
        self.db_user = d.get("user", "").strip()
        self.db_password = d.get("password", "")
        self.db_table = d.get("table", "counter_log").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", self.db_table):
            self.errors.append(f"[db] table 이름 '{self.db_table}' 이(가) 올바르지 않아 counter_log 를 사용합니다.")
            self.db_table = "counter_log"
        self.db_auto_create = d.get("auto_create_table", "true").strip().lower() in ("1", "true", "yes", "on")
        self.db_retry_sec = self._get_int(d, "retry_interval_sec", 10, minimum=1)
        self.sqlite_path = d.get("sqlite_path", "counter_data.db").strip() or "counter_data.db"
        self.odbc_driver = d.get("odbc_driver", "ODBC Driver 17 for SQL Server").strip()
        self.odbc_options = d.get("odbc_options", "").strip()

    def _get_int(self, section, key, default, minimum=None):
        raw = section.get(key, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"[{section.name}] {key} 값 '{raw}' 이(가) 숫자가 아니어서 기본값을 사용합니다.")
            return default
        if minimum is not None and value < minimum:
            return minimum
        return value

    @property
    def use_csv(self):
        return self.mode in ("csv", "both")

    @property
    def use_db(self):
        return self.mode in ("db", "both")

    def describe(self):
        parts = []
        if self.use_csv:
            parts.append("CSV")
        if self.use_db:
            parts.append(f"DB({self.db_type})")
        return " + ".join(parts)


# ==============================================================================
# DB 저장
# ==============================================================================
class DbSink:
    """DB 종류별 접속 / 테이블 생성 / 일괄 INSERT"""

    def __init__(self, settings):
        self.s = settings
        self.conn = None
        t = settings.db_table
        self.placeholder = "?" if settings.db_type in ("sqlite", "mssql") else "%s"
        ph = ", ".join([self.placeholder] * 5)
        self.insert_sql = (f"INSERT INTO {t} (logged_at, station, device_name, port, count_value) "
                           f"VALUES ({ph})")

    def connect(self):
        s = self.s
        t = s.db_type
        if t == "sqlite":
            import sqlite3
            conn = sqlite3.connect(s.sqlite_path, timeout=10)
        elif t == "mysql":
            import pymysql
            conn = pymysql.connect(host=s.db_host, port=s.db_port or 3306, user=s.db_user,
                                   password=s.db_password, database=s.db_name,
                                   charset="utf8mb4", connect_timeout=5, autocommit=False)
        elif t == "mssql":
            import pyodbc
            cs = f"DRIVER={{{s.odbc_driver}}};SERVER={s.db_host},{s.db_port or 1433};DATABASE={s.db_name};"
            if s.db_user:
                # ODBC 규칙: 중괄호로 감싼 값 안의 '}' 는 '}}' 로 표기
                pwd = s.db_password.replace("}", "}}")
                cs += f"UID={s.db_user};PWD={{{pwd}}};"
            else:
                cs += "Trusted_Connection=yes;"
            if s.odbc_options:
                cs += s.odbc_options.rstrip(";") + ";"
            conn = pyodbc.connect(cs, timeout=5, autocommit=False)
        elif t == "postgresql":
            import psycopg2
            conn = psycopg2.connect(host=s.db_host, port=s.db_port or 5432, dbname=s.db_name,
                                    user=s.db_user, password=s.db_password, connect_timeout=5)
        else:
            raise ValueError(f"지원하지 않는 DB 종류: {t}")

        self.conn = conn
        try:
            if s.db_auto_create:
                self._create_table()
        except Exception:
            self.close()
            raise

    def _create_table(self):
        t = self.s.db_table
        idx = t.replace(".", "_") + "_logged_at"
        dbt = self.s.db_type
        if dbt == "sqlite":
            sqls = [f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    f"logged_at TEXT NOT NULL, station TEXT, device_name TEXT, port TEXT, count_value INTEGER)",
                    f"CREATE INDEX IF NOT EXISTS ix_{idx} ON {t} (logged_at)"]
        elif dbt == "mysql":
            sqls = [f"CREATE TABLE IF NOT EXISTS {t} (id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                    f"logged_at DATETIME NOT NULL, station VARCHAR(100), device_name VARCHAR(100), "
                    f"port VARCHAR(50), count_value BIGINT, INDEX ix_logged_at (logged_at)) "
                    f"DEFAULT CHARSET=utf8mb4"]
        elif dbt == "postgresql":
            sqls = [f"CREATE TABLE IF NOT EXISTS {t} (id BIGSERIAL PRIMARY KEY, "
                    f"logged_at TIMESTAMP NOT NULL, station VARCHAR(100), device_name VARCHAR(100), "
                    f"port VARCHAR(50), count_value BIGINT)",
                    f"CREATE INDEX IF NOT EXISTS ix_{idx} ON {t} (logged_at)"]
        else:  # mssql
            sqls = [f"IF OBJECT_ID(N'{t}', N'U') IS NULL BEGIN "
                    f"CREATE TABLE {t} (id BIGINT IDENTITY(1,1) PRIMARY KEY, "
                    f"logged_at DATETIME2(0) NOT NULL, station NVARCHAR(100), device_name NVARCHAR(100), "
                    f"port NVARCHAR(50), count_value BIGINT); "
                    f"CREATE INDEX ix_{idx} ON {t} (logged_at); END"]
        cur = self.conn.cursor()
        for sql in sqls:
            cur.execute(sql)
        self.conn.commit()
        cur.close()

    def insert(self, rows):
        """rows: [(datetime, station, device_name, port, count), ...] → 한 트랜잭션으로 저장"""
        if self.s.db_type == "sqlite":
            params = [(r[0].strftime("%Y-%m-%d %H:%M:%S"),) + tuple(r[1:]) for r in rows]
        else:
            params = [(r[0].replace(microsecond=0),) + tuple(r[1:]) for r in rows]
        cur = self.conn.cursor()
        try:
            if self.s.db_type == "mssql":
                cur.fast_executemany = True
            cur.executemany(self.insert_sql, params)
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


# ==============================================================================
# 저장 스레드
# ==============================================================================
def _safe_filename(name):
    return re.sub(r'[\\/:*?"<>|\s]', "_", name.strip()) or "noname"


class StorageWriter:
    """수집 데이터를 큐로 받아 백그라운드 스레드에서 CSV / DB 에 저장"""

    FLUSH_CHUNK = 1000

    def __init__(self, settings, on_status=None):
        self.s = settings
        self.on_status = on_status or (lambda text, level: None)
        self.q = queue.Queue()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="StorageWriter", daemon=True)

        self.db = DbSink(settings) if settings.use_db else None
        self.db_error = None
        self.next_retry = 0.0
        self.csv_error = None
        self.pending_count = self._count_pending() if self.db else 0
        self._last_status = None

    # ---- UI 스레드에서 호출 ----------------------------------------------
    def start(self):
        self.thread.start()
        self._report()

    def put(self, device_name, port, count, ts=None):
        self.q.put((ts or datetime.now(), self.s.station, device_name, port, int(count)))

    def stop(self, timeout=15):
        """남은 데이터를 저장하고 종료 (DB가 끊겨 있으면 미전송 파일로 보관)"""
        self._stop.set()
        self.q.put(None)  # 대기 중인 저장 스레드를 즉시 깨움
        self.thread.join(timeout)

    # ---- 저장 스레드 --------------------------------------------------------
    def _run(self):
        while True:
            try:
                self._step()
            except Exception as e:
                # 예상치 못한 오류가 나도 저장 스레드는 계속 동작
                self.csv_error = f"저장 처리 오류: {e}"
            self._report()
            if self._stop.is_set() and self.q.empty():
                break
        if self.db:
            self.db.close()

    def _step(self):
        batch = self._drain()
        if batch:
            if self.s.use_csv:
                self._write_csv(batch)
            if self.db:
                self._write_db(batch)
        elif self.db and self.pending_count and not self._stop.is_set():
            # 새 데이터가 없어도 미전송분 재전송 시도
            if self._db_ready():
                try:
                    self._flush_pending()
                except Exception as e:
                    self._db_failed(e)

    def _drain(self):
        batch = []
        try:
            item = self.q.get(timeout=1.0)
            while True:
                if item is not None:  # None = 종료 알림
                    batch.append(item)
                if len(batch) >= 5000:
                    break
                item = self.q.get_nowait()
        except queue.Empty:
            pass
        return batch

    def _write_csv(self, batch):
        groups = {}
        for row in batch:
            ts, station, device, port, count = row
            fname = f"counter_log_{_safe_filename(device)}_{ts.strftime('%Y%m%d')}.csv"
            groups.setdefault(fname, []).append(row)
        try:
            os.makedirs(self.s.csv_folder, exist_ok=True)
            for fname, rows in groups.items():
                path = os.path.join(self.s.csv_folder, fname)
                file_exists = os.path.exists(path)
                with open(path, mode="a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["일시", "장비명", "COM포트", "카운트 수"])
                    for ts, station, device, port, count in rows:
                        writer.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), device, port, count])
            self.csv_error = None
        except OSError as e:
            self.csv_error = str(e)

    def _db_ready(self):
        if self.db.conn is not None:
            return True
        # 종료 중이거나 재시도 대기 시간 전이면 접속 시도하지 않음
        if self._stop.is_set() or time.monotonic() < self.next_retry:
            return False
        try:
            self.db.connect()
            self.db_error = None
            return True
        except ImportError as e:
            self._db_failed(f"DB 드라이버가 설치되어 있지 않습니다 ({e.name})")
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:  # 드라이버 로드 중 발생하는 비정상 오류까지 포함
            self._db_failed(e)
        return False

    def _db_failed(self, err):
        self.db_error = str(err).strip().splitlines()[0][:200] if str(err).strip() else type(err).__name__
        self.db.close()
        self.next_retry = time.monotonic() + self.s.db_retry_sec

    def _write_db(self, batch):
        if not self._db_ready():
            self._append_pending(batch)
            return
        try:
            if self.pending_count:
                self._flush_pending()
            self.db.insert(batch)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            self._db_failed(e)
            self._append_pending(batch)

    # ---- 미전송 데이터 파일 -------------------------------------------------
    def _count_pending(self):
        if not os.path.exists(PENDING_FILE):
            return 0
        try:
            with open(PENDING_FILE, encoding="utf-8-sig") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def _append_pending(self, batch):
        try:
            with open(PENDING_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for ts, station, device, port, count in batch:
                    w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), station, device, port, count])
            self.pending_count += len(batch)
        except OSError as e:
            self.db_error = f"{self.db_error or ''} / 미전송 파일 저장 실패: {e}"

    def _flush_pending(self):
        """미전송 파일을 DB로 전송. 실패하면 남은 행만 파일에 다시 기록"""
        if not os.path.exists(PENDING_FILE):
            self.pending_count = 0
            return
        with open(PENDING_FILE, encoding="utf-8-sig") as f:
            rows = []
            for r in csv.reader(f):
                if len(r) != 5:
                    continue
                try:
                    rows.append((datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), r[1], r[2], r[3], int(r[4])))
                except ValueError:
                    continue
        done = 0
        try:
            for i in range(0, len(rows), self.FLUSH_CHUNK):
                chunk = rows[i:i + self.FLUSH_CHUNK]
                self.db.insert(chunk)
                done += len(chunk)
        except Exception:
            self._rewrite_pending(rows[done:])
            raise
        os.remove(PENDING_FILE)
        self.pending_count = 0

    def _rewrite_pending(self, rows):
        tmp = PENDING_FILE + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for ts, station, device, port, count in rows:
                w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), station, device, port, count])
        os.replace(tmp, PENDING_FILE)
        self.pending_count = len(rows)

    # ---- 상태 알림 ----------------------------------------------------------
    def _report(self):
        level = "ok"
        parts = []
        if self.s.use_csv:
            if self.csv_error:
                parts.append(f"CSV 저장 실패: {self.csv_error}")
                level = "error"
            else:
                parts.append("CSV")
        if self.db:
            name = f"DB({self.s.db_type})"
            if self.db_error:
                parts.append(f"{name} 오류: {self.db_error}")
                level = "error"
            elif self.db.conn is not None:
                parts.append(f"{name} 연결됨")
            else:
                parts.append(f"{name} 대기")
            if self.pending_count:
                parts.append(f"미전송 {self.pending_count:,}건")
                if level == "ok":
                    level = "warn"
        status = ("저장: " + " · ".join(parts), level)
        if status != self._last_status:
            self._last_status = status
            self.on_status(*status)
