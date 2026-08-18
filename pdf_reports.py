from __future__ import annotations

from calendar import monthrange
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


PDF_LAYOUT_VERSION = "atlas_portrait_v2"
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
WEEKDAY_ABBR = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

INK = colors.HexColor("#171B20")
OLIVE = colors.HexColor("#566D2F")
OLIVE_DARK = colors.HexColor("#405421")
OLIVE_PALE = colors.HexColor("#F0F3EA")
MUTED = colors.HexColor("#6A716A")
LINE = colors.HexColor("#D7DCD4")
SOFT = colors.HexColor("#F8F9F6")
WARN = colors.HexColor("#D97745")
DEBIT = colors.HexColor("#BE332C")
WHITE = colors.white


def _invalidate_legacy_archives() -> None:
    """Migração única para reemitir competências antigas no layout aprovado."""
    archive_dir = Path(__file__).resolve().parent / "data" / "closed_reports"
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        marker = archive_dir / f".{PDF_LAYOUT_VERSION}"
        if marker.exists():
            return
        for old_pdf in archive_dir.glob("espelho_ponto_*.pdf"):
            try:
                old_pdf.unlink()
            except OSError:
                pass
        marker.write_text(PDF_LAYOUT_VERSION, encoding="utf-8")
    except OSError:
        pass


_invalidate_legacy_archives()


def _month_label(month: str) -> str:
    year, mon = month.split("-", 1)
    return f"{MONTH_NAMES.get(mon, mon)} / {year}"


def _hm(minutes: int | float | None) -> str:
    if minutes is None:
        return "-"
    value = int(round(minutes))
    sign = "-" if value < 0 else ""
    value = abs(value)
    hours, mins = divmod(value, 60)
    return f"{sign}{hours:02d}:{mins:02d}"


def _fit_text(c: canvas.Canvas, text: str, max_width: float, size: float, *, bold: bool = False, min_size: float = 5.6) -> float:
    font = "Helvetica-Bold" if bold else "Helvetica"
    current = size
    while current > min_size and stringWidth(str(text), font, current) > max_width:
        current -= 0.2
    c.setFont(font, current)
    return current


def _clip_text(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _observation(day: dict | None) -> str:
    if not day:
        return "-"
    if day.get("absence"):
        return "Falta"
    if day.get("forgotten_punch"):
        return "Batida incompleta"
    status = str(day.get("status") or "")
    if "Intervalo" in status:
        return status
    if day.get("holiday"):
        return "Feriado"
    return "-"


def _punches(day: dict | None) -> str:
    if not day:
        return "-"
    values = []
    for punch in day.get("punches") or []:
        try:
            values.append(punch.strftime("%H:%M"))
        except AttributeError:
            values.append(str(punch))
    return " | ".join(values) if values else "-"


def _calendar_rows(month: str, days: list[dict]) -> list[tuple[date, dict | None]]:
    year, mon = map(int, month.split("-"))
    by_date = {d["date"]: d for d in days}
    return [
        (date(year, mon, day_number), by_date.get(date(year, mon, day_number)))
        for day_number in range(1, monthrange(year, mon)[1] + 1)
    ]


def _round_rect(c: canvas.Canvas, x: float, y: float, w: float, h: float, *, fill=WHITE, stroke=LINE, radius=2.2 * mm) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.55)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)


def _draw_atlas_mark(c: canvas.Canvas, x: float, y: float) -> None:
    c.setFillColor(OLIVE)
    c.setFont("Helvetica-Bold", 25)
    c.drawString(x, y, "A")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(x + 10.5 * mm, y + 1.0 * mm, "ATLAS")
    c.setFillColor(OLIVE)
    c.setFont("Helvetica-Bold", 6.2)
    c.drawString(x + 10.8 * mm, y - 3.6 * mm, "PONTO & JORNADA")


def _draw_header(c: canvas.Canvas, month: str, finalized: bool) -> float:
    width, height = A4
    left = 9 * mm
    right = width - 9 * mm
    top = height - 9 * mm

    _draw_atlas_mark(c, left, top - 10.0 * mm)
    divider_x = left + 50 * mm
    c.setStrokeColor(colors.HexColor("#ABB1AA"))
    c.setLineWidth(0.6)
    c.line(divider_x, top - 14 * mm, divider_x, top + 1 * mm)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12.4)
    c.drawString(divider_x + 5 * mm, top - 5.2 * mm, "ESPELHO MENSAL DE PONTO")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.1)
    c.drawString(divider_x + 5 * mm, top - 9.2 * mm, "Registro oficial de jornada de trabalho")
    c.drawString(divider_x + 5 * mm, top - 12.5 * mm, "Baseado nas marcações do AFD")

    competence_x = right - 48 * mm
    c.setStrokeColor(colors.HexColor("#ABB1AA"))
    c.line(competence_x - 5 * mm, top - 14 * mm, competence_x - 5 * mm, top + 1 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 5.4)
    c.drawString(competence_x, top - 1.2 * mm, "COMPETÊNCIA")
    c.setFillColor(INK)
    _fit_text(c, _month_label(month).upper(), 47 * mm, 11.5, bold=True, min_size=8.5)
    c.drawString(competence_x, top - 7 * mm, _month_label(month).upper())
    if finalized:
        label = "COMPETÊNCIA FINALIZADA"
        label_w = stringWidth(label, "Helvetica-Bold", 5.2) + 5 * mm
        c.setFillColor(OLIVE)
        c.roundRect(competence_x, top - 13.3 * mm, label_w, 5 * mm, 1.2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 5.2)
        c.drawString(competence_x + 2.5 * mm, top - 11.6 * mm, label)

    return top - 21 * mm


def _draw_employee_card(c: canvas.Canvas, y_top: float, report: dict, company_name: str, month: str) -> float:
    width, _ = A4
    left = 9 * mm
    right = width - 9 * mm
    h = 18 * mm
    y = y_top - h
    _round_rect(c, left, y, right - left, h, fill=SOFT)

    employee = report["employee"]
    c.setFillColor(OLIVE)
    c.circle(left + 9 * mm, y + h / 2, 5.5 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.circle(left + 9 * mm, y + h / 2 + 1.5 * mm, 1.7 * mm, fill=1, stroke=0)
    c.ellipse(left + 5.6 * mm, y + 3.3 * mm, left + 12.4 * mm, y + 8.2 * mm, fill=1, stroke=0)

    name_x = left + 19 * mm
    c.setFillColor(INK)
    _fit_text(c, str(employee.get("name") or "Colaborador"), 76 * mm, 10.2, bold=True, min_size=7.1)
    c.drawString(name_x, y + 10.8 * mm, str(employee.get("name") or "Colaborador").upper())
    c.setFont("Helvetica", 6.2)
    c.setFillColor(INK)
    c.drawString(name_x, y + 5.7 * mm, "CPF/ID AFD:")
    c.setFillColor(OLIVE_DARK)
    c.setFont("Helvetica-Bold", 6.2)
    c.drawString(name_x + 19 * mm, y + 5.7 * mm, str(employee.get("external_id") or "-"))

    divider1 = left + 101 * mm
    divider2 = left + 148 * mm
    c.setStrokeColor(colors.HexColor("#D0D5CF"))
    c.line(divider1, y + 3.5 * mm, divider1, y + h - 3.5 * mm)
    c.line(divider2, y + 3.5 * mm, divider2, y + h - 3.5 * mm)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 5.2)
    c.drawString(divider1 + 5 * mm, y + 11.5 * mm, "EMPRESA")
    c.setFillColor(INK)
    _fit_text(c, company_name, 39 * mm, 7.0, min_size=5.4)
    c.drawString(divider1 + 5 * mm, y + 6.2 * mm, company_name)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 5.2)
    c.drawString(divider2 + 5 * mm, y + 11.5 * mm, "COMPETÊNCIA")
    c.setFillColor(INK)
    c.setFont("Helvetica", 7)
    c.drawString(divider2 + 5 * mm, y + 6.2 * mm, _month_label(month))

    return y - 3 * mm


def _draw_summary(c: canvas.Canvas, y_top: float, report: dict, settings: dict) -> float:
    width, _ = A4
    left = 9 * mm
    right = width - 9 * mm
    h = 15 * mm
    y = y_top - h
    _round_rect(c, left, y, right - left, h, fill=WHITE)

    totals = report["totals"]
    normal_percent = settings.get("overtime_weekday_percent", "50")
    bank_enabled = settings.get("bank_hours_enabled") == "1"
    if bank_enabled:
        items = [
            ("SALDO BANCO", _hm(totals.get("bank")), OLIVE_DARK),
            ("HE 100%", _hm(totals.get("overtime_sunday_holiday")), OLIVE_DARK),
            ("DÉBITOS", _hm(totals.get("shortage")), INK),
            ("FALTAS", str(report["absence_count"]), INK),
            ("BATIDAS INCOMPLETAS", str(report["forgotten_count"]), INK),
        ]
    else:
        items = [
            (f"HE {normal_percent}%", _hm((totals.get("overtime_weekday") or 0) + (totals.get("overtime_saturday") or 0)), OLIVE_DARK),
            ("HE 100%", _hm(totals.get("overtime_sunday_holiday")), OLIVE_DARK),
            ("DÉBITOS", _hm(totals.get("shortage")), INK),
            ("FALTAS", str(report["absence_count"]), INK),
            ("BATIDAS INCOMPLETAS", str(report["forgotten_count"]), INK),
        ]

    cell_w = (right - left) / len(items)
    for idx, (label, value, value_color) in enumerate(items):
        x = left + idx * cell_w
        if idx:
            c.setStrokeColor(colors.HexColor("#D0D5CF"))
            c.line(x, y + 3 * mm, x, y + h - 3 * mm)
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 5.3)
        _fit_text(c, label, cell_w - 8 * mm, 5.3, bold=True, min_size=4.7)
        c.drawCentredString(x + cell_w / 2, y + 9.6 * mm, label)
        c.setFillColor(value_color)
        c.setFont("Helvetica-Bold", 11.5)
        c.drawCentredString(x + cell_w / 2, y + 3.7 * mm, value)

    return y - 3 * mm


def _draw_table(c: canvas.Canvas, y_top: float, report: dict, month: str, settings: dict) -> float:
    width, _ = A4
    left = 9 * mm
    right = width - 9 * mm
    table_w = right - left
    normal_percent = settings.get("overtime_weekday_percent", "50")
    bank_enabled = settings.get("bank_hours_enabled") == "1"
    rows = _calendar_rows(month, report["days"])

    if bank_enabled:
        ratios = [0.085, 0.06, 0.305, 0.11, 0.10, 0.10, 0.24]
        headers = ["DATA", "DIA", "MARCAÇÕES", "BANCO", "HE 100%", "DÉBITO", "OCORRÊNCIA / OBSERVAÇÃO"]
    else:
        ratios = [0.085, 0.06, 0.305, 0.10, 0.10, 0.095, 0.255]
        headers = ["DATA", "DIA", "MARCAÇÕES", f"HE {normal_percent}%", "HE 100%", "DÉBITO", "OCORRÊNCIA / OBSERVAÇÃO"]

    xs = [left]
    for ratio in ratios:
        xs.append(xs[-1] + table_w * ratio)

    header_h = 5.6 * mm
    row_h = 4.25 * mm if len(rows) >= 31 else 4.45 * mm
    y = y_top - header_h
    c.setFillColor(INK)
    c.roundRect(left, y, table_w, header_h, 1.8 * mm, fill=1, stroke=0)
    c.rect(left, y, table_w, header_h / 2, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 5.2)
    for idx, header in enumerate(headers):
        _fit_text(c, header, xs[idx + 1] - xs[idx] - 2 * mm, 5.2, bold=True, min_size=4.6)
        c.drawCentredString((xs[idx] + xs[idx + 1]) / 2, y + 1.9 * mm, header)

    for row_index, (row_date, day) in enumerate(rows):
        y -= row_h
        fill = WHITE if row_index % 2 == 0 else colors.HexColor("#FBFCFA")
        c.setFillColor(fill)
        c.rect(left, y, table_w, row_h, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#E0E3DE"))
        c.setLineWidth(0.35)
        c.line(left, y, right, y)
        for x in xs[1:-1]:
            c.line(x, y, x, y + row_h)

        if bank_enabled:
            values = [
                row_date.strftime("%d/%m"),
                WEEKDAY_ABBR[row_date.weekday()],
                _punches(day),
                _hm(day.get("bank") if day else 0),
                _hm(day.get("overtime_sunday_holiday") if day else 0),
                _hm(day.get("shortage") if day else 0),
                _observation(day),
            ]
        else:
            values = [
                row_date.strftime("%d/%m"),
                WEEKDAY_ABBR[row_date.weekday()],
                _punches(day),
                _hm(((day.get("overtime_weekday") or 0) + (day.get("overtime_saturday") or 0)) if day else 0),
                _hm(day.get("overtime_sunday_holiday") if day else 0),
                _hm(day.get("shortage") if day else 0),
                _observation(day),
            ]

        baseline = y + 1.4 * mm
        for idx, value in enumerate(values):
            cell_left, cell_right = xs[idx], xs[idx + 1]
            cell_w = cell_right - cell_left
            value = str(value)
            if idx == 2:
                c.setFillColor(INK)
                _fit_text(c, value, cell_w - 3 * mm, 5.25, bold=True, min_size=4.6)
                c.drawCentredString((cell_left + cell_right) / 2, baseline, value)
            elif idx == len(values) - 1:
                obs = _clip_text(value, 28)
                c.setFillColor(WARN if obs not in ("-", "Feriado") else MUTED)
                _fit_text(c, obs, cell_w - 3 * mm, 5.0, min_size=4.4)
                c.drawCentredString((cell_left + cell_right) / 2, baseline, obs)
            else:
                if idx in (3, 4):
                    c.setFillColor(OLIVE_DARK if value != "00:00" else INK)
                elif idx == 5:
                    c.setFillColor(DEBIT if value != "00:00" else INK)
                else:
                    c.setFillColor(INK)
                c.setFont("Helvetica-Bold" if idx in (3, 4, 5) and value != "00:00" else "Helvetica", 5.25)
                c.drawCentredString((cell_left + cell_right) / 2, baseline, value)

    total_h = 5.2 * mm
    y -= total_h
    c.setFillColor(OLIVE_PALE)
    c.rect(left, y, table_w, total_h, fill=1, stroke=0)
    c.setStrokeColor(LINE)
    c.line(left, y, right, y)
    c.setFillColor(OLIVE_DARK)
    c.setFont("Helvetica-Bold", 5.5)
    c.drawString(left + 3 * mm, y + 1.8 * mm, "TOTAL DO MÊS")

    totals = report["totals"]
    total_values = [
        None,
        None,
        None,
        _hm(totals.get("bank")) if bank_enabled else _hm((totals.get("overtime_weekday") or 0) + (totals.get("overtime_saturday") or 0)),
        _hm(totals.get("overtime_sunday_holiday")),
        _hm(totals.get("shortage")),
        "-",
    ]
    for idx in range(3, len(total_values)):
        val = total_values[idx]
        c.setFillColor(DEBIT if idx == 5 and val != "00:00" else OLIVE_DARK)
        c.setFont("Helvetica-Bold", 5.5)
        c.drawCentredString((xs[idx] + xs[idx + 1]) / 2, y + 1.8 * mm, val)

    c.setStrokeColor(LINE)
    c.setLineWidth(0.55)
    c.rect(left, y, table_w, y_top - y, fill=0, stroke=1)
    return y - 3 * mm


def _draw_footer(c: canvas.Canvas, y_top: float, report: dict, finalized_at: str, page_number: int, page_count: int, finalized: bool) -> None:
    width, _ = A4
    left = 9 * mm
    right = width - 9 * mm

    c.setFont("Helvetica", 4.9)
    c.setFillColor(MUTED)
    c.drawString(left, y_top, "● Hora extra - acrescentada")
    c.setFillColor(DEBIT)
    c.drawString(left + 39 * mm, y_top, "● Débito - descontado")
    c.setFillColor(WARN)
    c.drawString(left + 74 * mm, y_top, "● Ocorrência registrada")

    boxes_top = y_top - 4 * mm
    box_h = 22 * mm
    gap = 5 * mm
    left_w = 77 * mm
    right_w = right - left - left_w - gap
    left_y = boxes_top - box_h
    _round_rect(c, left, left_y, left_w, box_h, fill=SOFT)
    _round_rect(c, left + left_w + gap, left_y, right_w, box_h, fill=WHITE)

    c.setFillColor(OLIVE)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left + 5 * mm, left_y + 8.5 * mm, "✓")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 5.4)
    c.drawString(left + 15 * mm, left_y + 14.2 * mm, "Documento gerado pelo ATLAS · PONTO & JORNADA")
    c.setFont("Helvetica", 5.0)
    c.drawString(left + 15 * mm, left_y + 10.0 * mm, f"Competência encerrada em {finalized_at or '-'}")
    c.drawString(left + 15 * mm, left_y + 5.8 * mm, "Apuração baseada nas marcações AFD consideradas no fechamento.")

    sig_x = left + left_w + gap
    c.setFillColor(OLIVE)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(sig_x + 6 * mm, left_y + 13.2 * mm, "“")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 5.0)
    c.drawString(sig_x + 15 * mm, left_y + 14.3 * mm, "Declaro que tive acesso aos registros de jornada acima")
    c.drawString(sig_x + 15 * mm, left_y + 10.9 * mm, "e estou ciente das marcações referentes à competência indicada.")
    if finalized:
        line_y = left_y + 4.8 * mm
        c.setStrokeColor(colors.HexColor("#858B84"))
        c.line(sig_x + 15 * mm, line_y, sig_x + 62 * mm, line_y)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 4.9)
        c.drawString(sig_x + 15 * mm, line_y - 3.1 * mm, _clip_text(str(report["employee"].get("name") or "COLABORADOR").upper(), 34))
        c.setFont("Helvetica", 4.6)
        c.drawRightString(sig_x + right_w - 6 * mm, line_y - 0.5 * mm, "Data: ____ / ____ / ________")
    else:
        c.setFillColor(WARN)
        c.setFont("Helvetica-Bold", 5.0)
        c.drawString(sig_x + 15 * mm, left_y + 4.8 * mm, "PRÉVIA - competência em aberto, sem campo de assinatura.")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 4.7)
    c.drawCentredString(width / 2, 5 * mm, f"Página {page_number} de {page_count}")


def _draw_employee_page(c: canvas.Canvas, report: dict, month: str, company_name: str, finalized_at: str, page_number: int, page_count: int, settings: dict, finalized: bool = True) -> None:
    y = _draw_header(c, month, finalized)
    y = _draw_employee_card(c, y, report, company_name, month)
    y = _draw_summary(c, y, report, settings)
    y = _draw_table(c, y, report, month, settings)
    _draw_footer(c, y, report, finalized_at, page_number, page_count, finalized)


def build_monthly_pdf(month: str, closure, settings: dict, reports: Iterable[dict]) -> bytes:
    reports = list(reports)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    company_name = str(settings.get("company_name") or "Controle de Ponto")
    finalized_at = str(closure["finalized_at"] if closure is not None else "")

    c.setTitle(f"Espelho mensal de ponto - {_month_label(month)}")
    c.setAuthor(company_name)
    c.setSubject(f"Competência finalizada - Atlas Ponto - layout {PDF_LAYOUT_VERSION}")

    if not reports:
        width, height = A4
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(18 * mm, height - 28 * mm, "Espelho mensal de ponto")
        c.setFont("Helvetica", 9)
        c.drawString(18 * mm, height - 38 * mm, f"Competência {_month_label(month)} sem colaboradores para emissão.")
        c.save()
        return buffer.getvalue()

    page_count = len(reports)
    for index, report in enumerate(reports, start=1):
        _draw_employee_page(c, report, month, company_name, finalized_at, index, page_count, settings, finalized=True)
        c.showPage()

    c.save()
    return buffer.getvalue()


def build_employee_pdf(month: str, closure, settings: dict, report: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    company_name = str(settings.get("company_name") or "Controle de Ponto")
    finalized = closure is not None
    finalized_at = str(closure["finalized_at"] if closure is not None else "")
    c.setTitle(f"Espelho de ponto - {report['employee'].get('name', 'Colaborador')} - {_month_label(month)}")
    c.setAuthor(company_name)
    c.setSubject(f"Espelho individual de ponto - Atlas Ponto - layout {PDF_LAYOUT_VERSION}")
    _draw_employee_page(c, report, month, company_name, finalized_at, 1, 1, settings, finalized=finalized)
    c.showPage()
    c.save()
    return buffer.getvalue()


def _register_employee_pdf_route() -> None:
    from fastapi import HTTPException
    from fastapi.responses import Response
    from app import _load_employee_month, _month_closure, _month_value, _settings_for_month, app, connect

    @app.get("/report/{employee_id}/pdf", include_in_schema=False)
    def atlas_employee_pdf(employee_id: int, month: str | None = None):
        month_value = _month_value(month)
        with connect() as conn:
            employee = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
            if not employee:
                raise HTTPException(404, "Colaborador não encontrado")

            configured = conn.execute(
                "SELECT 1 FROM monthly_schedules WHERE employee_id=? AND month=? LIMIT 1",
                (employee_id, month_value),
            ).fetchone()
            if not configured:
                raise HTTPException(409, "A jornada desta competência ainda não foi configurada")

            closure = _month_closure(conn, month_value)
            settings = _settings_for_month(conn, month_value, closure)
            days = _load_employee_month(conn, employee, month_value)
            totals = {
                "worked": sum(d["worked"] for d in days),
                "overtime_weekday": sum(d["overtime_weekday"] for d in days),
                "overtime_saturday": sum(d["overtime_saturday"] for d in days),
                "overtime_sunday_holiday": sum(d["overtime_sunday_holiday"] for d in days),
                "shortage": sum(d["shortage"] for d in days),
                "bank": sum(d["bank"] for d in days),
            }
            report = {
                "employee": dict(employee),
                "days": days,
                "totals": totals,
                "absence_count": sum(1 for d in days if d.get("absence")),
                "forgotten_count": sum(1 for d in days if d.get("forgotten_punch")),
            }
            pdf_bytes = build_employee_pdf(month_value, closure, settings, report)

        safe_name = "".join(ch if ch.isalnum() else "_" for ch in str(employee["name"]))
        filename = f"espelho_ponto_{month_value}_{safe_name}.pdf"
        disposition = "attachment" if closure else "inline"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )


_register_employee_pdf_route()
