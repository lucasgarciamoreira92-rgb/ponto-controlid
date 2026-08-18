from __future__ import annotations
import sqlite3
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "ponto.db"

DEFAULT_SETTINGS = {
    "company_name": "Controle de Ponto",
    "daily_tolerance_minutes": "10",
    "overtime_weekday_percent": "50",
    "overtime_saturday_percent": "50",
    "overtime_sunday_holiday_percent": "100",
    "bank_hours_enabled": "0",
    "min_interval_minutes": "60",
    "night_shift_enabled": "0",
    "night_start": "22:00",
    "night_end": "05:00",
    "consider_holidays": "1",
}

SCHEMA = r'''
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    is_workday INTEGER NOT NULL DEFAULT 0,
    start1 TEXT,
    end1 TEXT,
    start2 TEXT,
    end2 TEXT,
    expected_minutes INTEGER NOT NULL DEFAULT 0,
    UNIQUE(employee_id, weekday),
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS monthly_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    is_workday INTEGER NOT NULL DEFAULT 0,
    start1 TEXT,
    end1 TEXT,
    start2 TEXT,
    end2 TEXT,
    expected_minutes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(employee_id, month, weekday),
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS month_closures (
    month TEXT PRIMARY KEY,
    finalized_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settings_json TEXT NOT NULL,
    holidays_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date TEXT UNIQUE NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    file_hash TEXT UNIQUE NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    device_model TEXT,
    total_lines INTEGER NOT NULL DEFAULT 0,
    punch_count INTEGER NOT NULL DEFAULT 0,
    employee_records INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS punches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER,
    external_id TEXT NOT NULL,
    nsr INTEGER,
    punched_at TEXT NOT NULL,
    timezone_offset TEXT,
    raw_line TEXT NOT NULL,
    raw_hash TEXT UNIQUE NOT NULL,
    import_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE SET NULL,
    FOREIGN KEY(import_id) REFERENCES imports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_punches_employee_date ON punches(employee_id, punched_at);
CREATE INDEX IF NOT EXISTS idx_punches_external_date ON punches(external_id, punched_at);
CREATE INDEX IF NOT EXISTS idx_monthly_schedules_employee_month ON monthly_schedules(employee_id, month);
'''

@contextmanager
def connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}
