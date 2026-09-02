from __future__ import annotations

from datetime import date

import app as app_module
import app_auto as app_auto_module


_original_load_employee_month = app_module._load_employee_month


def _load_employee_month_without_future(conn, employee, month: str):
    days = _original_load_employee_month(conn, employee, month)

    # Competências já fechadas continuam exatamente como foram congeladas.
    if app_module._month_closure(conn, month):
        return days

    today = date.today()
    return [day for day in days if day.get("date") is None or day["date"] <= today]


# As rotas de app.py consultam a função pelo namespace do módulo em tempo de
# execução. app_auto também mantém uma referência local para os PDFs fechados.
# Atualizamos as duas para que todo o sistema use a mesma regra.
app_module._load_employee_month = _load_employee_month_without_future
app_auto_module._load_employee_month = _load_employee_month_without_future
