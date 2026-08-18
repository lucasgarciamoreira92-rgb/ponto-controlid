#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Erro: python3 não foi encontrado. Instale Python 3.9 ou superior."
  exit 1
fi

echo "Usando: $($PYTHON_BIN --version)"

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo ""
echo "Aplicativo iniciado em: http://127.0.0.1:8000"
python app_main.py
