# API 参考

所有端点由 `agentd/main.py` 的 `create_app()` 定义。除 `/api/health` 与静态资源外，
全部要求 `Authorization: Bearer <server.token>`（token 为空时本机模式不校验）。

跨站防护：带 `Origin` 头的请求必须与 `Host` 同主机（`localhost` / `127.0.0.1` / `::1`
互认），否则 403。curl / 安卓 App / 测试脚本不带 `Origin`，照常放行。

限流：默认 120 次/分钟（`agentd/rate_limit.py`），`/api/health` 与静态资源豁免。

## 健康与自检

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 深度探活：DB 连通性、LLM 配置、Mem0、调度器。无鉴权 |
| GET | `/api/diagnostics` | 环境自检 + 设备能力矩阵（`docs/capability-matrix.md`） |
| GET | `/api/metrics` | Prometheus 文本格式指标 |
| GET | `/api/stats` | 运行统计：`metrics` 快照（请求/工具/LLM tokens）+ LiteLLM / Mem0 状态，不含密钥 |

`/api/health` 返回 `{"ok": bool, "ready": bool, "components": {...}}`；
`ready` 表示模型已配置（`mock` 模式恒为 true）。

## 对话与流式

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 发送消息，返回 SSE 流。同一会话进行中的消息自动排队 |
| POST | `/api/stop` | 停止该会话当前轮次（置 `cancelled` 并取消任务） |

`POST /api/chat` 请求体：`{"message": str, "session_id"?: str}`。
响应为 `text/event-stream`，每帧 `data: <json>`：

| type | 载荷 | 含义 |
|---|---|---|
| `session` | `session_id` | 首帧，新建会话时前端据此绑定 |
| `text` | `text` | 增量正文 |
| `thinking` | `text` | 思考模型/推理模型的思维链增量 |
| `tool_start` | `id`,`name`,`input`,`preview`? | 工具调用开始；文件写/编辑/删除带 diff 预览 |
| `tool_update` | `id`,`name`,`status`,`result`,`undoable` | 工具结果（status: completed/failed/denied） |
| `approval` | `id`,`name`,`summary`,`risk`,`preview`? | 等待用户审批 |
| `plan` | `plan`,`tool_calls` | Plan 模式的计划（不执行工具） |
| `todo` | `todos` | 任务清单变更 |
| `queued` | `message` | 上一轮未结束，本条已排队 |
| `error` | `message` | 错误 |
| `done` | `stop_reason`,`usage`? | 结束（end_turn / plan / queued / unconfigured）。`usage` 为本轮累计 `{prompt_tokens, completion_tokens, total_tokens, estimated?}` |

## 开发工作区

前缀 `/api/workspace`，全部要求 token。Python 路径规则：读走 `$HOME` / `~/storage` / `/sdcard` 白名单；写/删/改名只允许 `AGENT_WORKSPACE` 内部。

原生 APK 的根是应用私有 `filesDir`，`config.json` / `pocketmind.db` / `checkpoints` / `audit` / `workspace_changes.json` / `workspace_tasks` / `.git` 不出现在树里，也不能读写；读上限 200KB、搜索单文件 1MB。原生已实现 `info/tree/file/preview/search/mkdir/rename/delete/exec/changes/undo/tasks/git`：写删会先备份并记一条变更，后台任务可查询、取消、通过 SSE 看增量，Git 用 JGit 纯 Java 实现状态/分支/预合并/提交/中止/撤销/冲突整文件解决；`features.git_blocks=false`（三路块仍是 Python 运行时能力），PTY 仍明确不可用。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workspace/info` | 根目录、运行时、能力位 |
| GET | `/api/workspace/tree?path=` | 单层目录（最多 500） |
| GET | `/api/workspace/file?path=` | 读文本（≤1MB） |
| PUT | `/api/workspace/file` | 写文本（≤2MB，`expected_mtime` 冲突 409，返回 `change_id`） |
| POST | `/api/workspace/preview` | unified diff，不落盘 |
| GET | `/api/workspace/search?q=&path=&case_sensitive=` | 逐行搜索（≤200 条） |
| POST | `/api/workspace/exec` | 同步命令（cwd + timeout≤300s） |
| POST | `/api/workspace/tasks` | 后台任务 `{command,cwd,timeout}`，返回 `id` |
| GET | `/api/workspace/tasks?limit=` | 最近任务 |
| GET | `/api/workspace/tasks/{id}` | 任务状态、输出、日志路径 |
| GET | `/api/workspace/tasks/{id}/stream` | SSE 任务流：运行中推快照，终态发 done；最长挂 300s |
| POST | `/api/workspace/tasks/{id}/cancel` | 取消任务并回收进程组 |
| GET | `/api/workspace/changes?limit=&include_undone=` | 文件变更记录 |
| POST | `/api/workspace/changes/{id}/undo` | 撤销写入/删除/新建；后续修改冲突 409 |
| POST | `/api/workspace/mkdir` / `/rename` | 新建目录 / 重命名 |
| DELETE | `/api/workspace/file?path=` | 删除文件（先备份）或空目录 |
| GET | `/api/workspace/git?cwd=` | 兼容旧接口：原始分支/status/diffstat 文本 |
| GET | `/api/workspace/git/status?cwd=` | 结构化 Git 状态：branch、clean、ahead/behind、merging、files、branches、conflicts |
| POST | `/api/workspace/git/init` | 初始化工作区 Git 仓库并写入 `.gitignore`（隐藏应用私有数据） |
| POST | `/api/workspace/git/checkout` | 切分支 `{cwd,branch,create}`；脏工作区或合并中 409 |
| POST | `/api/workspace/git/commit` | 提交工作区改动 `{cwd,message,all}`；`all=true` 时先暂存当前目录全部改动 |
| POST | `/api/workspace/git/merge/preview` | 进入两阶段合并 `{cwd,branch}`，返回冲突列表、状态与 premerge 检查点 |
| POST | `/api/workspace/git/merge/abort` | 中止合并，恢复合并前状态并清理检查点 |
| POST | `/api/workspace/git/merge/commit` | 提交合并 `{cwd,message}`；返回检查点；还有未解决冲突则 409 |
| GET | `/api/workspace/git/merge/checkpoint` | 查询当前 premerge 检查点 |
| POST | `/api/workspace/git/merge/undo` | 把已提交的双亲合并 reset 回检查点；脏工作区/非合并提交 409 |
| GET | `/api/workspace/git/conflicts?cwd=` | 读取 base/ours/theirs/工作区文本 |
| POST | `/api/workspace/git/conflicts/resolve` | 解决单个冲突：`ours` / `theirs` / `manual`，并 `git add` |
| GET | `/api/workspace/git/conflicts/blocks?cwd=&path=` | 三路冲突块（ours/base/theirs，来自 `git merge-file --diff3`） |
| POST | `/api/workspace/git/conflicts/blocks/resolve` | 按块解决：`choices:[{index,side,content?}]`，全部块都选完才写文件并 `git add` |
| POST | `/api/workspace/patch` | 应用 unified diff；默认 `dry_run=true` 只预检 |
| WS | `/api/workspace/term?cwd=&token=&session=` | PTY；30 秒内带 `session` 重连可接管原 shell |

写文件、删除、重命名、撤销、冲突 resolve 都经过进程内路径锁；默认等待 8 秒，
超时返回 409。保存仍带 `expected_mtime`，跨进程/命令台改动由 mtime 兜底。

WS 首帧为 `{"type":"ready","session_id":...,"reconnected":bool}`，其余为原始终端输出；客户端输入 `{"type":"input","data":...}`、`{"type":"resize","cols":..,"rows":..}`。

## 审批

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/approval` | `{session_id, tool_call_id, decision}`，decision ∈ allow_once / allow_always / allow_global / deny / deny_always |
| GET | `/api/approvals/pending` | 未决审批（含剩余秒数），覆盖后台/定时任务触发 |
| GET | `/api/approvals` | 当前名单：global / deny / sessions |
| DELETE | `/api/approvals?scope=deny\|global\|session:<sid>&tool=` | 清除名单条目 |

超时自动拒绝（默认 120s，`server.approval_timeout` 可配）。

## 会话与消息

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sessions` | 会话列表（含消息数；置顶优先，其次最近活跃） |
| POST | `/api/sessions` | 新建会话 |
| PUT | `/api/sessions/{sid}` | 重命名 / 置顶，body `{title}` 或 `{pinned: bool}`（可同时给） |
| DELETE | `/api/sessions/{sid}` | 删除会话（消息/记忆/清单/归档/定时任务级联） |
| GET | `/api/sessions/{sid}/messages` | 该会话最近 200 条消息 |
| DELETE | `/api/sessions/{sid}/messages/{mid}` | 从该消息起截断（编辑重发 / 重新生成） |
| GET | `/api/sessions/search?q=&limit=` | 跨会话搜索：消息正文 + 工具输出 + 工具调用参数 |
| POST | `/api/sessions/{sid}/compact` | 手动压缩历史，body `{keep?: int}` |
| GET/DELETE | `/api/sessions/{sid}/todos` | 会话任务清单 |
| POST | `/api/undo` | `{session_id, tool_call_id}` 撤销文件写/删 |

## 定时任务与配方

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/jobs` | 列表 / 新建（`name`,`trigger_type`∈cron/interval/date,`expr`,`message`,`condition`?） |
| PUT/DELETE | `/api/jobs/{jid}` | 更新（enabled / 表达式热更新）/ 删除 |
| POST | `/api/jobs/{jid}/run` | 立即执行一次（后台起跑，HTTP 立即返回；条件表达式仍生效） |
| GET | `/api/recipes` | 预设配方列表（含是否已实例化） |
| POST | `/api/recipes/{id}/apply` | 一键实例化（幂等） |

条件仅支持 `battery <op> <number>`（op ∈ `< > <= >= ==`）。

## 记忆 / 知识库 / 技能

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/api/memory/profile` | 档案记忆（USER.md / FACT.md）；PUT 支持 `user` / `facts` / `fact` |
| GET | `/api/memory` | Mem0 记忆列表（未启用返回 `enabled:false`） |
| GET | `/api/memory/search?q=&limit=` | Mem0 语义搜索 |
| DELETE | `/api/memory/{id}` | 删除单条 Mem0 记忆 |
| POST | `/api/memory/reset` | 清空 Mem0 记忆 |
| GET | `/api/kb` | 知识库条目（标题 + 字数） |
| DELETE | `/api/kb/{kid}` | 删除条目 |
| GET | `/api/skills` | 已安装 SKILL.md 技能 |
| GET | `/api/permissions` | Android 运行时权限状态（含是否有前台 Activity） |
| POST | `/api/permissions/request` | 请求一组权限，由前台 Activity 弹系统授权框；无 Activity 返回 409 |

## 工具 / MCP / 设置 / 导出

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tools` | 工具清单（名称/描述/风险/分组/超时） |
| GET | `/api/mcp` | MCP 服务器配置与已连接工具 |
| GET/PUT | `/api/settings` | 读写配置；GET 返回的 api_key / token / tg_token 均为打码值 |
| POST | `/api/llm/test` | 用当前配置发一次最小请求验证连通性 |
| GET | `/api/providers` | 内置厂商预设 |
| GET | `/api/export` | 全量导出（会话/消息/归档/清单/档案/知识库/定时任务） |
| POST | `/api/import` | 从导出文件恢复：合并语义（同 id 会话跳过、知识库去重、任务默认停用），坏文件 400 且不写半截 |

`PUT /api/settings` 的语义：`api_key` / `server.token` / `server.tg_token` 回传打码值
或空串时**不覆盖**已存值，只有新值才写入；数值字段由 pydantic 模型收敛到合法区间。

`GET /api/export` 的 `max_sessions` 上限为 500（`exported_session_count` 给出实际条数，
`export_truncated` 标记是否触顶）。单会话最多导出最近 1000 条消息。

`POST /api/import` 返回 `{"ok": true, "imported": {sessions, sessions_skipped, messages, knowledge, todos, jobs}}`；
单次最多 500 个会话，超过请拆分。

**备份格式版本（1.3 起）**：`GET /api/export` 响应带 `format_version`（当前 `1`）。
导入规则：缺失视为 `1`（历史备份照常导入）；大于当前支持的版本 → `400`，
提示先把本机升级到与备份同版本；非正整数（含布尔值）→ `400`。

**搜索命中来源**：`/api/sessions/search` 的每条命中带 `matched` 字段——
`message`（消息正文）、`tool_output`（工具输出，`role='tool'` 的 content）、
`tool_call`（工具调用参数，assistant 消息 `meta.tool_calls` 的 arguments JSON）。
