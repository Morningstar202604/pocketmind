# 口袋 Agent · 快速开始（新版）

> 本仓库已按《审查报告与重构方案》完成 M1+M2+M3 重构：**Python agentd + FastAPI + 一套 Web UI**。
> 旧实现（goose/Node/Flutter）已全部清理，本仓库仅保留新架构。

## 30 秒看懂新版结构

```
agentd/                 # Python 后端（唯一服务端，单进程）
  ├─ main.py            # FastAPI：静态页 + /api/* + SSE 流式
  ├─ agent.py           # Agent 循环（流式 → 工具 → 审批 → 回填 → 记忆摘要）
  ├─ tools/             # shell / files / phone(termux-api 30 个工具)
  └─ web/               # 前端（Vite+React+TS），dist 由 agentd 直接托管
termux/                 # 手机端：install.sh（2-5 分钟装好）/ start.sh
QUICKSTART.md           # 本文件
```

## 在手机上用（Termux）

```bash
pkg install git python
git clone <本仓库地址> && cd termux-agent
bash termux/install.sh          # 装 Python 依赖 + termux-api（无需编译）
bash termux/start.sh            # 启动服务
# 浏览器打开 http://127.0.0.1:8787
# 右上角 ⚙ 设置：选模型厂商（豆包/DeepSeek/千问/Kimi/智谱/硅基流动）→ 填 API Key → 保存
```

## 在本机开发/体验（不需要手机）

```bash
# 离线演示模式（不消耗 API，可验证完整链路）
cd termux-agent
AGENT_HOME=/tmp/pa python3 -m agentd.main --mock --port 8787
# 浏览器打开 http://127.0.0.1:8787
# 试试：帮我看看电池 / 读一下最近短信 / 帮我定位 / 发条通知 / 用工具看看

# 真实模式
AGENT_HOME=/tmp/pa python3 -m agentd.main --port 8787
```

## 前端开发

```bash
cd agentd/web && npm install && npm run dev   # 开发（/api 代理到 8787）
npm run build                                  # 构建 dist（agentd 直接托管，手机端零 Node 依赖）
```

## 安全须知

- 默认只监听本机 `127.0.0.1`；开启局域网访问用 `--lan`，并务必先在设置里配置访问令牌；
- 权限模式默认「逐次审批」：写文件/危险操作（shell、删除、发短信、拨号等）需在界面确认，
  超时（默认 120s，可调）自动拒绝；
- 会话、消息、记忆摘要存本地 SQLite，服务重启不丢上下文。

## 里程碑进度

| 阶段 | 状态 |
|---|---|
| M1 最小闭环（agentd + SSE 流式聊天 + 基础工具 + 审批门 + 会话存储） | ✅ 已完成 |
| M2 手机能力扩展（termux-api 30 个工具：短信/电话/定位/传感器/TTS/WiFi 等）+ 会话自动命名 + 记忆摘要 + 审批超时可配 | ✅ 已完成 |
| M3 美术重构（品牌口袋 Logo 全套 + PWA manifest/图标 + 明暗双主题可切换 + 内联图标库 + 设置分组） | ✅ 已完成 |
| 查漏补缺（--lan 强制令牌、前端令牌自动携带、未知工具也审批、SSE 心跳、子进程超时清理、删除级联清理、写文件限 1MB） | ✅ 已完成 |
| M4 APK/PWA（Service Worker 离线壳 + PWA 安装指引 + WebView 壳 APK 模板工程） | ✅ 已完成 |
| M5 安全加固、真机矩阵、文档（数据库/目录权限 600/700、短 key 打码改进、超时拒绝回归、device-matrix 排障手册） | ✅ 已完成 |
| P0 先进方案落地（固定 system 前缀→提示缓存命中 1 折、思考模型识别分流、消息排队自动执行、长期偏好 user_prefs 跨会话注入、审批时自动展开思考链） | ✅ 已完成 |
| P1 进阶能力（定时/条件触发 APScheduler+自管持久化、跨会话相关记忆检索、通知栏持久进度、工具参数 pydantic 校验、MCP 最小客户端可选接入、sherpa-onnx 离线语音可选） | ✅ 已完成（本轮） |
| P2 基础设施（危险操作 checkpoint/undo 自动备份可撤销、工具按分组展示、会话重命名、Termux 开机自启 boot.sh + 安装脚本完善、数据导出备份 JSON） | ✅ 已完成（本轮） |
| P2 模型/国外（llama.cpp 离线降级、Telegram 远程入口、Plan-and-Act） | ⬜ 按用户要求暂缓 |
