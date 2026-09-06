#!/bin/zsh
cd "$(dirname "$0")"
if /usr/bin/curl -fsS http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
  open http://127.0.0.1:8765
  echo '工作台已经启动。请在页面中打开小红书登录。'
  exit 0
fi
(
  for attempt in {1..60}; do
    if /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
      open http://127.0.0.1:8765
      exit 0
    fi
    sleep 1
  done
  echo '服务尚未就绪，请查看此窗口中的报错。'
) &
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
