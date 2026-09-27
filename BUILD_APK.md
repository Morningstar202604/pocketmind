# 构建 Termux Agent 安卓 APK

> ⚠ **重要提示（已废弃）**：本 `app/`（Flutter）属于重构前的旧版 goose 架构，
> APK 默认连接旧后端 ACP（`ws://127.0.0.1:3284/acp`）。当前新版后端是 Python
> agentd（FastAPI + SSE，端口 8787），**没有 ACP 服务**，此 APK 与新后端不兼容。
> **推荐入口：网页版 / PWA**（`bash termux/start.sh` → 浏览器打开 8787，可"安装到主屏"）。
> 以下内容仅作为旧版构建流程的参考。

本仓库提供 `app/scripts/build_apk.sh` 一键脚本，自动安装 JDK、Android SDK、Flutter 并构建 APK。

## 本地构建

```bash
# debug APK（可直接 adb 安装）
bash app/scripts/build_apk.sh

# release APK（带 debug keystore 兜底签名，正式分发请自备 keystore）
bash app/scripts/build_apk.sh release
```

产物位置：

- debug: `app/build/app/outputs/flutter-apk/app-debug.apk`
- release: `app/build/app/outputs/flutter-apk/app-release.apk`

## 手机端使用前置

APK 是纯前端壳，必须先在手机 Termux 里装好 goose 后端：

```bash
# 1. 装好 Termux 与桥接
pkg install termux-api
termux-setup-storage

# 2. 跑安装脚本（clone goose 源码 + 构建 + 配置 provider）
bash termux/install.sh all

# 3. 填入 LLM API Key
vi ~/.agent/termux.env
# 编辑 LLM_API_KEY=your-key-here

# 4. 启动 goose 服务
source ~/.agent/termux.env
~/.local/bin/goose serve --dangerously-unauthenticated --host 127.0.0.1 --port 3284
```

启动本 APK，默认连接 `ws://127.0.0.1:3284/acp`。

## 网页版（不需要 APK）

如果不想装 Termux/Flutter，也可以：

1. 在 PC 或服务器上跑 goose（参考仓库 `goose/` 目录的 BUILDING_LINUX.md）
2. 在本仓库 `web/` 目录：

```bash
cd web
npm install
LLM_API_KEY=your-key npm run build   # 产出 dist/
node server.mjs                       # 单端口 5173：静态 + API + ACP
```

3. 浏览器打开 `http://localhost:5173`，即可在网页端跟本地 goose 对话。

## 配置

`web/settings.json` 保存 LLM 模型、温度、最大 token、思考强度、工具权限等。
默认值在 `web/server.mjs` 里。改完设置后点「保存并重启」会自动重启 goose 进程。
