from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import app, templates, connect, WEEKDAYS, derive_expected_minutes
from schedule_inference import infer_employee_schedule


@app.get("/employees/auto-schedule", response_class=HTMLResponse)
def employee_auto_schedule_page(request: Request, days: int = 90):
    lookback_days = max(30, min(int(days or 90), 365))
    suggestions = []
    with connect() as conn:
        employees = conn.execute(
            "SELECT * FROM employees WHERE active=1 ORDER BY name"
        ).fetchall()
        for employee in employees:
            rows = conn.execute(
                "SELECT punched_at FROM punches WHERE employee_id=? ORDER BY punched_at",
                (employee["id"],),
            ).fetchall()
            inference = infer_employee_schedule(
                [row["punched_at"] for row in rows],
                lookback_days=lookback_days,
            )
            suggestions.append({"employee": employee, "inference": inference})

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
                fallback_hours = str(
                    form.get(prefix + "hours", "0") or "0"
                ).replace(",", ".")
                try:
                    fallback_minutes = int(round(float(fallback_hours) * 60))
                except ValueError:
                    fallback_minutes = 0

                expected = (
                    derive_expected_minutes(
                        start1, end1, start2, end2, fallback_minutes
                    )
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
