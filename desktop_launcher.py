from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _setup_logging() -> None:
    log_dir = _runtime_root() / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "atlas_ponto.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _server_ready(timeout: float = 0.8) -> bool:
    try:
        with urlrequest.urlopen(BASE_URL + "/health", timeout=timeout) as response:
            return response.status == 200
    except (urlerror.URLError, TimeoutError, OSError):
        return False


def _open_when_ready() -> None:
    for _ in range(60):
        if _server_ready():
            webbrowser.open(BASE_URL)
            return
        time.sleep(0.25)


def main() -> None:
    _setup_logging()

    # Se o Atlas já estiver rodando, um segundo clique apenas abre a interface.
    if _server_ready():
        webbrowser.open(BASE_URL)
        return

    threading.Thread(target=_open_when_ready, daemon=True).start()

    try:
        import uvicorn
        from app_main import app

        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            reload=False,
            log_level="warning",
            access_log=False,
        )
    except Exception:
        logging.exception("Falha ao iniciar Atlas Ponto")
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                "O Atlas Ponto não conseguiu iniciar. Consulte o arquivo data\\atlas_ponto.log.",
                "Atlas Ponto",
                0x10,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
