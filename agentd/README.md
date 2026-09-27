# 口袋 Agent（agentd）—— 手机本地智能体服务

重构后的核心：**Python 单进程**（FastAPI + agent 循环），替代旧实现的
goose(Rust) + Node 双服务器。前端为 `agentd/web/`（Vite + React + TS），构建产物 `agentd/web/dist`
直接由 agentd 托管，手机端零 Node 依赖。版本 v0.2.0。

## 架构

```
浏览器 / APK 壳 ──HTTP+SSE──▶ agentd (FastAPI, :8787, 默认仅本机)
                                 ├─ Agent 循环（openai 兼容 function calling，国产模型路由分流）
                                 ├─ 工具层（31 个）：phone(termux-api) / files / system / voice / mcp
                                 ├─ 审批门（服务端强制，写/危险操作需确认；超时自动拒绝）
                                 ├─ SQLite（会话/消息/记忆/定时任务/撤销备份，重启不丢）
                                 ├─ 调度器（APScheduler：cron/间隔/一次性/条件触发，jobs.db 持久化）
                                 ├─ 通知（termux-notification 四态进度，缺 termux-api 自动降级）
                                 └─▶ LLM API（豆包/DeepSeek/千问/Kimi/智谱/硅基流动…）
```

## 运行

```bash
# 开发（本机）
python -m agentd.main --mock --port 8787     # 离线演示，不消耗 API
python -m agentd.main --port 8787            # 真实模式（先在页面配置 LLM）

# 手机（Termux）
bash termux/install.sh       # 一键安装（依赖 + 数据目录 700）
bash termux/start.sh         # 启动
bash termux/uninstall.sh     # 卸载（--keep-data 保留数据）
# 开机自启：见 termux/boot.sh（Termux:Boot 插件）
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | `{session_id?, message}` → SSE 事件流 |
| POST | `/api/approval` | `{session_id, tool_call_id, decision: allow_once\|allow_always\|deny}` |
| POST | `/api/undo` | `{session_id, tool_call_id}` 撤销文件类危险操作 |
| POST | `/api/stop` | 中断当前回复并清空排队 |
| GET/POST/DELETE | `/api/sessions[/{id}]` | 会话管理 |
| PUT | `/api/sessions/{id}` | 会话重命名 |
| GET | `/api/sessions/{id}/messages` | 会话消息历史 |
| GET/PUT | `/api/settings` | 设置（GET 时 API Key 打码） |
| GET | `/api/providers` | 国产 LLM 厂商预设 |
| GET | `/api/tools` | 工具清单（含分组与风险分级） |
| GET/POST/PUT/DELETE | `/api/jobs[/{id}]` | 定时/条件触发任务 |
| GET | `/api/mcp` | MCP 客户端配置与连接状态 |
| GET | `/api/export` | 导出全部会话+配置（脱敏）JSON 备份 |
| GET | `/api/health` | 健康检查 |

SSE 事件：`session` → `thinking`/`text`（流式）→ `tool_start` → `approval`（可选）→
`tool_update`（可带 `undoable`）→ `queued`（排队）→ `done`/`error`，15s 心跳保活。

## 安全设计

- 默认只监听 `127.0.0.1`；`--lan` 时所有 `/api/*` 校验 `Authorization: Bearer <token>`，
  **未配置令牌时 `--lan` 直接拒绝启动**；
- 工具按危险度分级：`safe` / `write` / `danger`，`approve` 模式下后两类必须用户确认；
  模型幻觉出的**未知工具同样要求确认**（按 danger），不会静默执行；审批超时自动拒绝；
- **危险操作可回滚（checkpoint/undo）**：写文件覆盖、删文件执行前自动备份到
  `checkpoints/`（目录 700、文件 600），记录落 SQLite；对话里一键撤销，服务重启仍有效；
- API Key 只存本地 `config.json`（0600），任何接口不回传明文；导出备份也不含密钥；
- shell 输出截断、超时限制；文件路径白名单（仅主目录与存储目录）、写入限 1MB；
  子进程超时强制 kill；删除会话级联清理消息/记忆/审批记忆/撤销记录/定时任务；
- 数据目录、数据库、备份目录均为 700/600，仅本人可读。

## 工具（31 个，按分组）

| 分组 | 工具 | 风险 |
|---|---|---|
| 手机能力（25） | get_battery / send_notification / show_toast / vibrate / set_torch / get_volume / set_volume / set_brightness / send_sms / read_sms / make_call / get_location / get_clipboard / set_clipboard / list_sensors / read_sensor / tts_speak / speech_to_text / get_wifi_info / scan_wifi / set_wifi / take_photo / share_text / download_file / open_target | 只读/需确认/危险分级 |
| 文件（4） | list_dir / read_file（只读）、write_file（需确认）、delete_file（危险，可撤销） | |
| 系统（1） | run_shell（危险，任意命令需确认） | |
| 语音（1） | tts_offline（离线本地 TTS，可选，见 termux/voice.md） | 只读 |
| MCP 扩展 | `mcp__服务__工具`（可选接入，默认需审批） | |

termux-api 未安装时返回结构化错误，由模型转告执行 `pkg install termux-api`。

## 能力进度

- **M2 记忆**：会话自动命名（≤14 字）、滚动记忆摘要注入下一轮；
- **M3 美术**：口袋品牌 Logo、明暗双主题（跟随系统）、内联图标库、PWA manifest；
- **M4 安装形态**：Service Worker 离线壳（导航 network-first）+ WebView 壳 APK 模板（零依赖）；
- **M5 安全加固**：权限 600/700、短 key 打码、删除级联、真机矩阵（termux/device-matrix.md）；
- **P0 先进方案**：固定 system 前缀（提示缓存命中约 1 折）、思考模型识别分流
  （reasoner 类不假装执行工具）、同一会话连发排队自动执行、长期偏好 user_prefs
  注入系统提示、审批时自动展开思考链；
- **P1 进阶能力**：定时/条件触发（APScheduler + 自管持久化，前端面板）、跨会话相关
  记忆检索、通知栏四态进度、工具参数 pydantic 校验、MCP 最小客户端（零依赖 stdio
  JSON-RPC）、sherpa-onnx 离线语音（可选）；
- **基础设施轮**：checkpoint/undo、工具分组展示、会话重命名、Termux 开机自启 +
  安装/卸载脚本、数据导出备份。

## 真实模型联调

内置 7 家国产厂商预设（豆包/DeepSeek/通义/Kimi/智谱/硅基流动/自定义），
base_url 与默认模型已于 2026-09-25 对照各厂商官方文档核验。联调步骤：

1. 浏览器打开 `http://127.0.0.1:8787` → 设置 → 选厂商 → 填 API Key → 保存；
2. 顶部状态变为「已就绪」即配置成功；
3. 对话一句「查看电池电量」验证工具调用 → 审批 → 回填全链路。

注意：厂商新模型名更新后（如 DeepSeek 的 deepseek-flash、Kimi 的 kimi-k3），
预设已同步；仍提示 401/404 时先在厂商控制台确认 Key 有效与模型名。

## 前端开发

```bash
cd agentd/web
npm install
npm run dev        # 开发模式（/api 代理到 8787）
npm run build      # 产出 dist/，agentd 直接托管
```

> 前端零额外运行时依赖（React + marked + dompurify），CSS 设计 token 化，
> 未引入 Tailwind/组件库，保持轻量。
