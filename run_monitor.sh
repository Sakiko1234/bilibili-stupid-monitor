#!/bin/bash
set -Eeuo pipefail

REPO=/root/bilibili-stupid-monitor
cd "$REPO"
LOCK_PATH=/run/lock/bilibili-stupid-monitor.lock
exec 9>"$LOCK_PATH"
if ! /usr/bin/flock -n 9; then
    printf '[%s] monitor skipped: another job holds lock\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 0
fi

set -a
source .env
set +a
export PYTHONUNBUFFERED=1
/usr/bin/python3.11 -u monitor.py >> "$REPO/cron.log" 2>&1
