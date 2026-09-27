<div align="center">
  <h1>termux-agent · 口袋 Agent</h1>
  <p>在安卓手机（Termux）上运行的通用 AI Agent · Python agentd 后端 + React 网页前端，LLM 走外部 OpenAI 兼容 API</p>
  <img src="https://img.shields.io/badge/Platform-Android_Termux-green" alt="Platform" />
  <img src="https://img.shields.io/badge/Language-Python-blue" alt="Language" />
  <img src="https://img.shields.io/badge/UI-React_Web-8A2BE2" alt="UI" />
  <br />
</div>

---

## 架构

```
┌─────────────── 同一台安卓手机 ───────────────┐
│  Web UI（React，agentd 托管，可作 PWA 安装） │
│        ↓ SSE / REST（127.0.0.1:8787）        │
│  agentd（Python · FastAPI，单进程）           │
│    ├─ Agent 循环（流式 → 工具 → 审批 → 回填） │
│    ├─ tools/（shell / 文件 / phone·termux-api）│
│    └─ SQLite（会话 / 消息 / 记忆 / 定时任务）  │
└──────────────────────────────────────────────┘
        ↓ 工具调用                 ↓ 推理请求
   termux-api（通知/短信/定位/传感器…）   外部 LLM（豆包/DeepSeek/千问/Kimi/智谱/硅基流动…）
```

- **全部数据在本机**：会话、消息、记忆摘要、定时任务、文件备份都存本地 SQLite，权限 600/700，重启不丢上下文；
- **LLM 只负责推理**：通过 OpenAI 兼容端点调用，`base_url` + `api_key` 在页面「设置」里配置；
- **工具审批由服务端强制**：写文件 / shell / 发短信 / 拨号等危险操作需在界面确认，超时自动拒绝。

---

## 快速开始

### 方案 A：网页版（推荐，无需 APK）

```bash
# 1. 手机侧一次性准备（Termux 里执行）
pkg install git python termux-api
git clone <本仓库地址> && cd termux-agent
bash termux/install.sh          # 装 Python 依赖 + 数据目录（2-5 分钟，无需编译）

# 2. 启动服务
bash termux/start.sh            # 默认 http://127.0.0.1:8787

# 3. 浏览器打开 http://127.0.0.1:8787
#    右上角 ⚙ 设置：选模型厂商 → 填 API Key → 保存 → 开聊
```

> 手机浏览器访问同一 WiFi 下的服务：`bash termux/start.sh 8787 --lan`（务必先在设置里配置访问令牌）。

### 方案 B：本机开发 / 体验（不需要手机）

```bash
# 离线演示模式（不消耗 API，验证完整链路）
cd termux-agent
AGENT_HOME=/tmp/pa python3 -m agentd.main --mock --port 8787
# 浏览器打开 http://127.0.0.1:8787 ，发「帮我看看电池 / 读一下最近短信」即可看到工具链路

# 真实模式（需先在设置里配 LLM）
AGENT_HOME=/tmp/pa python3 -m agentd.main --port 8787
```

---

## 目录

| 路径 | 说明 |
|------|------|
| `agentd/` | **唯一服务端**：FastAPI（`main.py`）+ Agent 循环（`agent.py`）+ 工具（`tools/`）+ 存储/调度 |
| `agentd/web/` | React 前端（Vite+TS），构建产物 `dist/` 由 agentd 直接托管 |
| `termux/` | 手机端：`install.sh` 一键装依赖 / `start.sh` 启动 / `boot.sh` 开机自启 |
| `config/` | LLM 厂商预设（OpenAI 兼容端点） |
| `tests/` | 回归测试（排队 / 定时任务 / 安全加固） |

---

## 日常使用

```bash
bash termux/start.sh            # 启动（或 python3 -m agentd.main --port 8787）
bash termux/start.sh --lan      # 局域网访问（需先配访问令牌）
```

## 数据边界与安全

- **本地**：agent 代码、会话、文件读写、shell 执行、termux-api 调用、定时任务；
- **云端（仅推理）**：LLM API。agent 上下文可能携带本地文件内容，敏感文件请自行做白名单/脱敏；
- 默认只监听 `127.0.0.1`；`--lan` 强制要求访问令牌；
- 权限模式默认「逐次审批」：危险操作需界面确认，超时（默认 120s，可调）自动拒绝；
- 写文件 / 删文件执行前自动备份，对话里可一键撤销（`/api/undo`）。

## License

Apache-2.0（本仓库实现见 [LICENSE](LICENSE)）
