# PocketMind 架构设计

> 本地优先的智能体服务：LLM 住云端，身体住手机。

## 1. 定位

PocketMind 是一个运行在 Android Termux（或 Docker/VPS）上的**本地 AI Agent 后端**。
它把任意 OpenAI 兼容 LLM 与手机硬件能力（短信、定位、传感器、拍照、Shell）桥接起来，
数据**永不离开设备**，全部 SQLite 本地持久化。

## 2. 分层

```
┌─────────────────────────────────────────────────────┐
│  客户端   React 18 + Vite + Zustand（PWA）           │
│          Telegram Bot（可选）                         │
│          外部 MCP 客户端（Cline / Claude Desktop）    │
└────────────────────────┬────────────────────────────┘
                         │ SSE / REST (HTTP)
┌────────────────────────┴────────────────────────────┐
│  FastAPI 网关  agentd/main.py                        │
│   · Bearer Token 鉴权                               │
│   · ApprovalCenter（审批中心，asyncio.Event）        │
│   · SSE 帧广播（sse-starlette）                      │
│   · MCP Server 注册、Recipe 加载                     │
├─────────────────────────────────────────────────────┤
│  Agent 循环  agentd/agent.py                         │
│   · 单进程 ReAct：stream → tool_call → approve → 回填│
│   · 链长封顶 agent.max_tool_chain_depth（默认 20）   │
│   · 上下文管理（摘要 + FTS5 记忆搜索 + 历史裁剪）     │
├─────────────────────────────────────────────────────┤
│  工具层   agentd/tools/                              │
│   · phone/ 子包（system/comm/sensors/network/media） │
│   · files（CRUD + checkpoint 自动备份）              │
│   · shell（danger 审批）                             │
│   · voice（离线 TTS）                                │
│   · mcp__<server>__<tool>（动态注册第三方）           │
│   · pydantic 动态参数校验                            │
├─────────────────────────────────────────────────────┤
│  横向能力                                             │
│   · Scheduler（APScheduler + reschedule_job）        │
│   · CheckpointStore（SQLite + undo）                 │
│   · Store（SQLAlchemy 异步 + FTS5 全文索引）         │
│   · MCP Client/Server（官方 SDK）                    │
│   · notify（系统通知）                               │
└─────────────────────────────────────────────────────┘
```

## 3. 关键设计决策

### 为什么自己写 Agent 循环而非 LangGraph？

当前选择手写循环的原因：**零额外依赖、单文件可审计、上下文长度可控**。
手写循环约 200 行，覆盖 tool_call / 审批 / 历史裁剪 / 异常处理。
标注为 **未来可迁移到 LangGraph**（获得 checkpoint/HITL 开箱即用）。

为什么没迁移？LangGraph 引入额外的抽象层（StateGraph、Checkpointer、Pydantic State），
对于当前「单用户、单进程、本地 SQLite」场景，迁移收益不高，成本显著。

### 为什么 MCP 官方 SDK 而不再手写？

早期有一个手写 JSON-RPC MCP demo server（已删除）。现在全部走官方 SDK：
`agentd/mcp.py` 是 stdio client，`agentd/mcp_server.py` 是反向暴露手机工具。

### 定时任务如何热更新？

`SchedulerService.update()` 在 expr/trigger_type 变更时走 APScheduler 的
`reschedule_job()` 重建 trigger，job/kwargs 内存同步更新。**无需删了重建**。

### 审批机制如何工作？

每条 `write`/`danger` 工具调用会创建一个 `asyncio.Event`（gate），前端通过 SSE 收到
审批卡片；用户点「允许一次 / 始终允许 / 全局允许 / 拒绝 / 始终拒绝」后 decide 通知 gate 继续。
120s 超时自动拒绝（安全优先）。

三级权限粒度（对标 goose 的 AlwaysAllow / AskBefore / NeverAllow）：

- **全局黑名单（deny_always）**：工具名粒度、跨会话生效、**优先级最高**——命中即短路拒绝，
  既不建等待门也不推审批卡（拒绝是收敛方向，故默认全局）；可在 设置 → 审批权限名单 撤销。
- **全局白名单（allow_global）**：仅由用户在审批卡上显式选择「全局允许」写入，
  会话级决策不会隐式升级为全局（避免一次误点永久放行）。
- **会话白名单（allow_always）**：`(会话, 工具)` 粒度，单会话内免重复确认。

## 4. 数据流

```
用户输入 → POST /api/chat → 中断恢复（孤立 tool_calls 补占位）
        → add_message → 组装上下文（固定前缀 → 偏好 → 档案记忆 → 技能索引
                                    → RRF 检索 → 分页摘要 → 历史）
        → Agent.chat() → OpenAI stream → tool_call →
          ├─ safe → 直接执行（瞬时失败按 max_tool_retries 重试）
          └─ write/danger → ApprovalCenter.ask() → SSE emit approval → 等用户
        → 工具结果回写 SSE + add_message → 循环下一轮（max_tool_chain_depth 封顶）
        → done → 滚动更新每会话记忆摘要（LLM 摘要器；预算超阈值先归档）
```

## 5. 文件地图

```
agentd/
├── main.py          FastAPI 入口 + 路由 + ApprovalCenter 注入
├── agent.py         ReAct 循环（~200 行核心；链长/重试/压缩全部配置驱动）
├── approval.py      审批中心（三级：会话白名单 / 全局白名单 / 全局黑名单）
├── config.py        pydantic 类型强制配置（旋钮全部有真实消费方）
├── memory.py        SQLAlchemy Store + FTS5（记忆 / 任务清单 / 知识库三组表）
├── profile_memory.py 结构化长期记忆（USER.md / FACT.md / JOURNAL.jsonl）
├── skills.py        SKILL.md 渐进披露（name/description 进 system，正文按需读）
├── summarizer.py    会话摘要器（直连 OpenAI 兼容端点，不依赖 LiteLLM）
├── scheduler.py     APScheduler 封装 + 热更新
├── checkpoints.py   文件 undo 日志
├── notify.py        系统通知
├── mcp.py           第三方 MCP Server 客户端（注解 → 风险分级）
├── mcp_server.py    反向暴露手机工具为 MCP（含运行期工具补注册）
├── mock_llm.py      离线 Mock LLM（流式 + 正则匹配）
└── tools/
    ├── __init__.py  Tool 注册表 + pydantic 动态校验 + safe 工具重试
    ├── ctx.py       工具执行上下文（会话/存储/推流回调，contextvars）
    ├── todo.py      任务清单工具（todo_write / todo_clear）
    ├── knowledge.py 知识库工具（kb_add / kb_search / kb_list，显式检索）
    ├── phone/       手机能力（5 子包 + _termux.py）
    ├── files.py     文件系统 CRUD
    ├── shell.py     bash 执行（强制审批）
    └── voice.py     离线 TTS
```

## 6. 安全清单

| 威胁 | 防护 |
|---|---|
| 未授权访问 | 默认绑 127.0.0.1；`--lan` 强制 Bearer Token |
| API Key 泄漏 | mask_key 打码；文件权限 600 |
| 路径穿越 | 白名单 $HOME + ~/storage + /sdcard |
| SSRF | URL scheme 白名单 http/https |
| 文件覆盖丢失 | CheckpointStore 自动备份 + 一键 undo |
| 无限 shell | `run_shell` 强制审批；超时 120s |
| 敏感数据残留 | 会话删除级联清理（checkpoint + scheduler + approval） |
