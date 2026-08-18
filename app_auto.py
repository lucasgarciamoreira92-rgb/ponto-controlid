from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import (
    BASE_DIR,
    WEEKDAYS,
    _load_employee_month,
    _month_closure,
    _settings_for_month,
    app,
    connect,
    derive_expected_minutes,
    templates,
)
from pdf_reports import build_monthly_pdf
from schedule_inference import infer_employee_schedule


CLOSED_REPORTS_DIR = BASE_DIR / "data" / "closed_reports"
CLOSED_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MONTH_NAMES = {
    "01": "Janeiro",
    "02": "Fevereiro",
    "03": "Março",
    "04": "Abril",
    "05": "Maio",
    "06": "Junho",
    "07": "Julho",
    "08": "Agosto",
    "09": "Setembro",
    "10": "Outubro",
    "11": "Novembro",
    "12": "Dezembro",
}


def _schedule_dict(row) -> dict:
    return {
        "weekday": int(row["weekday"]),
        "active": bool(row["is_workday"]),
        "start1": row["start1"] or "",
        "end1": row["end1"] or "",
        "start2": row["start2"] or "",
        "end2": row["end2"] or "",
        "expected_minutes": int(row["expected_minutes"] or 0),
        "sample_count": 0,
        "confidence_score": 0,
        "confidence_label": "Jornada atual",
    }


def _empty_day(weekday: int) -> dict:
    return {
        "weekday": weekday,
        "active": False,
        "start1": "",
        "end1": "",
        "start2": "",
        "end2": "",
        "expected_minutes": 0,
        "sample_count": 0,
        "confidence_score": 0,
        "confidence_label": "Sem dados",
    }


def _pattern(day: dict) -> tuple:
    return (
        day.get("start1") or "",
        day.get("end1") or "",
        day.get("start2") or "",
        day.get("end2") or "",
        int(day.get("expected_minutes") or 0),
    )


def _quick_prefill(days: dict[int, dict], protect_existing: bool) -> tuple[dict, set[int]]:
    active_days = [(wd, d) for wd, d in days.items() if d and d.get("active")]
    if not active_days:
        return {
            "start1": "",
            "end1": "",
            "start2": "",
            "end2": "",
            "hours": "",
        }, ({0, 1, 2, 3, 4, 5} if not protect_existing else set())

    counts = Counter(_pattern(day) for _wd, day in active_days)
    common_pattern, _count = counts.most_common(1)[0]
    matching_days = {wd for wd, day in active_days if _pattern(day) == common_pattern}

    start1, end1, start2, end2, expected = common_pattern
    quick = {
        "start1": start1,
        "end1": end1,
        "start2": start2,
        "end2": end2,
        "hours": f"{expected / 60:.2f}" if expected else "",
    }

    # Para jornada já cadastrada, seleciona apenas os dias que já possuem o padrão
    # predominante. Assim um sábado ou outro dia especial não é sobrescrito por engano.
    if protect_existing:
        selected = matching_days
    else:
        selected = {0, 1, 2, 3, 4, 5}
    return quick, selected


def _validate_closed_month(month: str) -> str:
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(404, "Competência inválida") from exc
    return month


def _closed_month_employees(conn, month: str, closure):
    return conn.execute(
        """
        SELECT DISTINCT e.*
        FROM employees e
        WHERE EXISTS(
            SELECT 1
            FROM monthly_schedules ms
            WHERE ms.employee_id=e.id AND ms.month=?
        )
        OR EXISTS(
            SELECT 1
            FROM punches p
            WHERE p.employee_id=e.id
              AND p.punched_at LIKE ?
              AND p.id <= ?
        )
        ORDER BY e.name
        """,
        (month, month + "-%", int(closure["punch_cutoff_id"] or 0)),
    ).fetchall()


def _month_label(month: str) -> str:
    year, mon = month.split("-", 1)
    return f"{MONTH_NAMES.get(mon, mon)}/{year}"


def _archive_path(month: str) -> Path:
    return CLOSED_REPORTS_DIR / f"espelho_ponto_{month}.pdf"


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _closed_month_report_data(conn, month: str, closure):
    settings = _settings_for_month(conn, month, closure)
    employees = _closed_month_employees(conn, month, closure)
    reports = []

    for employee in employees:
        days = _load_employee_month(conn, employee, month)
        totals = {
            "worked": sum(d["worked"] for d in days),
            "overtime_weekday": sum(d["overtime_weekday"] for d in days),
            "overtime_saturday": sum(d["overtime_saturday"] for d in days),
            "overtime_sunday_holiday": sum(
                d["overtime_sunday_holiday"] for d in days
            ),
            "shortage": sum(d["shortage"] for d in days),
            "bank": sum(d["bank"] for d in days),
        }
        reports.append(
            {
                "employee": dict(employee),
                "days": days,
                "totals": totals,
                "absence_count": sum(1 for d in days if d.get("absence")),
                "forgotten_count": sum(
                    1 for d in days if d.get("forgotten_punch")
                ),
            }
        )

    return settings, reports


def _ensure_closed_month_pdf(month: str) -> Path:
    month = _validate_closed_month(month)
    path = _archive_path(month)
    if path.exists() and path.stat().st_size > 0:
        return path

    with connect() as conn:
        closure = _month_closure(conn, month)
        if not closure:
            raise HTTPException(404, "Competência ainda não foi finalizada")
        settings, reports = _closed_month_report_data(conn, month, closure)
        pdf_bytes = build_monthly_pdf(month, closure, settings, reports)

    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(pdf_bytes)
    tmp_path.replace(path)
    return path


@app.get("/closures", response_class=HTMLResponse)
def closed_competences_page(request: Request):
    items = []
    with connect() as conn:
        closures = conn.execute(
            "SELECT * FROM month_closures ORDER BY month DESC"
        ).fetchall()
        for closure in closures:
            month = str(closure["month"])
            employee_count = len(_closed_month_employees(conn, month, closure))
            path = _archive_path(month)
            archived = path.exists() and path.stat().st_size > 0
            items.append(
                {
                    "month": month,
                    "label": _month_label(month),
                    "finalized_at": closure["finalized_at"],
                    "employee_count": employee_count,
                    "archived": archived,
                    "file_size": _human_size(path.stat().st_size) if archived else "",
                }
            )

    return templates.TemplateResponse(
        "closures.html",
        {
            "request": request,
            "closures": items,
        },
    )


@app.get("/closures/{month}/pdf")
def closed_competence_pdf(month: str):
    path = _ensure_closed_month_pdf(month)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"espelho_ponto_{month}.pdf",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/employees/auto-schedule", response_class=HTMLResponse)
def employee_auto_schedule_page(request: Request, days: int = 90):
    lookback_days = max(30, min(int(days or 90), 365))
    suggestions = []

    with connect() as conn:
        employees = conn.execute(
            "SELECT * FROM employees WHERE active=1 ORDER BY name"
        ).fetchall()

        for employee in employees:
            punch_rows = conn.execute(
                "SELECT punched_at FROM punches WHERE employee_id=? ORDER BY punched_at",
                (employee["id"],),
            ).fetchall()
            inference = infer_employee_schedule(
                [row["punched_at"] for row in punch_rows],
                lookback_days=lookback_days,
            )

            saved_rows = conn.execute(
                "SELECT * FROM schedules WHERE employee_id=? ORDER BY weekday",
                (employee["id"],),
            ).fetchall()
            saved_days = {int(row["weekday"]): _schedule_dict(row) for row in saved_rows}
            has_existing_schedule = bool(saved_rows)

            if has_existing_schedule:
                initial_days = {
                    wd: saved_days.get(wd, _empty_day(wd)) for wd, _label in WEEKDAYS
                }
            else:
                initial_days = {
                    wd: inference["days"].get(wd, _empty_day(wd))
                    for wd, _label in WEEKDAYS
                }

            quick, quick_days = _quick_prefill(
                initial_days,
                protect_existing=has_existing_schedule,
            )

            suggestions.append(
                {
                    "employee": employee,
                    "inference": inference,
                    "has_existing_schedule": has_existing_schedule,
                    "saved_days": saved_days,
                    "initial_days": initial_days,
                    "quick": quick,
                    "quick_days": quick_days,
                }
            )

    return templates.TemplateResponse(
        "employee_auto_schedule.html",
        {
            "request": request,
            "suggestions": suggestions,
            "weekdays": WEEKDAYS,
            "lookback_days": lookback_days,
        },
    )


@app.post("/employees/auto-schedule")
async def employee_auto_schedule_save(request: Request):
    form = await request.form()
    saved = 0
    skipped = 0

    raw_ids = str(form.get("employee_ids", ""))
    employee_ids = []
    for raw in raw_ids.split(","):
        raw = raw.strip()
        if raw.isdigit():
            employee_ids.append(int(raw))

    with connect() as conn:
        for employee_id in employee_ids:
            approved = str(form.get(f"e{employee_id}_approved", "0")) == "1"
            if not approved:
                skipped += 1
                continue

            employee = conn.execute(
                "SELECT id FROM employees WHERE id=? AND active=1",
                (employee_id,),
            ).fetchone()
            if not employee:
                skipped += 1
                continue

            for weekday, _label in WEEKDAYS:
                prefix = f"e{employee_id}_d{weekday}_"
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

                expected = (
                    derive_expected_minutes(start1, end1, start2, end2, fallback_minutes)
                    if is_workday
                    else 0
                )

                conn.execute(
                    """
                    INSERT INTO schedules(
                        employee_id, weekday, is_workday, start1, end1,
                        start2, end2, expected_minutes
                    )
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(employee_id,weekday) DO UPDATE SET
                        is_workday=excluded.is_workday,
                        start1=excluded.start1,
                        end1=excluded.end1,
                        start2=excluded.start2,
                        end2=excluded.end2,
                        expected_minutes=excluded.expected_minutes
                    """,
                    (
                        employee_id,
                        weekday,
                        is_workday,
                        start1,
                        end1,
                        start2,
                        end2,
                        expected,
                    ),
                )
            saved += 1

    return RedirectResponse(
        f"/employees?auto_saved={saved}&auto_skipped={skipped}",
        status_code=303,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
