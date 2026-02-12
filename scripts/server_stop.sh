#!/bin/bash
set -euo pipefail

PORT="8789"
PID=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t || true)
if [ -z "${PID}" ]; then
  echo "No server is running on port ${PORT}."
  exit 0
fi

echo "Stopping server PID ${PID} on port ${PORT}..."
kill ${PID} || true
