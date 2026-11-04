#!/bin/bash
# 期货分析工作台容器入口：启动 web 服务 + cron 定时刷新
set -e

cd /opt/futures-workbench

# 首次启动：若 data/ 为空（全新容器/空持久卷），先完整刷新一次，让网站立即可访问
if [ -z "$(ls -A data 2>/dev/null)" ]; then
  echo "[entrypoint] 首次运行：data/ 为空，执行一次完整刷新（data + opinion）..."
  python cloud_cron.py both || echo "[entrypoint] 首次刷新失败，稍后 cron 会重试"
fi

# 启动常驻 web 服务（Python 标准库，零依赖；默认 0.0.0.0:8011）
cd /opt/futures-workbench/web
HOST=0.0.0.0 PORT=${PORT:-8011} python app/main.py &
WEB_PID=$!

# 启动 cron（定时刷新由 crontab 定义）
echo "[entrypoint] 启动 cron 定时刷新..."
cron || service cron start || true

echo "[entrypoint] 工作台已启动 (web pid=$WEB_PID)，访问 http://0.0.0.0:${PORT:-8011}"
wait $WEB_PID
