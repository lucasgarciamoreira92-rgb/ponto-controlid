from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from fastapi import File, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app import app
from app_paths import BACKUP_DIR
from backup_restore import BackupError, create_backup_archive, restore_backup_archive


MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


@app.get("/backup/download", include_in_schema=False)
def atlas_backup_download():
    path = create_backup_archive()
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/backup/restore", include_in_schema=False)
async def atlas_backup_restore(file: UploadFile = File(...)):
    filename = str(file.filename or "").strip()
    if not filename.lower().endswith(".zip"):
        return RedirectResponse(
            "/settings?backup_error=" + quote_plus("Selecione um arquivo .zip de backup do Atlas Ponto."),
            status_code=303,
        )

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = BACKUP_DIR / "restauracao_recebida.tmp.zip"
    received = 0

    try:
        with upload_path.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    raise BackupError("O arquivo de backup excede o limite permitido.")
                target.write(chunk)

        if received == 0:
            raise BackupError("O arquivo de backup está vazio.")

        result = restore_backup_archive(upload_path)
        safety = Path(result["safety_backup"]).name
        return RedirectResponse(
            "/settings?restored=1&safety=" + quote_plus(safety),
            status_code=303,
        )
    except BackupError as exc:
        return RedirectResponse(
            "/settings?backup_error=" + quote_plus(str(exc)),
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            "/settings?backup_error="
            + quote_plus("A restauração falhou antes de alterar a base. O backup atual foi preservado."),
            status_code=303,
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if upload_path.exists():
            try:
                upload_path.unlink()
            except OSError:
                pass
