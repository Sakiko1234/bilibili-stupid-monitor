#!/bin/bash
set -Eeuo pipefail

REPO=/root/bilibili-stupid-monitor
cd "$REPO"
LOCK_PATH=/run/lock/bilibili-stupid-monitor.lock
exec 9>"$LOCK_PATH"
if ! /usr/bin/flock -n 9; then
    printf '[%s] run skipped: another job holds lock\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 0
fi

if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    printf '[%s] ERROR: stale git rebase state; refusing to publish\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 1
fi

# 加载环境变量
set -a
source .env
set +a

# 合并 pipeline 数据
/usr/bin/python3.11 _merge_pipeline.py >> "$REPO/cron.log" 2>&1

# 运行监测
/usr/bin/python3.11 -u monitor.py >> "$REPO/cron.log" 2>&1

# 推送变化的文件到 GitHub
git add index.html users.html report_status.html

if git diff --cached --quiet; then
    printf '[%s] 无变化，跳过推送\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 0
fi

git commit -m "自动更新标记评论 [$(date '+%m-%d %H:%M')]"
git pull --rebase --autostash origin master
git push origin master
printf '[%s] 已推送到 GitHub\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
