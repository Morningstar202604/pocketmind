#!/usr/bin/env bash
# ⚠ 已废弃（legacy）：本脚本属于旧版 goose 架构，硬编码 /workspace/... 路径，
#   仅存在于原 AI 沙箱，真机不可用。新版系统为 Python agentd，请使用：
#   bash termux/start.sh   或   bash web/start.sh
#
# 旧看门狗：确保 goose serve (3284) 和单端口 web 服务器 (5173) 始终存活
# 每 20s 检查一次，挂了就重启。无 Vite、无 bridge 独立进程。

# 若存在 .env（被 git 忽略，本地凭据），载入 LLM_API_KEY 等变量
if [ -f "$(cd "$(dirname "$0")" && pwd)/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$(cd "$(dirname "$0")" && pwd)/.env"
  set +a
fi

GOOSE_PORT=3284
WEB_PORT=5173
GOOSE_BIN=/workspace/goose/target/release/goose

export GOOSE_PATH_ROOT=$HOME/.goose-test
export GOOSE_PROVIDER=custom_llm
export GOOSE_MODEL=agnes-3.0-flash
export LLM_API_KEY="${LLM_API_KEY:-}"
export GOOSE_DISABLE_KEYRING=1

port_alive_goose() {
  curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:$1/acp" 2>/dev/null | grep -qE "406|200"
}

port_alive_web() {
  curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:$1/api/health" 2>/dev/null | grep -q "200"
}

start_goose() {
  local model="agnes-3.0-flash" temp="" maxtok="" thinking="" key=""
  if [ -f /workspace/web/settings.json ]; then
    model=$(sed -n 's/.*"model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' /workspace/web/settings.json | head -1)
    temp=$(sed -n 's/.*"temperature"[[:space:]]*:[[:space:]]*\([0-9.]*\).*/\1/p' /workspace/web/settings.json | head -1)
    maxtok=$(sed -n 's/.*"maxTokens"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' /workspace/web/settings.json | head -1)
    thinking=$(sed -n 's/.*"thinking"[[:space:]]*:[[:space:]]*"\([a-z]*\)".*/\1/p' /workspace/web/settings.json | head -1)
    key=$(sed -n 's/.*"apiKey"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' /workspace/web/settings.json | head -1)
  fi
  [ -n "$key" ] && export LLM_API_KEY="$key"
  [ -n "$model" ] && export GOOSE_MODEL="$model"
  [ -n "$temp" ] && export GOOSE_TEMPERATURE="$temp" || unset GOOSE_TEMPERATURE
  [ -n "$maxtok" ] && export GOOSE_MAX_TOKENS="$maxtok" || unset GOOSE_MAX_TOKENS
  [ -n "$thinking" ] && export GOOSE_THINKING_EFFORT="$thinking" || unset GOOSE_THINKING_EFFORT
  echo "[$(date)] watchdog: 启动 goose serve on :$GOOSE_PORT (model=${GOOSE_MODEL:-默认})"
  cd /workspace/goose/target/release
  setsid nohup env GOOSE_PATH_ROOT="$HOME/.goose-test" GOOSE_PROVIDER=custom_llm GOOSE_DISABLE_KEYRING=1 GOOSE_DISABLE_TELEMETRY=1 LLM_API_KEY="${LLM_API_KEY:-}" ${GOOSE_MODEL:+GOOSE_MODEL="$GOOSE_MODEL"} ${GOOSE_TEMPERATURE:+GOOSE_TEMPERATURE="$GOOSE_TEMPERATURE"} ${GOOSE_MAX_TOKENS:+GOOSE_MAX_TOKENS="$GOOSE_MAX_TOKENS"} ${GOOSE_THINKING_EFFORT:+GOOSE_THINKING_EFFORT="$GOOSE_THINKING_EFFORT"} $GOOSE_BIN serve --dangerously-unauthenticated --host 127.0.0.1 --port $GOOSE_PORT >>/tmp/goose_wd.log 2>&1 < /dev/null &
  echo "[$(date)] watchdog: goose PID=$!"
}

start_web() {
  echo "[$(date)] watchdog: 启动 web server on :$WEB_PORT"
  cd /workspace/web
  setsid nohup env LLM_API_KEY="${LLM_API_KEY:-}" node server.mjs >>/tmp/web_wd.log 2>&1 < /dev/null &
  echo "[$(date)] watchdog: web PID=$!"
}

echo "[$(date)] watchdog: 启动看门狗 (goose + web 单端口方案)"
while true; do
  if ! port_alive_goose $GOOSE_PORT; then
    start_goose
    sleep 4
  fi
  if ! port_alive_web $WEB_PORT; then
    start_web
    sleep 3
  fi
  sleep 20
done
