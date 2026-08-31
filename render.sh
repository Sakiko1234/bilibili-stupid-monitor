#!/bin/bash
set -Eeuo pipefail

REPO=/root/bilibili-stupid-monitor
cd "$REPO"
LOCK_PATH=/run/lock/bilibili-stupid-monitor.lock
exec 9>"$LOCK_PATH"
if ! /usr/bin/flock -n 9; then
    printf '[%s] render skipped: another job holds lock\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 0
fi

if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    printf '[%s] ERROR: stale git rebase state; refusing to publish\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 1
fi

set -a
source .env
set +a

# 合并 pipeline 数据
/usr/bin/python3.11 _merge_pipeline.py >> "$REPO/cron.log" 2>&1

# 只复生 HTML + 推送，不跑 monitor.py（与 pipeline 冲突）
/usr/bin/python3.11 -c '
import json, datetime, subprocess
from monitor import build_html, build_users_html, check_report_results

ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
with open("data/flagged.json", encoding="utf-8") as f:
    data = json.load(f)

html = build_html(data) + "\n<!-- rendered at " + ts + " -->"
with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

users = build_users_html(data) + "\n<!-- rendered at " + ts + " -->"
with open("users.html", "w", encoding="utf-8") as f:
    f.write(users)

# 检查举报结果（更新 tracking 文件）
check_report_results()

# 用新脚本生成带分页检索的报告页
subprocess.run(["/usr/bin/python3.11", "_build_report_html.py"], check=True)
print("HTML regenerated")
' >> "$REPO/cron.log" 2>&1

git add index.html users.html report_status.html
if git diff --cached --quiet; then
    printf '[%s] 网页无变化\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
    exit 0
fi

git commit -m "自动更新网页 [$(date '+%m-%d %H:%M')]"
git pull --rebase --autostash origin master
git push origin master
printf '[%s] 已推送到 GitHub\n' "$(date '+%m-%d %H:%M:%S')" >> "$REPO/cron.log"
