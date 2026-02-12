#!/bin/bash
set -euo pipefail

PORT="8789"
PID=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t || true)
if [ -z "${PID}" ]; then
  echo "Server is not running on port ${PORT}."
  exit 0
fi

echo "Server running on port ${PORT} (PID ${PID})"
ps -p ${PID} -o command=
