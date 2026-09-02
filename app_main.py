from __future__ import annotations

from app_paths import CLOSED_REPORTS_DIR, UPLOAD_DIR, prepare_persistent_storage


# No Windows empacotado, migra a base antiga antes que qualquer módulo abra o
# SQLite. Em execução pelo código-fonte (Mac/Linux), os caminhos continuam no
# próprio diretório do projeto.
prepare_persistent_storage()

# app_auto carrega a aplicação principal, relatórios e inferência de jornadas.
from app_auto import app  # noqa: F401,E402

# Alguns módulos históricos mantêm os diretórios como variáveis globais. O
# launcher centraliza esses destinos no armazenamento persistente sem alterar
# o comportamento do desenvolvimento local.
import app as app_module  # noqa: E402
import app_auto as app_auto_module  # noqa: E402
import controlid_sync as controlid_sync_module  # noqa: E402

app_module.UPLOAD_DIR = UPLOAD_DIR
app_auto_module.CLOSED_REPORTS_DIR = CLOSED_REPORTS_DIR
controlid_sync_module.UPLOAD_DIR = UPLOAD_DIR

# Competências abertas nunca classificam datas futuras como falta.
import month_runtime_guard  # noqa: F401,E402

# Registra a prévia consolidada de todos os colaboradores antes do fechamento.
import monthly_preview  # noqa: F401,E402

# Registra as rotas e o sincronizador do REP Control iD.
import controlid_routes  # noqa: F401,E402

# Registra backup e restauração completos da base do Atlas Ponto.
import backup_routes  # noqa: F401,E402

# Permite desfazer um fechamento feito por engano, com backup preventivo.
import closure_admin  # noqa: F401,E402


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
