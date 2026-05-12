#!/bin/bash
cd /root/bilibili-stupid-monitor

# 加载环境变量
set -a
source .env
set +a

# 合并 pipeline 数据
/usr/bin/python3.11 _merge_pipeline.py >> cron.log 2>&1

# 运行监测
/usr/bin/python3.11 -u monitor.py >> cron.log 2>&1

# 推送变化的文件到 GitHub Pages
git add index.html users.html report_status.html

if git diff --cached --quiet; then
    echo "[$(date '+%m-%d %H:%M')] 无变化，跳过推送" >> cron.log
else
    git commit -m "自动更新标记评论 [$(date '+%m-%d %H:%M')]"
    git pull --rebase --autostash origin master
    git push origin master
    echo "[$(date '+%m-%d %H:%M')] 已推送到 GitHub" >> cron.log
fi
