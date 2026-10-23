# PocketMind 功能对标主流方案报告

> **历史快照（2026-10-14 之前）**：本文记录的是当时代码的状态与结论，部分内容已被后续修复/新增取代；
> **现状请以 [CHANGELOG.md](CHANGELOG.md) 与代码为准**。保留本文仅供追溯当时的判断依据。

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

## 四、核心差距与补强路线（2026-10-14 状态复核）

> 本节原为"待补"清单；下列状态为逐项复核结果，实现细节见 CHANGELOG.md 的 Unreleased 段。

### 已完成（原列为缺口）

- **长程记忆分页（原 P0）— 已完成**：上下文超窗口时把早期消息滚动归档为摘要
  （`Store.archive_messages` / `memory(kind=archive)`），并在组装时按旧→新注入；
  另有 token 预算触发路径（`context.budget_tokens` × `compress_threshold`）。
- **Token 级工具结果预算（原 P1）— 已完成**：`_tool_result_limited` 按预算 15% 截断工具结果，
  保头保尾；摘要链路补齐直连摘要器（未开 LiteLLM 也可用）。
- **MCP Client 落地（原 P1）— 已完成**：官方 SDK 双端（暴露 38 个工具给桌面 / 接入外部 MCP Server），
  并新增按注解自动分级（readOnlyHint→safe、destructiveHint→danger）。
- **结构化长期记忆 + 用户可编辑（新增项）— 已完成**：USER.md / FACT.md / JOURNAL.jsonl，
  设置面板可编辑，`remember_fact` 工具经审批写入。
- **知识库与技能（新增项）— 已完成**：KB 走显式工具检索不自动注入；Skills 按 SKILL.md 渐进披露。
- **任务清单（原对标 Claude Code 的缺口）— 已完成**：`todo_write` 落库 + SSE + 前端面板。

### 仍开放（有意不做，理由见下）

- **多 Agent 编排**：单用户单机场景价值有限，长任务已由调度器覆盖；维持单 agent。
- **浏览器/UI 自动化（手机屏幕操作）**：与"termux-api 系统能力"路线不同，属另一条产品线。
- **向量记忆默认开启**：chroma/fastembed(onnxruntime) 在 Termux/bionic 上无 wheel，
  维持"可选 + 缺依赖即降级"，默认长期记忆走 FTS5 + RRF；真机可安装性待实测。
- **换用 LangGraph / OpenAI Agents SDK 等框架**：调研结论为不换——其原生依赖（xxhash/ormsgpack/
  sqlite-vec、griffelib）在 Termux 无法落地，且自研循环已覆盖流式/审批/plan/前缀缓存/全量持久化。

## 五、结论

- **在"移动端 + 本地优先 + 安全审批 + 定时/远程"这个组合上，PocketMind 没有直接竞品**；桌面 Agent（Open Interpreter/Cline）和框架（ADK/LangGraph/CrewAI）都缺失手机能力与本地部署闭环。
- **主要短板集中在"深度"而非"广度"**：长程记忆分页、Token 级上下文管理、多 Agent 编排、MCP Client 落地，是向"专业 Agent 平台"进阶必须补的课。
- **建议优先级**：P0 长程记忆分页 → P1 Token 预算 + MCP Client 启用 → P1 子 Agent 编排 → P2 评测闭环/多模态。
- 上述 P0/P1 补强均可在现有架构上增量实现（无需更换框架），且能保持"本地优先 + 服务端强制审批"的核心设计不变。

> 详细工程状态与修复历史见 `POCKETMIND_修复完成报告.md`。