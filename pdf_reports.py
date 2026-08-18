from __future__ import annotations

from io import BytesIO
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


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


def _month_label(month: str) -> str:
    year, mon = month.split("-", 1)
    return f"{MONTH_NAMES.get(mon, mon)}/{year}"


def _hm(minutes: int | float | None) -> str:
    if minutes is None:
        return "-"
    value = int(round(minutes))
    sign = "-" if value < 0 else ""
    value = abs(value)
    hours, mins = divmod(value, 60)
    return f"{sign}{hours:02d}:{mins:02d}"


def _fit_text(c: canvas.Canvas, text: str, max_width: float, size: float, bold: bool = False, min_size: float = 6.5) -> float:
    font = "Helvetica-Bold" if bold else "Helvetica"
    current = size
    while current > min_size and stringWidth(text, font, current) > max_width:
        current -= 0.25
    c.setFont(font, current)
    return current


def _clip_text(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _observation(day: dict) -> str:
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


def _punches(day: dict) -> str:
    values = []
    for punch in day.get("punches") or []:
        try:
            values.append(punch.strftime("%H:%M"))
        except AttributeError:
            values.append(str(punch))
    return " | ".join(values) if values else "-"


def _draw_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, label: str, value: str) -> None:
    c.setStrokeColor(colors.HexColor("#D5D8D3"))
    c.setFillColor(colors.white)
    c.roundRect(x, y, w, h, 2.2 * mm, stroke=1, fill=1)
    c.setFillColor(colors.HexColor("#5D625D"))
    c.setFont("Helvetica-Bold", 6.3)
    c.drawString(x + 3.2 * mm, y + h - 5.2 * mm, label.upper())
    c.setFillColor(colors.HexColor("#161816"))
    _fit_text(c, value, w - 6.4 * mm, 9.2, bold=True, min_size=7.0)
    c.drawString(x + 3.2 * mm, y + 3.0 * mm, value)


def _draw_employee_page(
    c: canvas.Canvas,
    report: dict,
    month: str,
    company_name: str,
    finalized_at: str,
    page_number: int,
    page_count: int,
    settings: dict,
) -> None:
    width, height = landscape(A4)
    left = 10 * mm
    right = width - 10 * mm
    top = height - 8 * mm

    employee = report["employee"]
    days = report["days"]
    totals = report["totals"]
    absence_count = report["absence_count"]
    forgotten_count = report["forgotten_count"]
    bank_enabled = settings.get("bank_hours_enabled") == "1"
    normal_percent = settings.get("overtime_weekday_percent", "50")

    c.setFillColor(colors.HexColor("#4C603A"))
    c.rect(0, height - 8 * mm, width, 8 * mm, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#6A716A"))
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(left, top - 5 * mm, str(company_name or "Controle de Ponto").upper())

    c.setFillColor(colors.HexColor("#151715"))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(left, top - 12 * mm, "Espelho mensal de ponto")
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#5D625D"))
    c.drawString(left, top - 17 * mm, "Registro individual de jornada - competência finalizada")

    page_text = f"Página {page_number}/{page_count}"
    c.setFont("Helvetica", 7)
    c.drawRightString(right, top - 5 * mm, page_text)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor("#4C603A"))
    c.drawRightString(right, top - 12 * mm, _month_label(month))
    c.setFont("Helvetica", 6.5)
    c.setFillColor(colors.HexColor("#6A716A"))
    c.drawRightString(right, top - 17 * mm, f"Fechada em {finalized_at}")

    meta_y = top - 31 * mm
    gap = 4 * mm
    meta_w = (right - left - gap) / 2
    _draw_box(c, left, meta_y, meta_w, 16 * mm, "Colaborador", str(employee["name"]))
    _draw_box(c, left + meta_w + gap, meta_y, meta_w, 16 * mm, "Competência", _month_label(month))

    summary_y = meta_y - 18 * mm
    summary_gap = 2.2 * mm
    summary_count = 5 if bank_enabled else 6
    summary_w = (right - left - summary_gap * (summary_count - 1)) / summary_count

    if bank_enabled:
        summary = [
            ("Trabalhado", _hm(totals["worked"])),
            ("Banco", _hm(totals["bank"])),
            ("HE 100%", _hm(totals["overtime_sunday_holiday"])),
            ("Faltas", f"{absence_count} dia(s)"),
            ("Batidas incompletas", f"{forgotten_count} dia(s)"),
        ]
    else:
        summary = [
            ("Trabalhado", _hm(totals["worked"])),
            (f"HE {normal_percent}%", _hm(totals["overtime_weekday"] + totals["overtime_saturday"])),
            ("HE 100%", _hm(totals["overtime_sunday_holiday"])),
            ("Débitos", _hm(totals["shortage"])),
            ("Faltas", f"{absence_count} dia(s)"),
            ("Batidas incompletas", f"{forgotten_count} dia(s)"),
        ]

    for index, (label, value) in enumerate(summary):
        _draw_box(c, left + index * (summary_w + summary_gap), summary_y, summary_w, 14 * mm, label, value)

    table_top = summary_y - 5 * mm
    table_bottom = 31 * mm
    available_height = table_top - table_bottom
    row_count = max(1, len(days))
    header_h = 6.2 * mm
    row_h = min(4.15 * mm, max(3.15 * mm, (available_height - header_h) / row_count))

    table_width = right - left
    if bank_enabled:
        widths = [0.09, 0.06, 0.31, 0.10, 0.10, 0.10, 0.24]
        headers = ["Data", "Dia", "Marcações", "Trab.", "Banco", "HE 100%", "Observação"]
    else:
        widths = [0.085, 0.055, 0.285, 0.09, 0.085, 0.085, 0.08, 0.235]
        headers = ["Data", "Dia", "Marcações", "Trab.", f"HE {normal_percent}%", "HE 100%", "Débito", "Observação"]

    xs = [left]
    for ratio in widths:
        xs.append(xs[-1] + table_width * ratio)

    header_y = table_top - header_h
    c.setFillColor(colors.HexColor("#ECEEEA"))
    c.rect(left, header_y, table_width, header_h, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#C9CEC7"))
    c.rect(left, header_y, table_width, header_h, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 6.2)
    c.setFillColor(colors.HexColor("#3D423D"))
    for idx, header in enumerate(headers):
        c.drawCentredString((xs[idx] + xs[idx + 1]) / 2, header_y + 2.1 * mm, header)

    y = header_y
    c.setFont("Helvetica", 6.3)
    for day in days:
        y -= row_h
        c.setFillColor(colors.white)
        if day.get("review_required"):
            c.setFillColor(colors.HexColor("#FFF7F1"))
        c.rect(left, y, table_width, row_h, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#DADDDA"))
        c.line(left, y, right, y)
        for x in xs[1:-1]:
            c.line(x, y, x, y + row_h)

        values = [
            day["date"].strftime("%d/%m/%Y"),
            str(day.get("weekday") or ""),
            _punches(day),
            _hm(day.get("worked")),
        ]
        if bank_enabled:
            values.extend([
                _hm(day.get("bank")),
                _hm(day.get("overtime_sunday_holiday")),
                _observation(day),
            ])
        else:
            values.extend([
                _hm((day.get("overtime_weekday") or 0) + (day.get("overtime_saturday") or 0)),
                _hm(day.get("overtime_sunday_holiday")),
                _hm(day.get("shortage")),
                _observation(day),
            ])

        c.setFillColor(colors.HexColor("#222522"))
        baseline = y + max(1.2 * mm, (row_h - 2.0 * mm) / 2)
        for idx, value in enumerate(values):
            cell_w = xs[idx + 1] - xs[idx]
            if idx in (2, len(values) - 1):
                c.setFont("Helvetica", 6.1)
                c.drawString(xs[idx] + 1.5 * mm, baseline, _clip_text(value, 38 if idx == 2 else 30))
            else:
                c.setFont("Helvetica", 6.2)
                c.drawCentredString((xs[idx] + xs[idx + 1]) / 2, baseline, str(value))

    c.setStrokeColor(colors.HexColor("#C9CEC7"))
    c.rect(left, y, table_width, table_top - y, fill=0, stroke=1)

    signature_y = 17 * mm
    c.setFillColor(colors.HexColor("#5D625D"))
    c.setFont("Helvetica", 6.4)
    c.drawString(left, signature_y + 8 * mm, "Declaro que tive acesso e conferi os registros de jornada acima referentes à competência indicada.")

    line_w = 92 * mm
    c.setStrokeColor(colors.HexColor("#5D625D"))
    c.line(left, signature_y, left + line_w, signature_y)
    c.line(right - 55 * mm, signature_y, right, signature_y)
    c.setFont("Helvetica", 6.2)
    c.setFillColor(colors.HexColor("#5D625D"))
    c.drawString(left, signature_y - 3.5 * mm, "Assinatura do colaborador")
    c.drawString(right - 55 * mm, signature_y - 3.5 * mm, "Data: ____/____/________")

    c.setFont("Helvetica", 5.7)
    c.setFillColor(colors.HexColor("#7A807A"))
    c.drawRightString(right, 5.5 * mm, "Marcações originais preservadas conforme registros considerados no fechamento.")


def build_monthly_pdf(
    month: str,
    closure,
    settings: dict,
    reports: Iterable[dict],
) -> bytes:
    reports = list(reports)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4), pageCompression=1)
    company_name = str(settings.get("company_name") or "Controle de Ponto")
    finalized_at = str(closure["finalized_at"] if closure is not None else "")

    c.setTitle(f"Espelho mensal de ponto - {_month_label(month)}")
    c.setAuthor(company_name)
    c.setSubject("Competência finalizada - espelhos individuais de ponto")

    if not reports:
        width, height = landscape(A4)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, height - 25 * mm, "Espelho mensal de ponto")
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, height - 35 * mm, f"Competência {_month_label(month)} sem colaboradores para emissão.")
        c.save()
        return buffer.getvalue()

    page_count = len(reports)
    for index, report in enumerate(reports, start=1):
        _draw_employee_page(
            c,
            report,
            month,
            company_name,
            finalized_at,
            index,
            page_count,
            settings,
        )
        c.showPage()

    c.save()
    return buffer.getvalue()
