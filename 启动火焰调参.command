#!/bin/zsh
cd "${0:A:h}"
if curl -fsS --max-time 2 http://127.0.0.1:8765/tuning >/dev/null 2>&1; then
  open http://127.0.0.1:8765/tuning
  exit 0
fi
if curl -fsS --max-time 2 http://127.0.0.1:8765/ >/dev/null 2>&1; then
  echo '旧版 SeeEnd 正在运行，请退出它后再次打开此启动器。'
  read 'answer?按回车退出…'
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo '缺少 .venv 环境，请按照 README 安装项目依赖。'
  read 'answer?按回车退出…'
  exit 1
fi
.venv/bin/python -m aiflame --hub &
hub_pid=$!
trap 'kill -TERM "$hub_pid" 2>/dev/null' EXIT INT TERM
for attempt in {1..30}; do
  if curl -fsS --max-time 1 http://127.0.0.1:8765/tuning >/dev/null 2>&1; then
    open http://127.0.0.1:8765/tuning
    echo '火焰调参已启动。请保持此窗口开启；按 Ctrl+C 结束服务。'
    wait "$hub_pid"
    exit $?
  fi
  sleep 0.5
done
echo '服务未能启动，请查看上方错误信息。'
read 'answer?按回车退出…'
