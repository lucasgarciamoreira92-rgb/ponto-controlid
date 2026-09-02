from __future__ import annotations

from io import BytesIO
from typing import Optional

from fastapi.responses import RedirectResponse, Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import (
    _load_employee_month,
    _month_closure,
    _month_value,
    _settings_for_month,
    app,
    connect,
)
from pdf_reports_v2 import (
    INK,
    MUTED,
    WARN,
    WHITE,
    _draw_employee_card,
    _draw_header,
    _draw_summary,
    _draw_table,
    _month_label,
)


def _participant_employees(conn, month: str):
    return conn.execute(
        """
        SELECT DISTINCT e.*
        FROM employees e
        WHERE e.active=1
           OR EXISTS(
               SELECT 1 FROM punches p
               WHERE p.employee_id=e.id
                 AND p.punched_at LIKE ?
           )
           OR EXISTS(
               SELECT 1 FROM monthly_schedules ms
               WHERE ms.employee_id=e.id
                 AND ms.month=?
           )
        ORDER BY e.name
        """,
        (month + "-%", month),
    ).fetchall()


def _missing_month_schedules(conn, employees, month: str) -> list[int]:
    missing = []
    for employee in employees:
        configured = conn.execute(
            "SELECT 1 FROM monthly_schedules WHERE employee_id=? AND month=? LIMIT 1",
            (employee["id"], month),
        ).fetchone()
        if not configured:
            missing.append(int(employee["id"]))
    return missing


def _report_data(conn, employee, month: str) -> dict:
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
    return {
        "employee": dict(employee),
        "days": days,
        "totals": totals,
        "absence_count": sum(1 for d in days if d.get("absence")),
        "forgotten_count": sum(1 for d in days if d.get("forgotten_punch")),
    }


def _draw_preview_badge(c: canvas.Canvas) -> None:
    width, height = A4
    label = "PRÉVIA - EM ABERTO"
    x = width - 57 * mm
    y = height - 23.1 * mm
    c.setFillColor(WARN)
    c.roundRect(x, y, 43 * mm, 5 * mm, 1.2 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 5.2)
    c.drawCentredString(x + 21.5 * mm, y + 1.65 * mm, label)


def _draw_preview_footer(
    c: canvas.Canvas,
    y_top: float,
    page_number: int,
    page_count: int,
) -> None:
    width, _ = A4
    left = 9 * mm
    right = width - 9 * mm
    box_y = max(9 * mm, y_top - 17 * mm)
    box_h = 13 * mm

    c.setFillColor(WHITE)
    c.setStrokeColor(WARN)
    c.setLineWidth(0.7)
    c.roundRect(left, box_y, right - left, box_h, 2 * mm, fill=1, stroke=1)
    c.setFillColor(WARN)
    c.setFont("Helvetica-Bold", 6.1)
    c.drawString(left + 4 * mm, box_y + 7.7 * mm, "PRÉVIA GERAL - COMPETÊNCIA EM ABERTO")
    c.setFillColor(INK)
    c.setFont("Helvetica", 5.1)
    c.drawString(
        left + 4 * mm,
        box_y + 3.4 * mm,
        "Documento para conferência. Os dados ainda podem mudar e esta prévia não representa o fechamento da competência.",
    )

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 4.7)
    c.drawCentredString(width / 2, 5 * mm, f"Página {page_number} de {page_count}")


def build_monthly_preview_pdf(
    month: str,
    settings: dict,
    reports: list[dict],
) -> bytes:
    reports = list(reports)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    company_name = str(settings.get("company_name") or "Controle de Ponto")

    c.setTitle(f"Prévia geral - espelho mensal de ponto - {_month_label(month)}")
    c.setAuthor(company_name)
    c.setSubject("Prévia consolidada da competência em aberto - Atlas Ponto")

    if not reports:
        width, height = A4
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(18 * mm, height - 28 * mm, "Prévia geral de ponto")
        c.setFont("Helvetica", 9)
        c.drawString(
            18 * mm,
            height - 38 * mm,
            f"Competência {_month_label(month)} sem colaboradores para emissão.",
        )
        c.save()
        return buffer.getvalue()

    page_count = len(reports)
    for index, report in enumerate(reports, start=1):
        y = _draw_header(c, month, finalized=False)
        _draw_preview_badge(c)
        y = _draw_employee_card(c, y, report, company_name, month)
        y = _draw_summary(c, y, report, settings)
        y = _draw_table(c, y, report, month, settings)
        _draw_preview_footer(c, y, index, page_count)
        c.showPage()

    c.save()
    return buffer.getvalue()


@app.get("/reports/monthly-preview.pdf", include_in_schema=False)
def monthly_general_preview(month: Optional[str] = None):
    month_value = _month_value(month)

    with connect() as conn:
        closure = _month_closure(conn, month_value)
        if closure:
            return RedirectResponse(
                f"/closures/{month_value}/pdf",
                status_code=303,
            )

        employees = _participant_employees(conn, month_value)
        missing_ids = _missing_month_schedules(conn, employees, month_value)
        if missing_ids:
            ids = ",".join(str(value) for value in missing_ids)
            return RedirectResponse(
                (
                    f"/month-schedules?month={month_value}"
                    f"&error=preview_missing&missing={len(missing_ids)}"
                    f"&missing_ids={ids}"
                ),
                status_code=303,
            )

        settings = _settings_for_month(conn, month_value, None)
        reports = [
            _report_data(conn, employee, month_value)
            for employee in employees
        ]

    pdf_bytes = build_monthly_preview_pdf(month_value, settings, reports)
    filename = f"previa_geral_ponto_{month_value}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
