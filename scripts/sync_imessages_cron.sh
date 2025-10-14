#!/usr/bin/env bash
# Cron script for syncing iMessages incrementally
# This script should be run periodically (e.g., every 5 minutes via cron)

set -euo pipefail

# Change to the repository directory
REPO_DIR="/Users/zhiyufu/Dropbox/Juju/modules/memory-database"
cd "${REPO_DIR}"

# Setup logging
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/imessage_sync_$(date +%Y%m%d).log"

echo "[$(date +"%Y-%m-%d %H:%M:%S")] Starting iMessage sync..." >>"${LOG_FILE}"

# Run the sync command
UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju" /opt/homebrew/bin/uv run python -m memory_database.cli sync-imessages >>"${LOG_FILE}" 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Sync completed successfully" >>"${LOG_FILE}"
else
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Sync failed with error code $?" >>"${LOG_FILE}"
fi
