#!/usr/bin/env bash
# 口袋 Agent · 网页版一键启动（新版 agentd 后端）
#
# 注意：本文件已随项目重构改为启动 agentd（Python FastAPI）。
# 旧版 goose ACP 桥（node web/server.mjs）已废弃，其二进制路径仅存在于原 AI 沙箱，
# 在真机上无法运行，勿再使用。
#
# 用法:
#   bash web/start.sh                # 本机访问 http://127.0.0.1:8787
#   bash web/start.sh 8788           # 自定义端口
#   bash web/start.sh 8787 --lan     # 局域网访问（需先在设置里配置访问令牌）
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8787}"
EXTRA=""
if [ "${2:-}" = "--lan" ]; then
  EXTRA="--lan"
  echo "⚠ 局域网模式：必须已配置访问令牌，否则服务会拒绝启动（避免整机能力被局域网内任意设备调用）。"
  echo "  两种方式：本机启动时在页面「设置 → 局域网访问令牌」填好；或直接 AGENT_TOKEN=<令牌> bash web/start.sh $PORT --lan"
fi

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "✗ 未找到 python3/python，请先安装 Python 3.11+" >&2
  exit 1
fi

echo "启动口袋 Agent（端口 $PORT）..."
echo "浏览器打开 http://127.0.0.1:$PORT"
exec "$PY" -m agentd.main --port "$PORT" $EXTRA
