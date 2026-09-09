#!/bin/bash
# Pi Agent Monitor V3 看板启动脚本：起 watchdog 守护（或直接起服务）+ 打开浏览器
cd "$(dirname "$0")"

PORT=8571
URL="http://127.0.0.1:$PORT/"
PID_FILE="/tmp/agentmonitor_watchdog_v3.pid"

# 1) 服务已在跑 → 直接开浏览器
if curl -s "http://127.0.0.1:$PORT/api/services" >/dev/null 2>&1; then
  echo "服务已运行，直接打开看板..."
else
  # 2) watchdog 未起 → 起 watchdog（watchdog 会拉起 server 并自愈守护）
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
    echo "watchdog 已在守护，等待服务就绪..."
  else
    echo "启动 watchdog 守护 + 本地服务 (port $PORT) ..."
    python3 watchdog.py
  fi

  # 3) 等服务就绪（最多 ~10s）
  for _ in $(seq 1 20); do
    if curl -s "http://127.0.0.1:$PORT/api/services" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
fi

# 用默认浏览器打开
echo "打开浏览器: $URL"
open "$URL"
echo ""
echo "看板已启动。"
echo "  服务日志: /tmp/agentmonitor_server_v3.log"
echo "  守护日志: /tmp/agentmonitor_watchdog_v3.log"
echo "  停止服务: pkill -f 'agentmonitor.server'"
echo "  停止守护: kill \$(cat /tmp/agentmonitor_watchdog_v3.pid)"
