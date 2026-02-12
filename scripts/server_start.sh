#!/bin/bash
set -euo pipefail

APP_DIR="/Users/mpmp/Documents/SK Mnisecko app"
PORT="8789"

cd "$APP_DIR"

# Aktivuj venv
source .venv/bin/activate

# Pokud něco běží na portu, ukonči to
PID=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t || true)
if [ -n "${PID}" ]; then
  echo "Stopping existing server (PID ${PID})..."
  kill ${PID} || true
  sleep 0.5
fi

# Spusť server s autoreload
echo "Starting server on http://127.0.0.1:${PORT}/"
python manage.py runserver 127.0.0.1:${PORT}
