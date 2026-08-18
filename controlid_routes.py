from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import app, templates
from controlid_sync import (
    ControlIDError,
    device_status_summary,
    get_device,
    list_devices,
    recent_syncs,
    save_device,
    sync_device,
    test_device_connection,
)


def _redirect_devices(**params) -> RedirectResponse:
    query = "&".join(
        f"{quote_plus(str(key))}={quote_plus(str(value))}"
        for key, value in params.items()
        if value is not None
    )
    return RedirectResponse("/devices" + ("?" + query if query else ""), status_code=303)


@app.get("/devices", response_class=HTMLResponse)
def controlid_devices_page(request: Request, edit: Optional[int] = None):
    devices = list_devices()
    edit_device = get_device(edit) if edit else None
    syncs = recent_syncs(20)
    return templates.TemplateResponse(
        "devices.html",
        {
            "request": request,
            "devices": devices,
            "edit_device": edit_device,
            "syncs": syncs,
        },
    )


@app.post("/devices/save")
async def controlid_device_save(request: Request):
    form = await request.form()
    raw_id = str(form.get("device_id", "")).strip()
    device_id = int(raw_id) if raw_id.isdigit() else None
    try:
        saved_id = save_device(
            device_id=device_id,
            name=str(form.get("name", "REP Control iD")),
            host=str(form.get("host", "")),
            port=int(str(form.get("port", "443") or "443")),
            login=str(form.get("login", "admin")),
            password=str(form.get("password", "")),
            verify_tls=bool(form.get("verify_tls")),
            enabled=bool(form.get("enabled")),
            auto_sync_minutes=int(str(form.get("auto_sync_minutes", "0") or "0")),
        )
    except (ValueError, ControlIDError) as exc:
        return _redirect_devices(error=str(exc), edit=device_id)
    return _redirect_devices(saved=1, edit=saved_id)


@app.post("/devices/{device_id}/test")
def controlid_device_test(device_id: int):
    try:
        result = test_device_connection(device_id)
        clock = result.get("clock") or {}
        clock_text = ""
        if clock:
            try:
                clock_text = (
                    f" {int(clock.get('day', 0)):02d}/{int(clock.get('month', 0)):02d}/"
                    f"{int(clock.get('year', 0)):04d} "
                    f"{int(clock.get('hour', 0)):02d}:{int(clock.get('minute', 0)):02d}"
                )
            except (TypeError, ValueError):
                clock_text = ""
        return _redirect_devices(test_ok=f"Conexão realizada com sucesso.{clock_text}")
    except ControlIDError as exc:
        return _redirect_devices(error=str(exc), edit=device_id)


@app.post("/devices/{device_id}/sync")
def controlid_device_sync(device_id: int):
    try:
        result = sync_device(device_id)
        return _redirect_devices(
            sync_ok=1,
            new=result.get("inserted", 0),
            nsr=result.get("last_nsr", 0),
        )
    except ControlIDError as exc:
        return _redirect_devices(error=str(exc), edit=device_id)


@app.post("/devices/sync-all")
async def controlid_sync_all(request: Request):
    form = await request.form()
    month = str(form.get("month", "")).strip()
    devices = list_devices(enabled_only=True)
    if not devices:
        target = "/devices?error=" + quote_plus("Nenhum relógio ativo está configurado.")
        return RedirectResponse(target, status_code=303)

    inserted = 0
    errors = []
    for device in devices:
        try:
            result = sync_device(int(device["id"]))
            inserted += int(result.get("inserted") or 0)
        except ControlIDError as exc:
            errors.append(f"{device['name']}: {exc}")

    if errors:
        error_text = " | ".join(errors)
        if month:
            return RedirectResponse(
                f"/?month={quote_plus(month)}&rep_sync_error={quote_plus(error_text)}",
                status_code=303,
            )
        return _redirect_devices(error=error_text)

    if month:
        return RedirectResponse(
            f"/?month={quote_plus(month)}&rep_sync_ok={inserted}",
            status_code=303,
        )
    return _redirect_devices(sync_ok=1, new=inserted)


@app.get("/devices/status")
def controlid_status():
    return JSONResponse(device_status_summary())


def _parse_local_iso(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None


def _auto_sync_loop() -> None:
    # Executa apenas enquanto o aplicativo local estiver aberto.
    # O loop é deliberadamente simples e conservador: consulta a cada 60 s e
    # deixa o lock por equipamento impedir corrida com a sincronização manual.
    time.sleep(8)
    while True:
        try:
            now = datetime.now()
            for device in list_devices(enabled_only=True):
                interval = int(device["auto_sync_minutes"] or 0)
                if interval <= 0:
                    continue
                last_attempt = _parse_local_iso(device["last_sync_at"])
                due = last_attempt is None or (now - last_attempt).total_seconds() >= interval * 60
                if not due:
                    continue
                try:
                    sync_device(int(device["id"]))
                except ControlIDError:
                    # O erro fica registrado no próprio equipamento e no histórico.
                    pass
        except Exception:
            # Um problema no sincronizador nunca deve derrubar o servidor de ponto.
            pass
        time.sleep(60)


_auto_thread_started = False
_auto_thread_guard = threading.Lock()


@app.on_event("startup")
def start_controlid_auto_sync() -> None:
    global _auto_thread_started
    with _auto_thread_guard:
        if _auto_thread_started:
            return
        thread = threading.Thread(
            target=_auto_sync_loop,
            name="atlas-controlid-sync",
            daemon=True,
        )
        thread.start()
        _auto_thread_started = True
