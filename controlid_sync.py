from __future__ import annotations

import hashlib
import json
import sqlite3
import ssl
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from db import connect
from parser_afd import normalize_external_id, parse_afd


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEVICE_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS controlid_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 443,
    login TEXT NOT NULL DEFAULT 'admin',
    password TEXT NOT NULL DEFAULT '',
    verify_tls INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    auto_sync_minutes INTEGER NOT NULL DEFAULT 0,
    last_nsr INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_success_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_new_punches INTEGER NOT NULL DEFAULT 0,
    device_model TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controlid_syncs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requested_initial_nsr INTEGER,
    returned_max_nsr INTEGER,
    inserted_punches INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    message TEXT,
    import_id INTEGER,
    FOREIGN KEY(device_id) REFERENCES controlid_devices(id) ON DELETE CASCADE,
    FOREIGN KEY(import_id) REFERENCES imports(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_controlid_syncs_device_started
    ON controlid_syncs(device_id, started_at DESC);
'''


class ControlIDError(RuntimeError):
    pass


_device_locks: dict[int, threading.Lock] = {}
_device_locks_guard = threading.Lock()


def ensure_controlid_schema() -> None:
    with connect() as conn:
        conn.executescript(DEVICE_SCHEMA)


def _device_lock(device_id: int) -> threading.Lock:
    with _device_locks_guard:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


def normalize_host(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ControlIDError("Informe o IP ou hostname do relógio.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse.urlparse(value)
    if parsed.scheme.lower() != "https":
        raise ControlIDError("O REP Control iD deve ser acessado por HTTPS.")
    host = parsed.hostname
    if not host:
        raise ControlIDError("IP ou hostname inválido.")
    return host


def _decode_json(raw: bytes, action: str) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlIDError(f"Resposta inválida do relógio durante {action}.") from exc
    if not isinstance(data, dict):
        raise ControlIDError(f"Resposta inesperada do relógio durante {action}.")
    return data


class ControlIDClient:
    def __init__(
        self,
        host: str,
        port: int = 443,
        login: str = "admin",
        password: str = "",
        verify_tls: bool = False,
        timeout: int = 12,
    ) -> None:
        self.host = normalize_host(host)
        self.port = int(port or 443)
        self.login_name = str(login or "admin")
        self.password = str(password or "")
        self.verify_tls = bool(verify_tls)
        self.timeout = max(3, int(timeout or 12))
        self.session: Optional[str] = None

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"

    def _context(self):
        if self.verify_tls:
            return ssl.create_default_context()
        return ssl._create_unverified_context()

    def _post(self, path: str, payload: Optional[dict] = None) -> bytes:
        body = json.dumps(payload or {}).encode("utf-8")
        req = urlrequest.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "*/*",
                "User-Agent": "Atlas-Ponto/1.0",
            },
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout, context=self._context()) as response:
                return response.read()
        except urlerror.HTTPError as exc:
            if exc.code in (401, 403):
                raise ControlIDError("Usuário ou senha recusados pelo relógio.") from exc
            raise ControlIDError(f"O relógio respondeu HTTP {exc.code}.") from exc
        except urlerror.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise ControlIDError(f"Não foi possível conectar ao relógio: {reason}") from exc
        except TimeoutError as exc:
            raise ControlIDError("Tempo limite excedido ao conectar ao relógio.") from exc
        except ssl.SSLError as exc:
            raise ControlIDError(f"Falha no certificado HTTPS do relógio: {exc}") from exc

    def login(self) -> str:
        raw = self._post(
            "/login.fcgi",
            {"login": self.login_name, "password": self.password},
        )
        data = _decode_json(raw, "autenticação")
        session = str(data.get("session") or "").strip()
        if not session:
            raise ControlIDError("O relógio não retornou uma sessão válida.")
        self.session = session
        return session

    def _require_session(self) -> str:
        return self.session or self.login()

    def get_date_time(self) -> dict:
        session = urlparse.quote(self._require_session(), safe="")
        raw = self._post(
            f"/get_system_date_time.fcgi?session={session}&mode=671",
            {},
        )
        return _decode_json(raw, "consulta de data/hora")

    def get_afd(self, initial_nsr: Optional[int] = None) -> bytes:
        session = urlparse.quote(self._require_session(), safe="")
        payload: dict = {}
        if initial_nsr is not None and int(initial_nsr) > 0:
            payload["initial_nsr"] = int(initial_nsr)
        return self._post(
            f"/get_afd.fcgi?session={session}&mode=671",
            payload,
        )


def max_nsr_from_afd(raw: bytes) -> int:
    text = None
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        text = raw.decode("latin-1", errors="replace")
    maximum = 0
    for line in text.splitlines():
        if len(line) >= 9 and line[:9].isdigit():
            maximum = max(maximum, int(line[:9]))
    return maximum


def _import_afd_bytes(device_id: int, device_name: str, raw: bytes) -> dict:
    if not raw:
        return {
            "inserted": 0,
            "import_id": None,
            "max_nsr": 0,
            "device_model": None,
            "total_lines": 0,
            "employee_records": 0,
            "warnings": [],
        }

    file_hash = hashlib.sha256(raw).hexdigest()
    max_nsr = max_nsr_from_afd(raw)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored_name = f"SYNC_REP_{device_id}_{stamp}.txt"
    stored_path = UPLOAD_DIR / stored_name
    stored_path.write_bytes(raw)
    parsed = parse_afd(stored_path)

    # Sem registros úteis: não poluir o histórico de imports com um arquivo vazio.
    if not parsed.punches and not parsed.employees:
        try:
            stored_path.unlink()
        except OSError:
            pass
        return {
            "inserted": 0,
            "import_id": None,
            "max_nsr": max_nsr,
            "device_model": parsed.device_model,
            "total_lines": parsed.total_lines,
            "employee_records": len(parsed.employees),
            "warnings": parsed.warnings,
        }

    with connect() as conn:
        existing_import = conn.execute(
            "SELECT id FROM imports WHERE file_hash=?",
            (file_hash,),
        ).fetchone()
        if existing_import:
            try:
                stored_path.unlink()
            except OSError:
                pass
            return {
                "inserted": 0,
                "import_id": int(existing_import["id"]),
                "max_nsr": max_nsr,
                "device_model": parsed.device_model,
                "total_lines": parsed.total_lines,
                "employee_records": len(parsed.employees),
                "warnings": parsed.warnings,
            }

        original_filename = f"REP {device_name} - sincronização automática.txt"
        cur = conn.execute(
            """
            INSERT INTO imports(
                original_filename, stored_filename, file_hash, device_model,
                total_lines, punch_count, employee_records, notes
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                original_filename,
                stored_name,
                file_hash,
                parsed.device_model,
                parsed.total_lines,
                0,
                len(parsed.employees),
                "\n".join(parsed.warnings[:20]),
            ),
        )
        import_id = int(cur.lastrowid)

        latest = {}
        for employee_record in parsed.employees:
            latest[employee_record.external_id] = employee_record
        for external_id, employee_record in latest.items():
            conn.execute(
                """
                INSERT INTO employees(external_id, name, active)
                VALUES (?,?,1)
                ON CONFLICT(external_id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (external_id, employee_record.name),
            )

        for punch in parsed.punches:
            external_id = normalize_external_id(punch.external_id)
            employee = conn.execute(
                "SELECT id FROM employees WHERE external_id=?",
                (external_id,),
            ).fetchone()
            if not employee:
                conn.execute(
                    "INSERT INTO employees(external_id, name, active) VALUES (?,?,1)",
                    (external_id, f"Colaborador {external_id[-6:]}"),
                )

        inserted = 0
        for punch in parsed.punches:
            external_id = normalize_external_id(punch.external_id)
            employee = conn.execute(
                "SELECT id FROM employees WHERE external_id=?",
                (external_id,),
            ).fetchone()
            try:
                conn.execute(
                    """
                    INSERT INTO punches(
                        employee_id, external_id, nsr, punched_at,
                        timezone_offset, raw_line, raw_hash, import_id
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        employee["id"] if employee else None,
                        external_id,
                        punch.nsr,
                        punch.timestamp.isoformat(),
                        punch.timezone_offset,
                        punch.raw_line,
                        punch.raw_hash,
                        import_id,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # O AFD pode ser cumulativo. raw_hash mantém a importação idempotente.
                pass

        conn.execute(
            "UPDATE imports SET punch_count=? WHERE id=?",
            (inserted, import_id),
        )

    return {
        "inserted": inserted,
        "import_id": import_id,
        "max_nsr": max_nsr,
        "device_model": parsed.device_model,
        "total_lines": parsed.total_lines,
        "employee_records": len(parsed.employees),
        "warnings": parsed.warnings,
    }


def get_device(device_id: int):
    ensure_controlid_schema()
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM controlid_devices WHERE id=?",
            (device_id,),
        ).fetchone()


def list_devices(enabled_only: bool = False):
    ensure_controlid_schema()
    with connect() as conn:
        sql = "SELECT * FROM controlid_devices"
        params = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name, id"
        return conn.execute(sql, params).fetchall()


def save_device(
    device_id: Optional[int],
    name: str,
    host: str,
    port: int,
    login: str,
    password: str,
    verify_tls: bool,
    enabled: bool,
    auto_sync_minutes: int,
) -> int:
    ensure_controlid_schema()
    clean_host = normalize_host(host)
    clean_name = (name or "REP Control iD").strip()
    clean_login = (login or "admin").strip()
    clean_port = int(port or 443)
    if not (1 <= clean_port <= 65535):
        raise ControlIDError("Porta inválida.")
    allowed_intervals = {0, 5, 15, 30, 60}
    interval = int(auto_sync_minutes or 0)
    if interval not in allowed_intervals:
        interval = 0

    with connect() as conn:
        if device_id:
            existing = conn.execute(
                "SELECT * FROM controlid_devices WHERE id=?",
                (device_id,),
            ).fetchone()
            if not existing:
                raise ControlIDError("Equipamento não encontrado.")
            final_password = password if password != "" else existing["password"]
            conn.execute(
                """
                UPDATE controlid_devices
                SET name=?, host=?, port=?, login=?, password=?, verify_tls=?,
                    enabled=?, auto_sync_minutes=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    clean_name,
                    clean_host,
                    clean_port,
                    clean_login,
                    final_password,
                    1 if verify_tls else 0,
                    1 if enabled else 0,
                    interval,
                    device_id,
                ),
            )
            return int(device_id)

        cur = conn.execute(
            """
            INSERT INTO controlid_devices(
                name,host,port,login,password,verify_tls,enabled,auto_sync_minutes
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                clean_name,
                clean_host,
                clean_port,
                clean_login,
                password,
                1 if verify_tls else 0,
                1 if enabled else 0,
                interval,
            ),
        )
        return int(cur.lastrowid)


def _client_for_device(device) -> ControlIDClient:
    return ControlIDClient(
        host=device["host"],
        port=int(device["port"] or 443),
        login=device["login"],
        password=device["password"],
        verify_tls=bool(device["verify_tls"]),
    )


def test_device_connection(device_id: int) -> dict:
    device = get_device(device_id)
    if not device:
        raise ControlIDError("Equipamento não encontrado.")
    client = _client_for_device(device)
    tested_at = datetime.now().isoformat(timespec="seconds")
    try:
        client.login()
        clock = client.get_date_time()
        with connect() as conn:
            conn.execute(
                """
                UPDATE controlid_devices
                SET last_status='online', last_error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (device_id,),
            )
        return {"ok": True, "clock": clock, "tested_at": tested_at}
    except Exception as exc:
        message = str(exc)
        with connect() as conn:
            conn.execute(
                """
                UPDATE controlid_devices
                SET last_status='error', last_error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (message, device_id),
            )
        if isinstance(exc, ControlIDError):
            raise
        raise ControlIDError(message) from exc


def sync_device(device_id: int, force_full: bool = False) -> dict:
    ensure_controlid_schema()
    lock = _device_lock(device_id)
    if not lock.acquire(blocking=False):
        raise ControlIDError("Este relógio já está sendo sincronizado.")

    started_at = datetime.now().isoformat(timespec="seconds")
    sync_id = None
    try:
        device = get_device(device_id)
        if not device:
            raise ControlIDError("Equipamento não encontrado.")
        if not int(device["enabled"]):
            raise ControlIDError("Este equipamento está desativado.")

        last_nsr = int(device["last_nsr"] or 0)
        initial_nsr = None if force_full else (last_nsr + 1 if last_nsr > 0 else 1)
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO controlid_syncs(
                    device_id,started_at,requested_initial_nsr,status,message
                ) VALUES (?,?,?,?,?)
                """,
                (device_id, started_at, initial_nsr, "running", "Sincronização iniciada"),
            )
            sync_id = int(cur.lastrowid)

        client = _client_for_device(device)
        client.login()
        raw = client.get_afd(initial_nsr=initial_nsr)
        result = _import_afd_bytes(device_id, device["name"], raw)
        returned_max_nsr = int(result.get("max_nsr") or 0)
        effective_nsr = max(last_nsr, returned_max_nsr)
        inserted = int(result.get("inserted") or 0)
        finished_at = datetime.now().isoformat(timespec="seconds")
        model = result.get("device_model") or device["device_model"]
        message = (
            f"Sincronização concluída: {inserted} batida(s) nova(s). "
            f"Último NSR {effective_nsr}."
        )

        with connect() as conn:
            conn.execute(
                """
                UPDATE controlid_devices
                SET last_nsr=?, last_sync_at=?, last_success_at=?,
                    last_status='ok', last_error=NULL, last_new_punches=?,
                    device_model=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    effective_nsr,
                    finished_at,
                    finished_at,
                    inserted,
                    model,
                    device_id,
                ),
            )
            conn.execute(
                """
                UPDATE controlid_syncs
                SET finished_at=?, returned_max_nsr=?, inserted_punches=?,
                    status='ok', message=?, import_id=?
                WHERE id=?
                """,
                (
                    finished_at,
                    effective_nsr,
                    inserted,
                    message,
                    result.get("import_id"),
                    sync_id,
                ),
            )

        return {
            "ok": True,
            "inserted": inserted,
            "last_nsr": effective_nsr,
            "message": message,
            "device_model": model,
        }
    except Exception as exc:
        finished_at = datetime.now().isoformat(timespec="seconds")
        message = str(exc)
        with connect() as conn:
            conn.execute(
                """
                UPDATE controlid_devices
                SET last_sync_at=?, last_status='error', last_error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (finished_at, message, device_id),
            )
            if sync_id:
                conn.execute(
                    """
                    UPDATE controlid_syncs
                    SET finished_at=?, status='error', message=?
                    WHERE id=?
                    """,
                    (finished_at, message, sync_id),
                )
        if isinstance(exc, ControlIDError):
            raise
        raise ControlIDError(message) from exc
    finally:
        lock.release()


def recent_syncs(limit: int = 20):
    ensure_controlid_schema()
    with connect() as conn:
        return conn.execute(
            """
            SELECT s.*, d.name AS device_name
            FROM controlid_syncs s
            JOIN controlid_devices d ON d.id=s.device_id
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 20), 100)),),
        ).fetchall()


def device_status_summary() -> dict:
    devices = list_devices(enabled_only=True)
    if not devices:
        return {"configured": False, "count": 0}
    primary = devices[0]
    return {
        "configured": True,
        "count": len(devices),
        "id": int(primary["id"]),
        "name": primary["name"],
        "host": primary["host"],
        "last_status": primary["last_status"] or "never",
        "last_success_at": primary["last_success_at"],
        "last_new_punches": int(primary["last_new_punches"] or 0),
        "last_nsr": int(primary["last_nsr"] or 0),
        "auto_sync_minutes": int(primary["auto_sync_minutes"] or 0),
        "device_model": primary["device_model"],
        "last_error": primary["last_error"],
    }


ensure_controlid_schema()
