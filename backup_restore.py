from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, Optional

from app_paths import BACKUP_DIR, CLOSED_REPORTS_DIR, DATA_DIR, DB_PATH, UPLOAD_DIR
from db import DB_LOCK, init_db


BACKUP_FORMAT_VERSION = 1
MAX_ARCHIVE_FILES = 10000
MAX_ARCHIVE_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
REQUIRED_TABLES = {
    "employees",
    "schedules",
    "monthly_schedules",
    "month_closures",
    "settings",
    "imports",
    "punches",
}


class BackupError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup_database(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with DB_LOCK:
        if not DB_PATH.exists():
            init_db()
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()


def _add_tree(zf: zipfile.ZipFile, source: Path, archive_prefix: str) -> None:
    if not source.exists():
        return
    for item in sorted(source.rglob("*")):
        if not item.is_file() or item.is_symlink():
            continue
        relative = item.relative_to(source).as_posix()
        zf.write(item, f"{archive_prefix}/{relative}")


def create_backup_archive(prefix: str = "AtlasPonto_Backup") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    final_path = BACKUP_DIR / f"{prefix}_{_timestamp()}.zip"
    temp_output = final_path.with_suffix(".zip.tmp")

    with tempfile.TemporaryDirectory(prefix="atlas_backup_") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        snapshot = temp_dir / "ponto.db"
        _backup_database(snapshot)

        manifest = {
            "format": "atlas-ponto-backup",
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "database": "data/ponto.db",
            "includes": ["database", "closed_reports", "uploads"],
        }

        try:
            with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                zf.write(snapshot, "data/ponto.db")
                _add_tree(zf, CLOSED_REPORTS_DIR, "data/closed_reports")
                _add_tree(zf, UPLOAD_DIR, "uploads")
            temp_output.replace(final_path)
        finally:
            if temp_output.exists():
                try:
                    temp_output.unlink()
                except OSError:
                    pass

    return final_path


def _validate_member_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise BackupError("O arquivo de backup contém um caminho inválido.")
    if normalized.startswith("/") or ":" in path.parts[0] if path.parts else False:
        raise BackupError("O arquivo de backup contém um caminho inválido.")


def _validate_sqlite(path: Path) -> Dict[str, int]:
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise BackupError("O banco do backup falhou na verificação de integridade.")

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise BackupError(
                "O backup não pertence a uma base Atlas Ponto válida. "
                f"Tabelas ausentes: {', '.join(missing)}."
            )

        counts = {}
        for table in ("employees", "punches", "imports", "month_closures"):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return counts
    except sqlite3.DatabaseError as exc:
        raise BackupError("O arquivo ponto.db do backup não é um banco SQLite válido.") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


def inspect_backup_archive(path: Path) -> Dict[str, object]:
    if not path.exists() or path.stat().st_size <= 0:
        raise BackupError("O arquivo de backup está vazio.")
    if not zipfile.is_zipfile(path):
        raise BackupError("Selecione um backup .zip gerado pelo Atlas Ponto.")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise BackupError("O backup contém arquivos demais e foi bloqueado por segurança.")

            total_size = 0
            names = set()
            for info in infos:
                _validate_member_name(info.filename)
                total_size += int(info.file_size or 0)
                if total_size > MAX_ARCHIVE_UNCOMPRESSED:
                    raise BackupError("O backup é grande demais para restauração segura.")
                names.add(info.filename.replace("\\", "/"))

            if "manifest.json" not in names or "data/ponto.db" not in names:
                raise BackupError("O arquivo não possui a estrutura de backup do Atlas Ponto.")

            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                raise BackupError("O manifesto do backup é inválido.") from exc

            if manifest.get("format") != "atlas-ponto-backup":
                raise BackupError("Este arquivo não foi reconhecido como backup do Atlas Ponto.")
            version = int(manifest.get("format_version") or 0)
            if version < 1 or version > BACKUP_FORMAT_VERSION:
                raise BackupError("A versão deste backup não é compatível com esta instalação.")

            with tempfile.TemporaryDirectory(prefix="atlas_validate_") as temp_dir_raw:
                db_temp = Path(temp_dir_raw) / "ponto.db"
                with zf.open("data/ponto.db") as src, db_temp.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                counts = _validate_sqlite(db_temp)

            return {
                "manifest": manifest,
                "counts": counts,
                "archive_size": path.stat().st_size,
                "uncompressed_size": total_size,
                "file_count": len(infos),
            }
    except zipfile.BadZipFile as exc:
        raise BackupError("O arquivo ZIP está corrompido.") from exc


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    for info in zf.infolist():
        _validate_member_name(info.filename)
        target = destination / PurePosixPath(info.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _replace_tree(source: Optional[Path], target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if source and source.exists():
        for item in source.iterdir():
            destination = target / item.name
            if item.is_dir():
                shutil.copytree(item, destination)
            elif item.is_file():
                shutil.copy2(item, destination)


def restore_backup_archive(path: Path) -> Dict[str, object]:
    info = inspect_backup_archive(path)
    safety_backup = create_backup_archive(prefix="AtlasPonto_Antes_Restauracao")

    with tempfile.TemporaryDirectory(prefix="atlas_restore_") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        with zipfile.ZipFile(path, "r") as zf:
            _safe_extract(zf, temp_dir)

        restored_db = temp_dir / "data" / "ponto.db"
        restored_reports = temp_dir / "data" / "closed_reports"
        restored_uploads = temp_dir / "uploads"
        _validate_sqlite(restored_db)

        rollback_db = DATA_DIR / f"ponto.db.rollback_{_timestamp()}"
        with DB_LOCK:
            try:
                if DB_PATH.exists():
                    shutil.copy2(DB_PATH, rollback_db)

                staged_db = DATA_DIR / "ponto.db.restore_tmp"
                shutil.copy2(restored_db, staged_db)
                os.replace(staged_db, DB_PATH)
                _replace_tree(restored_reports if restored_reports.exists() else None, CLOSED_REPORTS_DIR)
                _replace_tree(restored_uploads if restored_uploads.exists() else None, UPLOAD_DIR)
                init_db()
            except Exception as exc:
                if rollback_db.exists():
                    try:
                        os.replace(rollback_db, DB_PATH)
                    except OSError:
                        pass
                raise BackupError(
                    "A restauração não pôde ser concluída. A base anterior foi preservada."
                ) from exc
            finally:
                if rollback_db.exists():
                    try:
                        rollback_db.unlink()
                    except OSError:
                        pass

    return {
        "restored": True,
        "safety_backup": safety_backup,
        "inspection": info,
    }
