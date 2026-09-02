from __future__ import annotations

import logging
import threading
import time
import webbrowser
from urllib import error as urlerror
from urllib import request as urlrequest

from app_paths import DATA_DIR, prepare_persistent_storage


HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"


def _setup_logging() -> None:
    prepare_persistent_storage()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "atlas_ponto.log",
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

        # Em um executável Windows sem console, sys.stdout/sys.stderr podem ser
        # None. O log_config padrão do Uvicorn tenta consultar isatty() nesses
        # streams e derruba a aplicação antes de o servidor iniciar. O Atlas já
        # possui logging próprio em arquivo, então desativamos apenas a
        # configuração de console do Uvicorn.
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            reload=False,
            log_level="warning",
            log_config=None,
            access_log=False,
        )
    except Exception:
        logging.exception("Falha ao iniciar Atlas Ponto")
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"O Atlas Ponto não conseguiu iniciar. Consulte o log em:\n{DATA_DIR / 'atlas_ponto.log'}",
                "Atlas Ponto",
                0x10,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
