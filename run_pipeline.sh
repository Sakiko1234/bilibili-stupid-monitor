#!/bin/bash
cd /root/bilibili-stupid-monitor

set -a
source .env
set +a

echo "[$(date '+%m-%d %H:%M:%S')] Pipeline start" >> cron.log
/usr/bin/python3.11 pipeline.py >> cron.log 2>&1
echo "[$(date '+%m-%d %H:%M:%S')] Pipeline done, generating HTML..." >> cron.log

# 用 monitor.py 的 build_html / build_users_html 重新生成页面
/usr/bin/python3.11 -c "
import json
from monitor import build_html, build_users_html, check_report_results, build_report_html
data = json.load(open('data/flagged.json'))
open('index.html','w').write(build_html(data))
open('users.html','w').write(build_users_html(data))
tracking = check_report_results()
open('report_status.html','w').write(build_report_html(tracking))
print('HTML regenerated')
" >> cron.log 2>&1
echo "[$(date '+%m-%d %H:%M:%S')] HTML done" >> cron.log

# 推送
git add index.html users.html report_status.html data/flagged.json data/.checked.json data/pipeline/ data/.pipeline_state.json 2>/dev/null

if git diff --cached --quiet; then
    echo "[$(date '+%m-%d %H:%M')] Pipeline 无变化" >> cron.log
else
    git commit -m "Pipeline自动更新 [$(date '+%m-%d %H:%M')]"
    git pull --rebase --autostash origin master
    git push origin master
    echo "[$(date '+%m-%d %H:%M')] Pipeline 已推送到 GitHub" >> cron.log
fi
