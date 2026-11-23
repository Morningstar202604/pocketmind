# Agent Harness 基线

> 这份文档回答一个问题：PocketMind 的 Agent 除了「会聊天、会调工具」之外，
> 运行时该有的基础件是否齐全。对应代码在 `agentd/agent.py`、`agentd/harness.py`
> 与 `agentd/tools/`，回归套件是 `smoke/p40_harness_test.py`。

## 1. 基线矩阵（26 项）

| 能力 | 状态 | 位置 | 说明 |
|---|---|---|---|
| 模型接入 / 流式 / usage | 已有 | `agent.py:_stream`、`litellm_client.py` | OpenAI 兼容直连、LiteLLM、mock 三条路；reasoning 与 usage 单独透出 |
| 模型超时 / 重试 / 故障转移 | 已有 | `agent.py:_llm`、`litellm_client.py:chat` | 直连 SDK `timeout=180`、`max_retries=2`；LiteLLM 按 fallback 列表换模型 |
| System 组装 / 偏好 / 档案 / 技能 | 已有 | `agent.py:_build_system_prefix` | 固定前缀利于提示缓存；记忆/技能失败只降级单项 |
| 工具注册 / JSON Schema / 参数强转 | 已有 | `tools/__init__.py` | pydantic 强转，`additionalProperties:false`，未知工具不静默执行 |
| 工具超时 / 只读重试 / 结构化结果 | 已有 | `tools/__init__.py:call_tool` | 只有 `safe` 工具重试；写/危险操作绝不重试 |
| 工具结果硬顶 | 已有 | `agent.py:_tool_result_limited`、`tools/shell.py` | 单条结果按预算 15% 截断并加头尾摘要；硬顶 60000 字符；shell 单独限长 |
| 审批边界 | 已有 | `approval.py`、`agent.py:_decide_tool` | risk×mode、全局黑名单、fail-closed、危险工具「始终允许」降级 |
| 审批等待超时 | 已有 | `approval.py`、`main.py:_approval_timeout` | 默认 120 秒无响应自动拒绝，宁可不做也不静默放行 |
| ReAct / Plan / 最大链长 | 已有 | `agent.py:_loop` | Plan 不执行工具；链长可配 |
| **单轮墙钟** | **M1.3 新增** | `harness.py:TurnGuard` | 默认 900 秒；防止后台一轮跑一小时 |
| **连续重复调用保护** | **M1.3 新增** | `harness.py:TurnGuard` | 同工具同参数超过阈值即拦截，给模型可恢复错误 |
| **非法工具参数** | **M1.3 新增** | `agent.py:_run_one_tool` | JSON 坏了直接 failed，绝不用 `{}` 执行写/危险工具 |
| 上下文预算 / 摘要 / 裁剪 | 已有 + **硬闸** | `_load_history`、`_trim`、`harness.fit_context` | 先归档再裁剪；发送前按预算做最后硬裁剪，保持 tool 对 |
| 会话持久化 / 中断恢复 / 取消 / 排队 | 已有 | `main.py:run_agent`、`memory.py` | 同会话进行中消息入队；SSE 断开/停止取消任务；重启时孤立 tool_calls 补占位 |
| SSE 心跳 / 长审批不断连 | 已有 | `main.py:sse` | EventSourceResponse `ping=15`，审批等待 120 秒也不会被中间层掐断 |
| 长期记忆 / 检索 | 已有 | `profile_memory.py`、`mem0_memory.py`、`memory.py` | USER/FACT/JOURNAL + FTS5 + 可选 Mem0；RRF 融合 |
| 规划 / todo | 已有 + **读取** | `tools/todo.py` | `todo_write` / `todo_read` / `todo_clear` 按会话落库 |
| 技能目录 / 渐进披露 | 已有 + **原生补齐** | `skills.py`、`SkillRegistry.kt` | `SKILL.md` 只把 name+description 注入 system，正文由 `read_skill` 按需读取；两运行时同契约 |
| 子代理 | 已有 + **原生补齐（只读）** | `subagent.py`、`SubAgentRunner.kt` | 独立上下文、工具收窄、禁止嵌套、审批不豁免；原生版只暴露 `list_dir`/`read_file`，headless 拒绝写/执行，子会话用完即删 |
| 编码工具 | 已有 + **补齐** | `tools/code.py` | 读/写/改/删、grep、glob、git diff/status/log/commit、apply_patch、run_tests |
| 原子写 / 并发写保护 | **M1.3 新增** | `tools/files.py` | 同目录临时文件 + `fsync` + `os.replace`；`expected_mtime` 拒绝旧内容覆盖 |
| 可观测性 | 已有 | `metrics.py`、`telemetry.py` | Prometheus 计数、OTEL span、`/api/stats` |
| 回归门禁 | 已有 + **p40 / HarnessTest / SkillRegistryTest** | `smoke/p40_harness_test.py`、`HarnessTest.kt`、`SkillRegistryTest.kt` | Python 26 项 + Kotlin 8 项覆盖本页新增项与原生技能目录 |

## 2. 新增的运行护栏

### 单轮墙钟

`TurnGuard(max_seconds=...)` 在每一轮 LLM 调用前检查。超过上限时发出
`error` 事件并结束本轮；已经落库的 assistant / tool 消息保留，刷新页面仍能看到
停在哪一步。配置项是 `agent.max_turn_seconds`，默认 900 秒，0 表示不限制。

### 连续重复调用

同一工具、同一组参数（键排序后哈希）连续超过 `agent.repeat_tool_limit`
（默认 3）次，执行前就被拦截，返回：

```json
{"error": "检测到连续 4 次完全相同的工具调用（read_file）...", "error_code": "repeat_tool_call"}
```

这是可恢复错误：模型看到后可以换参数、换工具或直接收尾。它不替代
`max_tool_chain_depth`，而是补上「同一轮里反复撞同一堵墙」的缺口。

### 非法参数

模型偶尔会把 tool arguments 输出成半截 JSON。旧实现解析失败后退回 `{}`，
继续调用工具——对 `write_file` 等于把文件覆盖成空内容。现在解析失败直接：

- `status=failed`
- `error_code=invalid_arguments`
- 不进入审批、不执行 handler
- 错误作为 tool 消息回填，模型下一轮可以修正

### 上下文硬闸

`fit_context(messages, budget_tokens)` 在每次发给模型前运行：

1. 保留第一条 system；
2. 保留最后一条 user；
3. 从旧到新按「assistant(tool_calls) + 所有 tool 响应」整块丢弃；
4. 仍然超预算时，依次丢弃可选 system 记忆块；
5. 连固定 system + 当前问题都放不下则返回 `None`，直接报错停止，绝不把超预算请求发出去。

预算为 0 时不裁剪，保持旧行为。

### 写文件原子性

`write_file` 现在：

- 覆盖前备份，保留 `_ckpt` 撤销凭证；
- 在目标同目录生成临时文件，`fsync` 后 `os.replace`；
- 保留原文件权限位；
- 支持 `expected_mtime`，旧内容不覆盖新改动。

## 3. 编码 Agent 的 Git 工具

M1.2 的 Git 面板是给人用的；这一层补的是**模型自己能用**的 Git 工具：

- `git_status`（safe）：分支、提交、ahead/behind、脏文件、冲突；
- `git_log`（safe）：最近提交，可按路径过滤；
- `git_commit`（danger）：只提交已暂存内容，或显式指定一个 `path`；**不执行 `git add -A`**；
- `apply_patch`（write）：先 `git apply --check`，默认 dry-run，确认后才应用。

`git_commit` 故意做成 danger：提交会改历史，必须让用户看见提交说明和路径。
`apply_patch` 默认 dry-run：模型先预检，再显式 `dry_run=false` 才落盘。

## 4. 已知边界

- 当前只做**整轮**墙钟，不做单工具墙钟之外的 LLM 请求超时覆盖；单工具超时由
  `Tool.timeout` 负责。
- 重复调用保护按「连续」判定，模型隔一次再重复不会被拦；这是为了避免误伤
  正常的轮询式读取。
- 冲突解决仍是整文件级，三栏逐块合并未做。
- 原生 Kotlin 引擎已同步补齐：配置化链长/重试、单轮墙钟、重复调用拦截、
  非法参数拒绝、预算硬闸、auto_compress；`HarnessTest.kt` 覆盖前四项。
  原生逐块冲突 UI 已在 M1.14 补齐；尚未做的是 Python 侧的完整工具集（原生是 39 个手机工具 + 动态 MCP）。
- 这些是能力边界，不是回归；新增项均由 p40 与后端门禁锁定。
