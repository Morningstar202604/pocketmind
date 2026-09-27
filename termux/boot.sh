#!/usr/bin/env bash
# 口袋 Agent · 开机自启（Termux:Boot）
#
# 安装（Termux 里执行）：
#   1) pkg install termux-api termux-services   （termux-services 提供开机运行）
#   2) pkg install termux-boot                   （Termux:Boot 插件，开机拉起 Termux）
#   3) 安装 Termux:Boot APK（F-Droid 或 GitHub Releases），至少手动打开一次
#   4) mkdir -p ~/.termux/boot && cp termux/boot.sh ~/.termux/boot/agentd.sh
#   5) chmod +x ~/.termux/boot/agentd.sh
#
# 之后每次开机，Termux:Boot 会自动执行本脚本启动 agentd。
# 开机后还建议开启常驻：pkg install termux-services && sv-enable termux
set -euo pipefail
cd "$(dirname "$0")/.."

# 端口可通过环境变量覆盖（默认 8787）；开机自启场景一般用默认即可。
PORT="${AGENT_PORT:-8787}"

# 唤醒锁：开机后保持 CPU 不休眠，否则定时任务/远程指令会被系统冻结。
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

# 开机启动稍等几秒，等网络/环境就绪（Termux:Boot 会在开机后尽快执行）
sleep 5

# 守护：若 agentd 已在跑则不动
if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "口袋 Agent 已在运行，跳过启动。"
  exit 0
fi

# 拉起 agentd（后台运行，日志写到数据目录）
mkdir -p "$HOME/.agent/termux-agent/logs"
nohup python -m agentd.main --port "$PORT" \
  >> "$HOME/.agent/termux-agent/logs/boot.log" 2>&1 &

# 保活：开机脚本退出后进程不会自动被杀；如需系统级保活请用 termux-services
sleep 2
if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "口袋 Agent 开机自启成功。"
else
  echo "启动失败，日志：$HOME/.agent/termux-agent/logs/boot.log"
fi
