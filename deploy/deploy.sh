# 口袋 Agent · 一键部署脚本
# 支持三种方式：
#   1) bash deploy/deploy.sh            # 本机部署（systemd 或 nohup）+ nginx 反代
#   2) docker compose up -d             # Docker 部署（见 DEPLOY.md）
#   3) 手动：python3 -m agentd.main --lan --port 8787
#
# 安全要点：
#   - 默认以 --lan 模式部署（公网可访问），自动生成随机访问令牌并写入配置
#   - 令牌在脚本输出与 $AGENT_HOME/config.json 中，请妥善保存
#   - 前端由 agentd 直接托管（dist 已内置），无需 Node
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8787}"
AGENT_HOME="${AGENT_HOME:-$HOME/.agent/termux-agent}"
PY="$(command -v python3 || command -v python)"

echo "== 1/4 安装依赖 =="
"$PY" -m pip install -q -r agentd/requirements.txt

echo "== 2/4 初始化数据目录（700）与访问令牌 =="
mkdir -p "$AGENT_HOME"
chmod 700 "$AGENT_HOME"
CFG="$AGENT_HOME/config.json"
TOKEN=""
if [ -f "$CFG" ]; then
  TOKEN="$("$PY" -c "import json,sys;d=json.load(open('$CFG'));print((d.get('server') or {}).get('token',''))" 2>/dev/null || true)"
fi
if [ -z "$TOKEN" ]; then
  TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  "$PY" - <<PYEOF
import json, os
p = "$CFG"
d = json.loads(p) if os.path.exists(p) else {}
try:
    d = json.load(open(p))
except Exception:
    d = {}
d.setdefault("server", {})["token"] = "$TOKEN"
d["server"]["allow_lan"] = True
os.makedirs(os.path.dirname(p), exist_ok=True)
os.chmod(os.path.dirname(p), 0o700)
with open(p, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.chmod(p, 0o600)
PYEOF
fi
echo "访问令牌: $TOKEN（请保存在安全的地方；页面设置里也可修改）"

echo "== 3/4 启动服务（--lan）=="
if command -v systemctl >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
  cp deploy/systemd/agentd.service /etc/systemd/system/agentd.service
  sed -i "s|^ExecStart=.*|ExecStart=$PY -m agentd.main --lan --port $PORT|" /etc/systemd/system/agentd.service
  sed -i "s|^Environment=.*|Environment=AGENT_HOME=$AGENT_HOME|" /etc/systemd/system/agentd.service
  systemctl daemon-reload
  systemctl enable agentd
  systemctl restart agentd
  echo "systemd: 已启用 agentd 服务（开机自启）"
else
  mkdir -p "$AGENT_HOME/logs"
  pkill -f "agentd.main --lan --port $PORT" 2>/dev/null || true
  nohup env AGENT_HOME="$AGENT_HOME" "$PY" -m agentd.main --lan --port "$PORT" >> "$AGENT_HOME/logs/agentd.log" 2>&1 &
  echo "nohup: PID=$!，日志 $AGENT_HOME/logs/agentd.log"
fi

echo "== 4/4 配置 nginx 反代（80 → $PORT）=="
if command -v nginx >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
  cp deploy/nginx/agentd.conf /etc/nginx/sites-available/agentd.conf
  sed -i "s|proxy_pass http://127.0.0.1:8787;|proxy_pass http://127.0.0.1:$PORT;|" /etc/nginx/sites-available/agentd.conf
  ln -sf /etc/nginx/sites-available/agentd.conf /etc/nginx/sites-enabled/agentd.conf
  nginx -t && systemctl reload nginx
  echo "nginx: 已反代 http://<服务器IP>/ → 127.0.0.1:$PORT"
fi

echo ""
echo "完成 ✅"
echo "  本机访问:  http://127.0.0.1:$PORT"
echo "  外网访问:  http://<服务器公网IP>/（经 nginx 80 端口）"
echo "  访问令牌:  $TOKEN（首次打开页面会要求输入；页面右上角 ⚙ 设置 → 局域网访问令牌）"
echo "  数据目录:  $AGENT_HOME"
echo "  配置 LLM:  页面 ⚙ 设置 → 选择厂商 → 填 API Key → 保存"
