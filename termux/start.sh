#!/usr/bin/env bash
# 口袋 Agent · 启动 agentd（本地智能体服务）
#
# 用法：
#   bash termux/start.sh                 # 本机访问 http://127.0.0.1:8787
#   bash termux/start.sh 8788            # 自定义端口
#   bash termux/start.sh 8787 --lan      # 局域网访问（建议先在设置里配置访问令牌）
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8787}"
EXTRA=""
if [ "${2:-}" = "--lan" ]; then
  EXTRA="--lan"
  echo "⚠ 局域网模式：请确认已在页面设置里配置访问令牌，否则任何设备都能调用本服务。"
fi

echo "启动口袋 Agent（端口 $PORT）..."
# 唤醒锁：阻止 CPU 在锁屏后休眠（否则定时任务/工具回调会被系统冻结）。
# 退出时务必释放，避免空耗电池；termux-wake-lock 不存在（非 Termux）时静默跳过。
WAKE=0
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock && WAKE=1
fi
cleanup() { [ "$WAKE" = 1 ] && command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock; }
trap cleanup EXIT INT TERM

python -m agentd.main --port "$PORT" $EXTRA
