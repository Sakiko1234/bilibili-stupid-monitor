#!/bin/bash
set -Eeuo pipefail

REPO=/root/bilibili-stupid-monitor
cd "$REPO"
GLOBAL_LOCK=/run/lock/bilibili-stupid-monitor.lock
exec 9>"$GLOBAL_LOCK"
if ! /usr/bin/flock -n 9; then
    printf '[%s] pipeline skipped: another job holds lock\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"
    exit 0
fi

if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    printf '[%s] ERROR: stale git rebase state; refusing to publish\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"
    exit 1
fi

PIPELINE_LOCKFILE="$REPO/data/.pipeline.lock"
if [ -f "$PIPELINE_LOCKFILE" ]; then
    PID=$(cat "$PIPELINE_LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        printf '[%s] Pipeline already running (PID=%s), skip\n' "$(date '+%m-%d %H:%M:%S')" "$PID" >> "$REPO/cron_pipeline.log"
        exit 0
    fi
fi
printf '%s\n' "$$" > "$PIPELINE_LOCKFILE"
trap 'rm -f "$PIPELINE_LOCKFILE"' EXIT

set -a
source .env
set +a

printf '[%s] Pipeline start\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"
/usr/bin/python3.11 pipeline.py >> "$REPO/cron_pipeline.log" 2>&1
printf '[%s] Pipeline done, generating HTML...\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"

/usr/bin/python3.11 -c "
import json
from monitor import build_html, build_users_html, check_report_results, build_report_html

with open('data/flagged.json', encoding='utf-8') as f:
    data = json.load(f)
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(build_html(data))
with open('users.html', 'w', encoding='utf-8') as f:
    f.write(build_users_html(data))
tracking = check_report_results()
with open('report_status.html', 'w', encoding='utf-8') as f:
    f.write(build_report_html(tracking))
print('HTML regenerated')
" >> "$REPO/cron_pipeline.log" 2>&1
printf '[%s] HTML done\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"

# 推送
git add index.html users.html report_status.html data/flagged.json data/.checked.json data/pipeline/ data/.pipeline_state.json

if git diff --cached --quiet; then
    printf '[%s] Pipeline 无变化\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"
    exit 0
fi

git commit -m "Pipeline自动更新 [$(date '+%m-%d %H:%M')]"
git pull --rebase --autostash origin master
git push origin master
printf '[%s] Pipeline 已推送到 GitHub\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron_pipeline.log"
