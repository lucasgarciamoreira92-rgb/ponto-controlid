from __future__ import annotations
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional
import hashlib
import shutil
import sqlite3

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from db import init_db, connect, get_settings
from parser_afd import parse_afd, normalize_external_id
from calc import analyze_day, derive_expected_minutes, hm, month_dates

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Ponto Control iD")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["hm"] = hm
init_db()

WEEKDAYS = [(0, "Segunda"), (1, "Terça"), (2, "Quarta"), (3, "Quinta"), (4, "Sexta"), (5, "Sábado"), (6, "Domingo")]


def _month_value(value: Optional[str]) -> str:
    if value and len(value) == 7:
        return value
    return datetime.now().strftime("%Y-%m")


def _schedules_by_weekday(conn, employee_id: int):
    rows = conn.execute("SELECT * FROM schedules WHERE employee_id=? ORDER BY weekday", (employee_id,)).fetchall()
    return {int(r["weekday"]): r for r in rows}


def _holiday_set(conn):
    return {r["holiday_date"] for r in conn.execute("SELECT holiday_date FROM holidays").fetchall()}


def _load_employee_month(conn, employee, month: str):
    year, mon = map(int, month.split("-"))
    prefix = f"{month}-"
    punches = conn.execute(
        "SELECT punched_at FROM punches WHERE employee_id=? AND punched_at LIKE ? ORDER BY punched_at",
        (employee["id"], prefix + "%"),
    ).fetchall()
    by_day = defaultdict(list)
    for row in punches:
        dt = datetime.fromisoformat(row["punched_at"])
        by_day[dt.date()].append(dt)

    schedules = _schedules_by_weekday(conn, employee["id"])
    settings = get_settings(conn)
    holidays = _holiday_set(conn)

    days = []
    for d in month_dates(year, mon):
        schedule = schedules.get(d.weekday())
        # Exibe dias com marcação e dias de trabalho configurados.
        if by_day.get(d) or (schedule and int(schedule["is_workday"])):
            days.append(analyze_day(d, by_day.get(d, []), schedule, settings, holidays))
    return days


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, month: Optional[str] = None):
    month = _month_value(month)
    with connect() as conn:
        employees = conn.execute("SELECT * FROM employees WHERE active=1 ORDER BY name").fetchall()
        summaries = []
        totals = {"worked": 0, "overtime_weekday": 0, "overtime_saturday": 0, "overtime_sunday_holiday": 0, "shortage": 0, "bank": 0, "issues": 0}
        for e in employees:
            days = _load_employee_month(conn, e, month)
            s = {
                "employee": e,
                "worked": sum(d["worked"] for d in days),
                "overtime_weekday": sum(d["overtime_weekday"] for d in days),
                "overtime_saturday": sum(d["overtime_saturday"] for d in days),
                "overtime_sunday_holiday": sum(d["overtime_sunday_holiday"] for d in days),
                "shortage": sum(d["shortage"] for d in days),
                "bank": sum(d["bank"] for d in days),
                "issues": sum(1 for d in days if d["status"] != "OK"),
            }
            summaries.append(s)
            for k in totals:
                totals[k] += s[k] if k in s else 0
        last_import = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
        settings = get_settings(conn)
    return templates.TemplateResponse("dashboard.html", {"request": request, "month": month, "summaries": summaries, "totals": totals, "last_import": last_import, "settings": settings})


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    with connect() as conn:
        imports = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()
    return templates.TemplateResponse("import.html", {"request": request, "imports": imports})


@app.post("/import")
async def import_afd(file: UploadFile = File(...)):
    if not file.filename:
        return RedirectResponse("/import?error=arquivo", status_code=303)
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    with connect() as conn:
        if conn.execute("SELECT id FROM imports WHERE file_hash=?", (file_hash,)).fetchone():
            return RedirectResponse("/import?duplicate=1", status_code=303)

    safe_name = "".join(c for c in Path(file.filename).name if c.isalnum() or c in "._- ()")
    stored_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    stored_path = UPLOAD_DIR / stored_name
    stored_path.write_bytes(content)
    parsed = parse_afd(stored_path)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO imports(original_filename, stored_filename, file_hash, device_model, total_lines, punch_count, employee_records, notes) VALUES (?,?,?,?,?,?,?,?)",
            (file.filename, stored_name, file_hash, parsed.device_model, parsed.total_lines, len(parsed.punches), len(parsed.employees), "\n".join(parsed.warnings[:20])),
        )
        import_id = cur.lastrowid

        # Mantém o cadastro mais recente encontrado no AFD para cada identificador.
        latest = {}
        for er in parsed.employees:
            latest[er.external_id] = er
        for ext, er in latest.items():
            conn.execute(
                "INSERT INTO employees(external_id, name, active) VALUES (?,?,1) ON CONFLICT(external_id) DO UPDATE SET name=excluded.name, updated_at=CURRENT_TIMESTAMP",
                (ext, er.name),
            )
        # Alguns AFDs podem conter batidas de pessoas cujo registro 5 não está no recorte exportado.
        for p in parsed.punches:
            row = conn.execute("SELECT id FROM employees WHERE external_id=?", (p.external_id,)).fetchone()
            if not row:
                conn.execute("INSERT INTO employees(external_id, name, active) VALUES (?,?,1)", (p.external_id, f"Colaborador {p.external_id[-6:]}"))

        inserted = 0
        for p in parsed.punches:
            employee = conn.execute("SELECT id FROM employees WHERE external_id=?", (p.external_id,)).fetchone()
            try:
                conn.execute(
                    "INSERT INTO punches(employee_id, external_id, nsr, punched_at, timezone_offset, raw_line, raw_hash, import_id) VALUES (?,?,?,?,?,?,?,?)",
                    (employee["id"] if employee else None, p.external_id, p.nsr, p.timestamp.isoformat(), p.timezone_offset, p.raw_line, p.raw_hash, import_id),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Mesma batida já importada em outro AFD acumulado.
                pass
        conn.execute("UPDATE imports SET punch_count=? WHERE id=?", (inserted, import_id))
    return RedirectResponse(f"/import?ok={inserted}", status_code=303)


@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request):
    with connect() as conn:
        employees = conn.execute("SELECT e.*, COUNT(p.id) AS punch_count FROM employees e LEFT JOIN punches p ON p.employee_id=e.id GROUP BY e.id ORDER BY e.active DESC, e.name").fetchall()
    return templates.TemplateResponse("employees.html", {"request": request, "employees": employees})


@app.get("/employees/new", response_class=HTMLResponse)
def employee_new(request: Request):
    return templates.TemplateResponse("employee_form.html", {"request": request, "employee": None, "schedules": {}, "weekdays": WEEKDAYS})


@app.get("/employees/{employee_id}/edit", response_class=HTMLResponse)
def employee_edit(request: Request, employee_id: int):
    with connect() as conn:
        employee = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            raise HTTPException(404)
        schedules = _schedules_by_weekday(conn, employee_id)
    return templates.TemplateResponse("employee_form.html", {"request": request, "employee": employee, "schedules": schedules, "weekdays": WEEKDAYS})


@app.post("/employees/save")
async def employee_save(request: Request):
    form = await request.form()
    employee_id_raw = form.get("employee_id")
    employee_id = int(employee_id_raw) if employee_id_raw else None
    name = str(form.get("name", "")).strip()
    external_id = normalize_external_id(str(form.get("external_id", "")))
    active = 1 if form.get("active") else 0
    if not name:
        return RedirectResponse("/employees?error=name", status_code=303)
    with connect() as conn:
        try:
            if employee_id:
                conn.execute("UPDATE employees SET name=?, external_id=?, active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, external_id or None, active, employee_id))
            else:
                cur = conn.execute("INSERT INTO employees(external_id, name, active) VALUES (?,?,?)", (external_id or None, name, active))
                employee_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return RedirectResponse("/employees?error=external_id", status_code=303)

        for weekday, _label in WEEKDAYS:
            prefix = f"d{weekday}_"
            is_workday = 1 if form.get(prefix + "active") else 0
            start1 = str(form.get(prefix + "start1", "")).strip() or None
            end1 = str(form.get(prefix + "end1", "")).strip() or None
            start2 = str(form.get(prefix + "start2", "")).strip() or None
            end2 = str(form.get(prefix + "end2", "")).strip() or None
            fallback_hours = str(form.get(prefix + "hours", "0") or "0").replace(",", ".")
            try:
                fallback_minutes = int(round(float(fallback_hours) * 60))
            except ValueError:
                fallback_minutes = 0
            expected = derive_expected_minutes(start1, end1, start2, end2, fallback_minutes) if is_workday else 0
            conn.execute(
                "INSERT INTO schedules(employee_id, weekday, is_workday, start1, end1, start2, end2, expected_minutes) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(employee_id,weekday) DO UPDATE SET is_workday=excluded.is_workday,start1=excluded.start1,end1=excluded.end1,start2=excluded.start2,end2=excluded.end2,expected_minutes=excluded.expected_minutes",
                (employee_id, weekday, is_workday, start1, end1, start2, end2, expected),
            )
    return RedirectResponse(f"/employees/{employee_id}/edit?saved=1", status_code=303)


@app.post("/employees/{employee_id}/toggle")
def employee_toggle(employee_id: int):
    with connect() as conn:
        row = conn.execute("SELECT active FROM employees WHERE id=?", (employee_id,)).fetchone()
        if row:
            conn.execute("UPDATE employees SET active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (0 if row["active"] else 1, employee_id))
    return RedirectResponse("/employees", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    with connect() as conn:
        settings = get_settings(conn)
        holidays = conn.execute("SELECT * FROM holidays ORDER BY holiday_date").fetchall()
    holiday_text = "\n".join(f"{h['holiday_date']} | {h['description'] or ''}" for h in holidays)
    return templates.TemplateResponse("settings.html", {"request": request, "settings": settings, "holiday_text": holiday_text})


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    keys = [
        "company_name", "daily_tolerance_minutes", "overtime_weekday_percent", "overtime_saturday_percent",
        "overtime_sunday_holiday_percent", "min_interval_minutes", "night_start", "night_end"
    ]
    checkbox_keys = ["bank_hours_enabled", "night_shift_enabled", "consider_holidays"]
    with connect() as conn:
        for key in keys:
            conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(form.get(key, ""))))
        for key in checkbox_keys:
            value = "1" if form.get(key) else "0"
            conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.execute("DELETE FROM holidays")
        for raw in str(form.get("holidays", "")).splitlines():
            raw = raw.strip()
            if not raw:
                continue
            parts = [p.strip() for p in raw.split("|", 1)]
            try:
                datetime.strptime(parts[0], "%Y-%m-%d")
            except ValueError:
                continue
            desc = parts[1] if len(parts) > 1 else ""
            conn.execute("INSERT OR IGNORE INTO holidays(holiday_date, description) VALUES (?,?)", (parts[0], desc))
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/report/{employee_id}", response_class=HTMLResponse)
def employee_report(request: Request, employee_id: int, month: Optional[str] = None):
    month = _month_value(month)
    with connect() as conn:
        employee = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            raise HTTPException(404)
        days = _load_employee_month(conn, employee, month)
        settings = get_settings(conn)
    totals = {
        "worked": sum(d["worked"] for d in days),
        "expected": sum(d["expected"] for d in days),
        "overtime_weekday": sum(d["overtime_weekday"] for d in days),
        "overtime_saturday": sum(d["overtime_saturday"] for d in days),
        "overtime_sunday_holiday": sum(d["overtime_sunday_holiday"] for d in days),
        "shortage": sum(d["shortage"] for d in days),
        "bank": sum(d["bank"] for d in days),
    }
    return templates.TemplateResponse("employee_report.html", {"request": request, "employee": employee, "month": month, "days": days, "totals": totals, "settings": settings})


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
