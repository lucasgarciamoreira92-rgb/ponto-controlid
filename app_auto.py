from __future__ import annotations

from collections import Counter

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import app, templates, connect, WEEKDAYS, derive_expected_minutes
from schedule_inference import infer_employee_schedule


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
