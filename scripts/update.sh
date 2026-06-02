#!/bin/bash
set -euo pipefail

APP_DIR=/home/ross/Apps/organiser

cd "$APP_DIR"

git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

git pull origin main --quiet
/home/ross/.local/bin/uv sync --no-dev --quiet
sudo systemctl restart organiser
echo "$(date): deployed $(git rev-parse --short HEAD)"
