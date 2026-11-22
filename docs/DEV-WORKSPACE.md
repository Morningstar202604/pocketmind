# Dev Workspace：手机开发工作区（M1.0 + M1.1）

本文档对应 `feat/dev-workspace` 分支的 Phase 1 实现。目标不是做「手机 IDE」，而是给 PocketMind 补上开发环境最缺的那一层：**文件树、编辑器、保存前 diff、终端、代码工具、变更/任务面板**，并把它接进现有 Agent 宿主。完整方案见 [`DEV-WORKSPACE-FULL-PLAN.md`](./DEV-WORKSPACE-FULL-PLAN.md)。

## 1. 现在能做什么

启动 agentd 后，底部导航会多出「工作区」页签（仅 Python/Termux 运行时显示；原生 APK 的 Kotlin 引擎暂未实现工作区 API，会自动隐藏）。

- **文件树**：浏览 `AGENT_WORKSPACE`（默认 `~/pocketmind-workspace`），支持进入目录、返回上级、新建文件/目录、重命名、删除。
- **编辑器**：CodeMirror 6，按扩展名动态加载 JS/TS/Python/JSON/Markdown/HTML/CSS 语言包；支持 Ctrl/Cmd+S 保存；保存前先调用 `/api/workspace/preview` 显示 unified diff，确认后才写入。
- **终端**：xterm.js + WebSocket + 后端 PTY，支持 `cd`、环境变量、交互式命令与 Ctrl+C；**断线 30 秒内点「重连」会接管原 shell**，不是重开一个。
- **命令台**：无状态的一次性命令；「运行」同步等待结果，「后台」创建可取消的工作区任务。
- **变更面板**：保存/新建/删除后落一条变更记录；支持逐文件撤销，且会拒绝覆盖你在保存后又改过的内容。
- **任务面板**：长命令后台执行，显示 running / done / failed / cancelled，可取消；输出与日志路径会保留。
- **搜索**：当前目录下逐行搜索，跳过 `.git`、`node_modules`、`dist`、`.venv`、缓存目录；最多 200 条。
- **Git 状态**：`/api/workspace/git` 返回当前分支、`git status --porcelain` 与 `git diff --stat`。

Agent 侧新增五个代码工具：

| 工具 | 风险 | 作用 |
|---|---|---|
| `edit_file` | write | 按 `old_string → new_string` 精确替换；默认唯一匹配，支持 `replace_all`；写前备份 |
| `grep` | safe | 正则全文搜索，返回文件:行号 |
| `glob` | safe | `**/*.py` 这类模式查文件 |
| `git_diff` | safe | 分支、status、unified diff；无 shell 拼接 |
| `run_tests` | danger | 自动识别 pytest / npm / gradle / go / cargo，也可传自定义命令 |

`write_file` / `edit_file` / `delete_file` 进入审批时会附带 unified diff 预览，用户点「允许」前能看到改了什么。

## 2. 后端接口

前缀 `/api/workspace`，全部挂在 `check_token` 下（与其它 API 一致）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/info` | 工作区根、可选根、运行时、能力位 |
| GET | `/tree?path=` | 单层目录列表（最多 500 项） |
| GET | `/file?path=` | 读文本文件（上限 1 MB） |
| PUT | `/file` | 写文件（上限 2 MB，支持 `expected_mtime` 冲突检测，返回 `change_id`） |
| POST | `/preview` | 返回 unified diff，不落盘 |
| GET | `/search?q=&path=&case_sensitive=` | 逐行搜索 |
| POST | `/exec` | 在 `cwd` 执行命令，同步等待，上限 300 秒 |
| POST | `/tasks` | 创建后台任务，立即返回 `id` |
| GET | `/tasks?limit=` | 最近任务列表 |
| GET | `/tasks/{id}` | 单个任务状态、stdout/stderr、日志路径 |
| POST | `/tasks/{id}/cancel` | 取消任务，回收进程组 |
| GET | `/changes?limit=&include_undone=` | 最近写/删/新建变更 |
| POST | `/changes/{id}/undo` | 撤销一条变更；后续修改冲突返回 409 |
| POST | `/mkdir` | 新建目录 |
| POST | `/rename` | 重命名 |
| DELETE | `/file?path=` | 删除文件（先备份）；目录只允许删空目录 |
| GET | `/git?cwd=` | Git 分支/状态/diffstat |
| WS | `/term?cwd=&token=&session=` | PTY 终端；`session` 用于断线重连接管 |

### 路径与权限

- **读**：复用 `agentd/tools/files.py` 的白名单（`$HOME`、`~/storage`、`/sdcard`），并拒绝 `.agent`、`.ssh`、`.gnupg` 等凭据目录。
- **写/删/改名**：只允许 `AGENT_WORKSPACE` 内部。即使 Web UI 被 XSS 或误操作，也改不到 `~/.ssh`、配置目录或系统文件。
- **命令台/后台任务**：先拦明显破坏性命令（`rm -rf /`、`mkfs`、`dd of=/dev/...`、fork bomb、`reboot` 等）；这不是完整沙箱。
- **冲突检测**：保存时带 `expected_mtime`；文件被其他程序改过会返回 409，让用户刷新后再保存。
- **撤销冲突**：变更记录带写入后的 mtime/size；再次修改后撤销会拒绝，避免把新改动覆盖掉。

## 3. 前端结构

- `agentd/web/src/components/WorkspacePage.tsx`：工作区页面；文件树、编辑器、终端、变更/任务面板。
- `agentd/web/src/components/WorkspaceEditor.tsx`：CodeMirror 6 编辑器；语言包动态 import。
- `agentd/web/src/components/WorkspaceTerminal.tsx`：xterm.js 终端；维护 `session_id`，断线重连时带回。
- `agentd/web/src/api.ts`：工作区类型与 API 封装。
- `agentd/web/src/components/BottomTabs.tsx`：新增「工作区」页签；`hidden` 用于原生运行时隐藏。
- `agentd/web/src/App.tsx`：探测 `/api/workspace/info`，可用才显示页签；不可用自动回落到对话。
- `agentd/web/src/styles.css`：工作区样式，手机端单列、桌面双列。

## 4. 后端模块

- `agentd/routes/workspace.py`：HTTP API、路径 jail、命令拦截、变更/任务路由。
- `agentd/workspace_terms.py`：PTY 会话池；30 秒宽限、滚动缓冲、重连接管。
- `agentd/workspace_tasks.py`：后台任务管理；取消时回收进程组，日志落 `$AGENT_HOME/workspace_tasks/`。
- `agentd/workspace_changes.py`：变更记录与撤销；`$AGENT_HOME/workspace_changes.json`，0600。
- `agentd/tools/code.py`：Agent 的 edit_file / grep / glob / git_diff / run_tests。
- `agentd/diff_preview.py`：审批卡里的文件 diff 预览。

## 5. 如何运行与验证

```bash
# 1) 构建前端
cd agentd/web
npm install
npm run build

# 2) 启动后端（mock 模式不调真实模型）
cd ../..
AGENT_HOME=/tmp/pm-home AGENT_WORKSPACE=/tmp/pm-ws \
  python3 -m agentd.main --mock --port 8787

# 3) 打开 http://127.0.0.1:8787 → 底部「工作区」
```

已做的验证：

- `ruff check`：工作区后端、代码工具、终端/任务/变更模块通过；
- `python3 -m compileall`：通过；
- `smoke/p37_workspace_test.py`：13/13；覆盖 info/tree/file/preview/exec、路径越界、危险命令、PTY echo；
- `smoke/p38_m11_test.py`：21/21；覆盖 edit_file/grep/glob/git_diff/run_tests、变更撤销、后台任务取消、PTY 重连接管；
- 后端全量门禁：53 项通过，唯一失败仍是 mypy 2.4.1 与锁文件 1.20.2 的版本漂移（旧问题）；
- `npx tsc -b`：通过；
- `npm test`：15 个测试文件、81/81 通过；
- `npm run build`：`WorkspacePage` 约 422.59 KB（gzip 137.77 KB），`WorkspaceTerminal` 约 339.01 KB（gzip 86.74 KB）；两者都不进首屏。

## 6. 已知边界

- **不是完整 IDE**：有语法高亮，但没有 LSP、补全、调试、测试结果面板。
- **PTY 不是沙箱**：终端里可以跑任意命令；它只做 shell 隔离，不是安全边界。
- **原生 APK 暂不可用**：Kotlin `ApiRouter` 还没有工作区端点；当前会自动隐藏页签。下一阶段要么在原生侧实现 app-private 工作区，要么等 PRoot 内置。
- **PRoot 未接**：目前工作区跑在 Termux 的 Python 运行时里；内置 Debian/Alpine rootfs 是 Phase 2。
- **任务输出不是流式**：任务结束才一次性返回 stdout/stderr；日志文件已落盘，真正的实时 tail 留到下一阶段。
- **任务只在内存里排队**：进程重启后任务状态不恢复；日志文件仍在 `workspace_tasks/`，可人工查看。
- **没有代码索引**：搜索是逐行扫，不是 repo map；大项目会慢，下一步加 ignore/索引/上下文预算。
- **命令台不是沙箱**：它只做明显破坏性命令拦截，不能替代 Android 应用沙箱/PRoot 隔离。

## 7. 下一阶段

1. **合并**：分支切换、预合并、三栏冲突解决、patch 应用、合并前 checkpoint、文件锁。
2. **调整**：计划暂停/改计划/继续；模型按任务路由与成本上限；工作区级权限规则；Termux/PRoot 运行时切换。
3. **PRoot 内置**：首次下载 Alpine/Debian rootfs，`proot` 启动；工作区 API 增加 runtime 适配层。
4. **原生 APK**：在 Kotlin 侧实现 app-private workspace API，或通过 PRoot 提供 Linux 用户态；让「一个 APK」也能打开工作区。
5. **上下文**：repo map、ignore、CJK 友好 token 估算、长对话压缩；修掉 `CHARS_PER_TOKEN = 4` 的偏差。
6. **安全 P0**：`auto` 模式下全局拒绝名单失效；危险工具「始终允许」只记工具名。审批边界要在这两项修完后才算真正成立。

## 8. 与现有项目的关系

不推翻现有 44 工具 / 审批 / 调度 / MCP / 手机能力；工作区是**新增的一层**，不是换赛道。对外定位仍是：

> 手机里的 Agent 工作站：能写代码、能跑命令、能打电话发短信、每一步有审批、后台常驻。

工作区让人能直接看/改代码；Agent 仍通过原有工具与审批改代码。两条路径共享同一个工作区目录与同一套备份/撤销。
