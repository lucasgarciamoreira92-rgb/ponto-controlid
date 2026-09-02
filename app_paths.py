from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parent


def _windows_frozen() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _persistent_root() -> Path:
    if _windows_frozen():
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Atlas Ponto"
        return Path.home() / "AppData" / "Local" / "Atlas Ponto"
    return CODE_DIR


PERSISTENT_ROOT = _persistent_root()
DATA_DIR = PERSISTENT_ROOT / "data"
UPLOAD_DIR = PERSISTENT_ROOT / "uploads"
CLOSED_REPORTS_DIR = DATA_DIR / "closed_reports"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "ponto.db"


def _copy_tree_if_needed(source: Path, target: Path) -> None:
    if not source.exists() or source.resolve() == target.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if destination.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, destination)
        elif item.is_file():
            shutil.copy2(item, destination)


def prepare_persistent_storage() -> None:
    """Cria os diretórios graváveis e migra a instalação Windows antiga uma vez."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CLOSED_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not _windows_frozen():
        return

    # A primeira versão Windows gravava dados ao lado do executável. A partir
    # desta versão, banco e documentos ficam fora da pasta do programa para que
    # atualização/reinstalação do executável não toque na base do RH.
    legacy_root = Path(sys.executable).resolve().parent
    legacy_data = legacy_root / "data"
    legacy_uploads = legacy_root / "uploads"
    legacy_db = legacy_data / "ponto.db"

    if legacy_db.exists() and not DB_PATH.exists():
        shutil.copy2(legacy_db, DB_PATH)

    _copy_tree_if_needed(legacy_data / "closed_reports", CLOSED_REPORTS_DIR)
    _copy_tree_if_needed(legacy_uploads, UPLOAD_DIR)


prepare_persistent_storage()
