from __future__ import annotations

# app_auto carrega a aplicação principal, relatórios e inferência de jornadas.
from app_auto import app  # noqa: F401

# Registra as rotas e o sincronizador do REP Control iD.
import controlid_routes  # noqa: F401,E402


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
