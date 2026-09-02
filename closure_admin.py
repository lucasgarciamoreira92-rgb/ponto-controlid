from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app import _month_closure, app, connect
from app_paths import CLOSED_REPORTS_DIR, CODE_DIR
from backup_restore import BackupError, create_backup_archive


def _validate_month(month: str) -> str:
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(404, "Competência inválida") from exc
    return month


def _remove_archived_pdfs(month: str) -> None:
    # Remove o documento gerado para o fechamento cancelado. Também verifica o
    # diretório legado das primeiras versões Windows para não deixar PDF antigo
    # acessível depois que a competência voltar a ficar aberta.
    roots = {
        CLOSED_REPORTS_DIR,
        CODE_DIR / "data" / "closed_reports",
    }
    for root in roots:
        try:
            if not root.exists():
                continue
            for path in root.glob(f"espelho_ponto_{month}*.pdf"):
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass


@app.post("/closures/{month}/cancel")
async def cancel_closed_competence(month: str, request: Request):
    month = _validate_month(month)
    form = await request.form()
    confirmation = str(form.get("confirm_month", "")).strip()

    if confirmation != month:
        return RedirectResponse(
            f"/month-schedules?month={month}&cancel_error=confirmation",
            status_code=303,
        )

    with connect() as conn:
        closure = _month_closure(conn, month)
        if not closure:
            return RedirectResponse(
                f"/month-schedules?month={month}&cancel_error=not_closed",
                status_code=303,
            )

    # Antes de desfazer o fechamento, cria uma cópia completa da situação atual.
    # Se algo inesperado acontecer, o usuário ainda possui o estado anterior.
    try:
        safety_backup = create_backup_archive(
            prefix=f"AtlasPonto_Antes_Cancelar_{month.replace('-', '_')}"
        )
    except BackupError:
        return RedirectResponse(
            f"/month-schedules?month={month}&cancel_error=backup",
            status_code=303,
        )

    with connect() as conn:
        closure = _month_closure(conn, month)
        if not closure:
            return RedirectResponse(
                f"/month-schedules?month={month}&cancel_error=not_closed",
                status_code=303,
            )
        conn.execute("DELETE FROM month_closures WHERE month=?", (month,))

    _remove_archived_pdfs(month)

    return RedirectResponse(
        (
            f"/month-schedules?month={month}&closure_cancelled=1"
            f"&safety_backup={safety_backup.name}"
        ),
        status_code=303,
    )
