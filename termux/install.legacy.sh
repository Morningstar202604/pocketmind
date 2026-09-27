#!/usr/bin/env bash
# ⚠ 已废弃（legacy）：旧版 goose 架构的安装脚本（编译 Rust goose，需 3GB 源码）。
# 新版 agentd 使用：bash termux/install.sh（纯 Python，2-5 分钟）
set -euo pipefail

# One-shot install for the Android agent distribution on Termux.
#
# Usage:
#   pkg install termux-api            # 一次性，装 Android 桥接
#   termux-setup-storage              # 一次性，授予文件访问
#   bash install.sh                   # 安装 goose + 配置 provider
#
# 前置：把 config/provider.json 和 termux/termux-bridge 拷贝到手机上的 ~/.agent/ 目录

ROOT="${GOOSE_HOME:-$HOME/.agent}"
GOOSE_BIN="${GOOSE_BIN:-$HOME/.local/bin/goose}"

usage() {
  cat <<'EOF'
Usage: bash install.sh [mode]

Modes:
  clone          克隆/更新 goose 源码到 $GOOSE_HOME/goose
  build [mode]   构建 goose（默认 portable-default，等价于 build portable）
  install-binary 构建后安装到 ~/.local/bin/goose
  setup-provider 把 $GOOSE_HOME/provider.json 写入 goose custom_providers
  setup-bridge   安装 termux-bridge 到 ~/.local/bin
  setup-env      安装/检查 $GOOSE_HOME/termux.env
  all            依次执行以上全部步骤（默认）
EOF
}

MODE="${1:-all}"

cmd_clone() {
  echo "[clone] 克隆/更新 goose..."
  mkdir -p "$ROOT"
  if [ -d "$ROOT/goose" ]; then
    git -C "$ROOT/goose" pull --ff-only || echo "提示：git pull 失败（可能离线或分叉），跳过更新"
  else
    git clone --depth 1 https://github.com/aaif-goose/goose.git "$ROOT/goose"
  fi
}

cmd_build() {
  local variant="${1:-portable-default}"
  echo "[build] 构建 goose（feature: $variant）..."
  command -v cargo >/dev/null || { echo "缺少 Rust 工具链，执行: pkg install rust"; exit 1; }
  command -v "$ROOT/goose/target/release/goose" >/dev/null || cmd_clone
  ( cd "$ROOT/goose" && cargo build --release -p goose-cli --bin goose --no-default-features --features "$variant" )
}

cmd_install_binary() {
  echo "[install-binary] 安装 goose 到 $GOOSE_BIN ..."
  mkdir -p "$HOME/.local/bin"
  cp "$ROOT/goose/target/release/goose" "$GOOSE_BIN"
}

cmd_setup_provider() {
  echo "[setup-provider] 配置 provider..."
  if [ ! -f "$ROOT/provider.json" ]; then
    echo "未找到 $ROOT/provider.json，请先拷贝 config/provider.json 到 $ROOT/"
    exit 1
  fi
  # 文件名必须与 provider.json 的 name 字段一致，goose 按 {name}.json 加载
  local provider_name
  if command -v jq >/dev/null 2>&1; then
    provider_name=$(jq -r '.name' "$ROOT/provider.json")
  else
    provider_name=$(grep -o '"name"[[:space:]]*:[[:space:]]*"[^"]*"' "$ROOT/provider.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
  fi
  mkdir -p "$HOME/.config/goose/custom_providers"
  cp "$ROOT/provider.json" "$HOME/.config/goose/custom_providers/$provider_name.json"
  echo "provider 已写入 ~/.config/goose/custom_providers/$provider_name.json"
}

cmd_setup_bridge() {
  echo "[setup-bridge] 安装 termux-bridge..."
  [ -f "$ROOT/termux-bridge" ] || { echo "未找到 $ROOT/termux-bridge"; exit 1; }
  chmod +x "$ROOT/termux-bridge"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$ROOT/termux-bridge" "$HOME/.local/bin/termux-bridge"
}

cmd_setup_env() {
  echo "[setup-env] 安装 termux.env..."
  [ -f "$ROOT/termux.env" ] || { echo "未找到 $ROOT/termux.env"; exit 1; }
  echo "请编辑 $ROOT/termux.env 填入 LLM_API_KEY（及 provider.json 中的 base_url/model）"
  echo "每次运行前: source $ROOT/termux.env && $GOOSE_BIN chat"
}

cmd_all() {
  cmd_clone
  cmd_build portable-default
  cmd_install_binary
  cmd_setup_provider
  cmd_setup_bridge
  cmd_setup_env
  echo "完成。运行: source $ROOT/termux.env && $GOOSE_BIN chat"
}

case "$MODE" in
  clone) cmd_clone ;;
  build) cmd_build "${2:-portable-default}" ;;
  install-binary) cmd_install_binary ;;
  setup-provider) cmd_setup_provider ;;
  setup-bridge) cmd_setup_bridge ;;
  setup-env) cmd_setup_env ;;
  all) cmd_all ;;
  *) usage; exit 1 ;;
esac
