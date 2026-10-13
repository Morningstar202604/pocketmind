# PocketMind 功能对标主流方案报告

> 日期：2026-10-13
> 对标基准：**当前工作区代码的实际状态**（已修复部署/鉴权/契约问题，APK 已可构建）
> 方法：代码级盘点 + 主流方案公开资料调研（2026 年现状）

---

## 一、对标对象

| 方案 | 定位 |
|---|---|
| **PocketMind** | Android Termux 上的常驻手机 Agent（本地优先） |
| **Open Interpreter** | 桌面终端编码 Agent，本地代码执行，ACP 协议 |
| **Letta（MemGPT）** | 记忆优先的 Agent 框架，分层记忆分页 |
| **Google ADK** | 代码优先的多 Agent 编排框架 |
| **LangGraph** | 图状态机 Agent 编排框架 |
| **CrewAI** | 多 Agent 角色协作框架 |
| **Cline / Claude Code** | IDE/终端编码 Agent，Plan 模式 + 审批 |
| **Mobile-Agent** | 手机屏幕 UI 自动化 Agent（视觉驱动） |

---

## 二、能力矩阵

图例：🟢 完整/原生支持  🟡 部分/可选  🔴 缺失

| 能力维度 | **PocketMind** | Open Interpreter | Letta | Google ADK | LangGraph | CrewAI | Cline | Mobile-Agent |
|---|---|---|---|---|---|---|---|---|
| 移动端原生运行 | 🟢 Termux+APK | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🟢 |
| 手机硬件能力（短信/电话/定位/传感器…） | 🟢 25+ 工具 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🟢 屏幕操作 |
| 本地优先/数据不出设备 | 🟢 SQLite+0600 | 🟢 | 🟡 | 🟡 | 🟡 | 🟡 | 🟢 | 🟡 |
| LLM 无关（任意 OpenAI 兼容） | 🟢 +LiteLLM | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 |
| 服务端强制审批 | 🟢 approve/auto/chat | 🟡 沙盒确认 | 🟡 | 🟡 | 🟡 | 🟡 | 🟢 | 🔴 |
| 危险操作可回滚（undo） | 🟢 checkpoint+备份 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🟡 git | 🔴 |
| Plan/Act 双模式 | 🟢 | 🔴 | 🔴 | 🔴 | 🟡 | 🔴 | 🟢 | 🔴 |
| 记忆管理（跨会话检索） | 🟡 摘要+FTS5+Mem0 | 🔴 | 🟢 分层分页 | 🟡 | 🟢 | 🟢 | 🟡 | 🔴 |
| 长程上下文分页 | 🔴 块裁剪（80条） | 🔴 | 🟢 | 🟡 | 🟢 | 🟡 | 🟢 token压缩 | 🔴 |
| 多 Agent 协作 | 🔴 单 Agent | 🔴 | 🔴 | 🟢 | 🟢 | 🟢 | 🔴 | 🔴 |
| MCP | 🟡 Server+Client | 🟢 生态 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 🟡 |
| 定时任务/配方 | 🟢 APScheduler | 🔴 | 🟢 | 🟢 | 🟢 | 🔴 | 🔴 | 🔴 |
| 远程控制（IM） | 🟢 Telegram 审批 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| 浏览器/UI 自动化 | 🔴 | 🟢 | 🔴 | 🟡 | 🟢 | 🟡 | 🟢 编码 | 🟢 |
| 可观测性 | 🟡 OTEL | 🔴 | 🟢 | 🟢 | 🟢 LangSmith | 🟢 | 🔴 | 🔴 |
| 限流/安全边界 | 🟢 slowapi+白名单+沙箱 | 🟡 | 🟡 | 🟢 | 🟡 | 🟡 | 🟢 | 🔴 |
| 评测/测试套件 | 🟡 smoke+promptfoo | 🔴 | 🟢 | 🟢 | 🟢 | 🟢 | 🔴 | 🟡 |
| 多模态/语音 | 🟡 TTS+STT | 🟡 | 🔴 | 🟡 | 🟡 | 🟡 | 🟢 图片 | 🟢 视觉 |
| 零第三方依赖可审计 | 🟢 核心纯Python | 🟢 | 🟡 | 🟡 | 🟡 | 🟡 | 🔴 | 🟡 |

---

## 三、PocketMind 真实能力盘点（基于代码）

### 已具备的差异化优势

1. **移动端 Agent 独一份**：`agentd/tools/phone/*` 提供 25+ 手机能力（电池/短信/电话/定位/传感器/WiFi/相机/剪贴板/TTS/STT），通过 termux-api 官方通道，无 root。Mobile-Agent 走屏幕 UI 自动化（不同路线），桌面 Agent 全部缺失此维度。
2. **安全设计完整**：
   - 服务端强制审批 `approval.py` + `permissions.py`（chat 模式连工具都不注册）；
   - 文件路径白名单 + 危险操作自动备份/undo（`checkpoints.py`）；
   - shell 沙箱（rlimit 内存/CPU/进程数）+ `bash -c` 减少注入面；
   - SSRF scheme 白名单、API Key 打码、数据目录 0600/0700。
3. **本地优先**：会话/消息/记忆/定时任务/备份全在设备 SQLite，`--lan` 才暴露且强制令牌。
4. **Plan/Act 双模式 + 审批流**：与 Cline 设计对齐，且审批是服务端强制，模型无法绕过。
5. **定时任务 + 配方 + Telegram 远程**：一键配方、条件触发（battery<20）、Telegram 按钮审批，桌面 Agent 普遍缺失。
6. **轻量可审计**：核心约 1 万行 Python，单进程 FastAPI，无重型框架依赖；MCP Server/Client 用官方 SDK。

---

## 四、核心差距与补强路线

### P0｜长程记忆分页（对标 Letta）— 工作量 ~200 行

- **现状**：上下文超 80 条消息时整体裁剪（`agent.py _trim`），早期细节永久丢失；记忆摘要只保留最近一份。
- **目标**：近期原始消息 + 远期滚动摘要，实现"虚拟上下文分页"。
- **做法**：`Store` 增加按会话归档表；每次对话前计算 token 预算，超出时把最旧消息压缩进摘要并落库；上下文重组时注入"摘要 + 近期原始"。

### P1｜Token 级上下文预算与压缩（对标 Cline/Claude Code）— 工作量 ~150 行

- **现状**：`agent_utils.check_token_budget` 只有软/硬停，没有裁剪工具返回。
- **目标**：工具返回结果按 token 预算截断（已有 `MAX_TOOL_TEXT=6000` 硬截断，改为按模型窗口比例 + 摘要），并在预算耗尽时强制触发摘要。

### P1｜MCP Client 实际启用（对标桌面生态）— 工作量 ~80 行 + 文档

- **现状**：`mcp_client.py` 已存在但默认 `enabled=false`，设置面板无入口，工具注册/调度无 UI。
- **目标**：设置面板增加 MCP Client 配置（服务器列表），连接后动态注册 `mcp__<server>__<tool>` 工具并纳入审批。

### P1｜多 Agent 编排（对标 ADK/LangGraph/CrewAI）— 工作量 ~300 行

- **现状**：单 Agent 循环。
- **目标**：增加"子 Agent 工具"——主 Agent 可把任务拆给子 Agent（如"搜索记忆""汇总文件"），子 Agent 复用同一套审批/存储；先实现 `AgentTool`，再考虑图编排。

### P2｜浏览器/UI 自动化 — 工作量 ~200 行 + 依赖

- **现状**：无。
- **目标**：可选接入 Playwright/浏览器 MCP Server，让 Agent 能查网页/操作 Web；对手机 Agent 价值中等。

### P2｜评测闭环（对标 ADK/LangGraph）— 工作量 ~100 行

- **现状**：`eval/promptfooconfig.yaml` 已就绪但未实际运行（需 API Key）。
- **目标**：把 smoke 断言 + promptfoo 用例纳入 CI 门禁（`workflow_dispatch`），并补充"工具选择正确率/审批拦截率"等指标。

### P2｜多模态（图片理解）— 工作量 ~60 行 + 模型支持

- **现状**：只有 TTS/STT。
- **目标**：`take_photo` 后把图片路径转 base64 传给支持视觉的模型（OpenAI 兼容），实现"拍照给我看看这是什么"。

---

## 五、结论

- **在"移动端 + 本地优先 + 安全审批 + 定时/远程"这个组合上，PocketMind 没有直接竞品**；桌面 Agent（Open Interpreter/Cline）和框架（ADK/LangGraph/CrewAI）都缺失手机能力与本地部署闭环。
- **主要短板集中在"深度"而非"广度"**：长程记忆分页、Token 级上下文管理、多 Agent 编排、MCP Client 落地，是向"专业 Agent 平台"进阶必须补的课。
- **建议优先级**：P0 长程记忆分页 → P1 Token 预算 + MCP Client 启用 → P1 子 Agent 编排 → P2 评测闭环/多模态。
- 上述 P0/P1 补强均可在现有架构上增量实现（无需更换框架），且能保持"本地优先 + 服务端强制审批"的核心设计不变。

> 详细工程状态与修复历史见 `POCKETMIND_修复完成报告.md`。