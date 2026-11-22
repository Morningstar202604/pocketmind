# PocketMind 手机开发工作站：完整方案

> 目标不是「把桌面 IDE 塞进手机」，而是把 **写代码、跑命令、看 diff、合并改动、中断恢复、检查修改** 这套动作，变成手机上一个常驻 Agent 能参与的工作流。
>
> 本文是总方案；当前已落地 M1.0 + M1.1 + M1.2（核心），见 `docs/DEV-WORKSPACE.md`。

---

## 一、产品定位与边界

### 1.1 一句话定位

**手机里的 Agent 工作站**：底层有 Linux 用户态，中间有代码工作区，上层有会写代码的 Agent；每一步写文件/跑命令都有审批、审计和回滚。

### 1.2 不是什么

- 不是「手机上的 Android Studio」：不做完整 Android App 构建、Gradle 全家桶、模拟器。
- 不是「手机上的 VS Code 复刻」：不做完整 LSP 生态、插件市场、远程开发容器。
- 不是「GUI 手机 Agent」：不靠点屏幕坐标，靠文件系统、shell、Git、包管理器。
- 不是「把手机当服务器」：不承诺 24×7 高负载、Docker 集群、多人协作。

### 1.3 目标场景

- 在手机上改一个小项目/脚本/Web 前端，跑测试，提交 Git。
- 出差/上课/床上没有电脑时，用 Agent 改代码、查日志、发通知。
- 把「写代码 → 跑测试 → 失败发短信 → 定时重跑」串成一条手机上的自动化链路。
- 作为个人作品/竞赛项目：展示「端侧 Agent 运行时 + 安全审批 + 移动开发环境」的组合。

---

## 二、总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ Android 宿主（Kotlin APK）                                    │
│  MainActivity / WebView / 前台服务 / 通知 / 权限 / Termux 桥  │
│  原生 AgentLoop（38 工具）  ←→  本地 HTTP + WebSocket         │
└───────────────┬──────────────────────────────────────────────┘
                │ 本地回环（token + 同源校验）
┌───────────────▼──────────────────────────────────────────────┐
│ React Web UI（同一套代码，PWA / APK WebView 共用）             │
│  对话 / 能力 / 任务 / 工作区                                  │
│  工作区：文件树 · CodeMirror · xterm · diff · Git · 任务面板   │
└───────────────┬──────────────────────────────────────────────┘
                │ /api/workspace/* + /api/workspace/term
┌───────────────▼──────────────────────────────────────────────┐
│ Python agentd（Termux / Linux）                               │
│  FastAPI · Workspace API · PTY · Agent · Approval · Scheduler │
│  MCP Client/Server · SQLite · Checkpoints · Skills · Memory   │
└───────────────┬──────────────────────────────────────────────┘
                │ 可选
┌───────────────▼──────────────────────────────────────────────┐
│ PRoot 用户态（Phase 2）                                       │
│  Alpine / Debian rootfs · bash · git · python · node · npm    │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 三层职责

| 层 | 职责 | 不做什么 |
|---|---|---|
| Android 宿主 | 保活、权限、通知、WebView、Termux 桥、原生工具 | 不跑完整 Linux、不跑 Node/Python 工具链 |
| Python agentd | 工作区 API、PTY、Agent 循环、审批、调度、MCP、数据 | 不直接点屏幕、不替代 Android 系统权限 |
| PRoot 用户态 | 提供 Linux 包管理器与开发工具链 | 不是 VM、不是 root、不是安全边界 |

### 2.2 运行时策略

- **Phase 1（当前）**：Termux 外置。用户装 Termux + PocketMind，Python agentd 在 Termux 里跑；工作区 API 与 PTY 都走 Termux 的 Linux 用户态。
- **Phase 2**：PRoot 内置。APK 首次下载/导入 Alpine 或 Debian rootfs，解压后用 PRoot 启动；工作区 runtime 适配层切换 `termux` / `proot`。
- **Phase 3（可选）**：AVF 虚拟机。只面向支持 AVF 的新设备，不作为大众路径。

---

## 三、四个核心操作：合并、调整、中断、查改

这四类操作是「开发」和「聊天」的分水岭。方案必须把它们定义清楚，不能只做一个文件树。

### 3.1 合并（Merge）

**要解决的问题**：人改的和 Agent 改的撞在一起；两个分支要合并；多个工具输出要合成为一个结论。

**工作流**：

1. **看变更**：`git status --porcelain` + `git diff` + 文件级 diff；工作区顶部显示「已修改 N 个文件」。
2. **选基线**：当前分支、目标分支、base commit；显示 ahead/behind。
3. **预合并**：`git merge --no-commit --no-ff` 或 `git diff base...target`，先在 UI 里看冲突文件。
4. **解冲突**：冲突文件进入三栏视图（ours / base / theirs）；逐块选择「保留我的 / 用对方的 / 两边都要」；保存前再走一次 diff 审批。
5. **提交**：确认后 `git add` + `git commit`；失败则保留工作区，不回滚用户改动。
6. **撤销**：合并前自动 checkpoint；一键 `git merge --abort` 或恢复备份。

**人机并发合并**：

- 文件保存带 `expected_mtime`；Agent 改过同一文件会 409，提示「先看 diff 再保存」。
- Agent 的 `write_file`/`edit_file` 前强制 checkpoint；人可以在「变更」面板逐文件撤销。
- 同一文件禁止两个 Agent 同时写：工作区级文件锁（按路径 hash），拿不到锁就排队/拒绝。

**多工具输出合并**：

- Agent 一轮里多个工具返回的结果，在 UI 里合并成「变更集」：读了哪些文件、改了哪些行、跑了哪些命令。
- 不是简单拼接 JSON，而是按文件/命令分组，冲突的结论标红。

### 3.2 调整（Adjust）

**要解决的问题**：计划变了、模型换了、权限要收紧、工作区要切换、分支要换。

**调整对象**：

| 对象 | 当前 | 目标 |
|---|---|---|
| 计划 | `plan` 模式先出计划再执行 | 支持「暂停 → 改计划 → 继续」；计划与工具调用绑定 |
| 模型 | 豆包/DeepSeek/Qwen/Kimi/智谱/Ollama 预设 | 按任务路由：规划/写代码/总结用不同模型；成本上限 |
| 权限 | `auto` / `approve` / `chat`；五档审批 | 工作区级规则：读自动、写看 diff、危险命令逐条确认 |
| 工作区 | `AGENT_WORKSPACE` 单根 | 多项目切换；每个项目独立 ignore、分支、运行时 |
| 运行时 | Termux / Python | Termux / PRoot 切换；切换前检查依赖与端口 |
| 分支 | 终端里手动 git | UI 里切换/新建分支；切换前检查未提交改动 |

**调整原则**：

- 任何调整都可逆：改模型不改历史；改权限不丢审计；切分支先 stash/checkpoint。
- 调整不影响正在跑的任务：先暂停/排队，再切换；切换后任务标记「需重新确认」。
- 所有调整写审计：谁改的、什么时候、旧值/新值。

### 3.3 中断（Interrupt）

**要解决的问题**：用户点停止、网络断了、App 被切后台、命令跑太久、Agent 卡住、手机被杀进程。

**中断类型**：

| 类型 | 触发 | 行为 |
|---|---|---|
| 用户停止 | 点「停止」/发 `/stop` | 取消当前 Agent run；杀掉工具子进程组；保留已完成的工具结果 |
| 命令超时 | `run_shell` / PTY 超时 | 杀进程组；返回 exit/timeout；日志落盘 |
| 终端断开 | WebSocket 关闭/切后台 | 保留 shell 进程一段时间；重连时恢复输出缓冲；超时后回收 |
| App 被杀 | Android 回收 | 前台服务 + watchdog 尽量保活；重启后从 SQLite 恢复会话/任务状态 |
| 模型断网 | API 超时/限流 | 指数退避；不重复发已成功的工具调用；提示用户 |
| 审批超时 | 120 秒无响应 | 默认拒绝；记录审计；不自动重试危险工具 |

**恢复原则**：

- **会话可恢复**：消息、工具结果、todo、审批记录都在 SQLite；刷新/重启后继续。
- **文件可恢复**：写/删前 checkpoint；恢复时对比 mtime，冲突则让用户选。
- **任务可恢复**：长任务（build/test/dev server）有 task id、日志文件、exit code；重启后标记「中断」，可一键重跑。
- **终端可恢复**：PTY 断开不杀 shell（可配置 30 秒宽限）；重连接管同一 master fd；超时回收。

**当前已做**：Agent `/api/stop`；终端 30 秒宽限与重连接管；命令超时杀进程组；工作区任务 cancel API。
**还缺**：Android 后台被杀后的任务恢复；任务输出实时流式 tail。

### 3.4 查改（Inspect & Modify）

**要解决的问题**：在手机上快速找到代码、看懂、改对、验证、回滚。

**查**：

- 文件树：懒加载、忽略 `.git/node_modules/dist/.venv`。
- 全文搜索：逐行搜索，结果带文件:行号；点击跳到编辑器对应行。
- Git 查询：分支、status、diff、log、blame（后续）。
- 诊断：Python/Node/Java 语法检查、测试输出、端口占用、依赖缺失。
- Agent 审计：哪次工具调用改了哪个文件、审批人、时间、结果。

**改**：

- 编辑器：CodeMirror 6，按扩展名加载语言包；Ctrl/Cmd+S 保存。
- 保存前 diff：unified diff；确认后才落盘；带 `expected_mtime` 冲突检测。
- 补丁：`edit_file` 按行替换，不整文件重写；失败保留原文。
- 测试：`run_tests` 在工作区跑 pytest/npm test；输出结构化；失败通知。
- 回滚：文件 checkpoint；`git checkout -- <file>`；工作区级「撤销本次 Agent 改动」。
- Agent 协作：选中文件 → 「让 Agent 改这个」→ 预填上下文；Agent 的写操作走审批卡。

---

## 四、数据模型

### 4.1 现有可复用

- `sessions` / `messages`：会话与消息。
- `checkpoints`：文件备份与撤销记录。
- `jobs`：定时任务。
- `kb` / `memory`：知识库与长期记忆。

### 4.2 新增（建议）

```sql
-- 工作区项目
workspace_projects(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  root TEXT NOT NULL UNIQUE,
  runtime TEXT NOT NULL,          -- termux | proot | native
  default_branch TEXT,
  ignore_json TEXT,
  created_at REAL, updated_at REAL
)

-- 工作区任务（build/test/dev server/长命令）
workspace_tasks(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  command TEXT NOT NULL,
  cwd TEXT NOT NULL,
  status TEXT NOT NULL,           -- running | done | failed | cancelled | interrupted
  exit_code INTEGER,
  log_path TEXT,
  started_at REAL, ended_at REAL
)

-- 变更集（人/Agent 改动）
workspace_changes(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  path TEXT NOT NULL,
  base_mtime REAL,
  diff TEXT,
  source TEXT NOT NULL,           -- human | agent | merge
  session_id TEXT,
  tool_call_id TEXT,
  checkpoint_id TEXT,
  created_at REAL
)

-- 文件锁（防两个 Agent 同时写）
workspace_locks(
  project_id TEXT NOT NULL,
  path_hash TEXT NOT NULL,
  owner TEXT NOT NULL,
  expires_at REAL,
  PRIMARY KEY(project_id, path_hash)
)
```

### 4.3 状态机

- 任务：`queued → running → done | failed | cancelled | interrupted`。
- 变更：`preview → approved → applied | rejected | conflicted`。
- 合并：`idle → preview → conflict → resolved → committed | aborted`。

---

## 五、接口设计

### 5.1 已实现（Phase 1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workspace/info` | 根、运行时、能力位 |
| GET | `/api/workspace/tree` | 单层目录 |
| GET | `/api/workspace/file` | 读文件 |
| PUT | `/api/workspace/file` | 写文件（mtime 冲突检测） |
| POST | `/api/workspace/preview` | unified diff |
| GET | `/api/workspace/search` | 逐行搜索 |
| POST | `/api/workspace/exec` | 一次性命令 |
| POST | `/api/workspace/mkdir` | 新建目录 |
| POST | `/api/workspace/rename` | 重命名 |
| DELETE | `/api/workspace/file` | 删除（先备份；目录只删空） |
| GET | `/api/workspace/git` | 分支/status/diffstat |
| POST/GET | `/api/workspace/tasks` | 后台任务创建/列表 |
| GET | `/api/workspace/tasks/{id}` | 任务详情/输出 |
| POST | `/api/workspace/tasks/{id}/cancel` | 取消任务 |
| GET | `/api/workspace/changes` | 变更记录 |
| POST | `/api/workspace/changes/{id}/undo` | 撤销变更 |
| WS | `/api/workspace/term` | PTY 终端；`session` 断线重连 |

### 5.2 M1.2 已实现（核心）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workspace/git/status` | 结构化状态：分支/脏文件/ahead/behind/merge/冲突 |
| POST | `/api/workspace/git/checkout` | 切分支；脏工作区或合并中拒绝 |
| POST | `/api/workspace/git/merge/preview` | 两阶段预合并（`--no-commit --no-ff`），返回冲突 |
| POST | `/api/workspace/git/merge/abort` | 中止合并，恢复合并前状态 |
| POST | `/api/workspace/git/merge/commit` | 提交合并；未解决冲突 409 |
| GET | `/api/workspace/git/conflicts` | 读取 base/ours/theirs/工作区文本 |
| POST | `/api/workspace/git/conflicts/resolve` | ours/theirs/manual 解冲突并 `git add` |
| POST | `/api/workspace/patch` | patch 预检/应用 |
| GET | `/api/workspace/info` | capabilities 新增 `locks`、`git_merge` |

### 5.3 待实现（Phase 2+）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/workspace/projects` | 多项目 |
| GET | `/api/workspace/tasks/{id}/log` | 增量日志尾随（当前只有结束快照） |
| POST | `/api/workspace/tests` | 跑测试并结构化输出 |
| GET | `/api/workspace/diagnostics` | 语法/依赖/端口诊断 |
| POST | `/api/workspace/git/conflicts/blocks` | 三栏逐块冲突选择（当前整文件 ours/theirs/manual） |

---

## 六、前端信息架构

### 6.1 工作区页面

```
┌ 工作区 ─────────────────────────────────────────────┐
│ 项目选择 ▾  分支 ▾  运行时 ● Termux   [新建文件]     │
├──────────────┬──────────────────────────────────────┤
│ 文件树       │ 文件标签：hello.py  ●未保存           │
│  src/        │ [还原] [保存] [重命名] [删除]          │
│  hello.py    │ ┌──────────────────────────────────┐ │
│  README.md   │ │ CodeMirror 编辑器                 │ │
│              │ └──────────────────────────────────┘ │
│ 搜索…        │ ┌ 终端 / 命令台 ────────────────────┐ │
│ 结果…        │ │ xterm.js PTY                    │ │
│              │ └──────────────────────────────────┘ │
│              │ [变更] [任务] [Git]                   │
└──────────────┴──────────────────────────────────────┘
```

### 6.2 页面/面板

- **文件树**：目录懒加载；文件图标；大小；隐藏文件开关。
- **编辑器**：CodeMirror 6；语言包按扩展名动态加载；暗色/亮色跟随主题。
- **diff 弹层**：保存前 unified diff；确认/取消；截断提示。
- **终端**：xterm.js + FitAddon；连接状态；重连；`cd`/交互式命令。
- **命令台**：无状态一次性命令；作为 PTY 不可用时的降级。
- **变更面板**：Git status + 文件 diff + 撤销；Agent 改动标 `agent`。
- **任务面板**：build/test/dev server 的日志、exit code、取消、重跑。
- **合并面板**：冲突文件、三栏 ours/base/theirs、逐块选择。
- **查改面板**：搜索命中、诊断、测试结果、审计。

### 6.3 与聊天页的联动

- 聊天里 Agent 改文件 → 「变更」面板出现卡片；点开看 diff。
- 工作区选中文件 → 「让 Agent 改这个」→ 预填路径/上下文；跳到聊天。
- Agent 审批卡 → 显示 diff/命令/cwd；允许/拒绝/始终允许。
- 任务失败 → 通知 + 聊天里插入一条系统消息。

---

## 七、安全模型

| 层 | 措施 |
|---|---|
| 网络 | 默认回环；局域网需 token；WebSocket 同源 + token |
| 路径 | 读白名单（`$HOME`/storage），写只允许 `AGENT_WORKSPACE`；拒绝 `.agent`/`.ssh`/`.gnupg` |
| 命令 | 明显破坏性命令拦截；Agent 工具走审批；PTY 不做沙箱承诺 |
| 审批 | 五档决策；写文件看 diff；跑命令看 cwd/命令/风险；120 秒超时拒绝 |
| 审计 | append-only；谁、何时、什么工具、什么参数、结果、审批人 |
| 回滚 | 写/删前 checkpoint；Git 变更可 `checkout`；合并前 checkpoint |
| 凭据 | API Key/Token 打码；日志脱敏；不把密钥写进工作区 |
| PRoot | 明确不是安全边界；恶意代码仍可访问 Termux 用户权限内的文件 |

---

## 八、里程碑与工期

### M1.0（已完成）

- 工作区 API（info/tree/file/preview/search/exec/mkdir/rename/delete/git）。
- 工作区 UI（文件树、CodeMirror、diff 确认、命令台、搜索、Git 状态）。
- PTY 终端（WebSocket + xterm.js）。
- 路径 jail、mtime 冲突、命令拦截、备份。

### M1.1（已完成）

- `edit_file` patch、`grep`、`glob`、`git diff`、`run_tests` 工具。
- 变更面板 + 撤销；Agent 写文件审批卡显示 diff。
- 任务面板 + cancel API；长任务日志落盘。
- 终端断线宽限/重连接管。

### M1.2（核心已完成）

- Git status / 分支切换 / 预合并 / 冲突 ours/theirs/manual / 提交 / 中止。
- patch 预检与应用；仓库根限制在 AGENT_WORKSPACE 内。
- 进程内文件锁、mtime 双重保护，覆盖人保存、Agent 写/删、撤销、冲突 resolve。
- 还没有三栏逐块冲突 UI；变更集仍按文件一条。

### M2（4–6 周）

- PRoot Alpine/Debian rootfs：首次下载、解压、校验、启动。
- Runtime 适配层：Termux / PRoot 切换。
- 原生 APK：app-private workspace 或 PRoot；一个 APK 打开工作区。
- 包体/内存/发热实测；低端机降级。

### M3（4–6 周）

- repo map / ignore / 文件索引；CJK token 估算修正。
- LSP/补全/诊断（可选，按语言逐个接）。
- 测试运行器、预览服务器、端口管理。
- 上下文压缩、成本上限、模型路由。

### M4（2–4 周）

- 真机矩阵、后台保活、崩溃恢复。
- 签名 release、引导向导、权限向导。
- 文档/演示/CI/分发。

**总工期**：全职 3–5 个月；兼职 6–9 个月。  
**先不做**：iOS/桌面端、完整 Android 构建、Docker、应用商店账号体系、本地大模型优先。

---

## 九、验收标准

### 9.1 功能验收

- 新建项目 → 文件树出现 → 打开文件 → 编辑 → 保存前看到 diff → 确认保存。
- `python3 hello.py` / `npm test` 在终端里跑通；退出码/输出正确。
- 点「停止」能停 Agent；点「重连」能重开 shell；超时命令被杀。
- 改错文件能撤销；删文件能恢复；Git 脏工作区切分支有提示。
- 两个来源改同一文件：mtime 冲突提示；合并面板能选 ours/theirs。
- 手机锁屏 5 分钟后回来：会话/文件/任务状态还在；终端可重连。

### 9.2 性能验收

- 工作区页面首屏 < 2 秒（局域网/本机）。
- 编辑器输入延迟 < 50ms（1000 行文件）。
- 终端连接 < 1 秒；`ls` 输出 < 200ms。
- 内存增量 < 200MB（不含 PRoot rootfs）。
- APK 不因工作区增加超过 5MB（CodeMirror/xterm 走 assets；rootfs 首次下载）。

### 9.3 安全验收

- 未授权请求 401；跨站请求 403；WebSocket 无 token 拒绝。
- 写 `/etc/passwd`、`~/.ssh`、`../escape` 被拒。
- `rm -rf /`、`mkfs`、`dd of=/dev/...` 被拒。
- 审批超时默认拒绝；审计可查；密钥不出现在日志。

---

## 十、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 范围太大，做成半成品 IDE | 用户不用，维护崩 | 守住「Agent 工作站」定位；先闭环再扩功能 |
| CodeMirror/xterm 包体大 | 首屏慢/低端机卡 | 懒加载 + 语言包动态 import；终端独立 chunk |
| PRoot 性能/兼容性 | 构建慢、部分机型失败 | 先 Termux；PRoot 作为可选；低端机降级 |
| Android 后台杀进程 | 长任务中断 | 前台服务 + watchdog + 任务日志 + 可重跑 |
| 命令台/PTY 被当成沙箱 | 用户跑恶意代码 | 明确提示；审批；PRoot 不是安全边界 |
| Git 合并冲突复杂 | 数据丢失 | 合并前 checkpoint；三栏 UI；可 abort |
| 本地模型质量差 | 用户失望 | 外部 API 优先；本地模型只做离线/隐私选项 |
| 许可证 | 分发/商业化风险 | Termux/PRoot GPL 合规；不抄 AGPL 代码 |

---

## 十一、当前代码位置

- 后端：`agentd/routes/workspace.py`（工作区 API + 变更/任务/Git 路由）。
- Git：`agentd/workspace_git.py`；锁：`agentd/workspace_locks.py`。
- PTY：`agentd/workspace_terms.py`；任务：`agentd/workspace_tasks.py`；变更：`agentd/workspace_changes.py`。
- 代码工具：`agentd/tools/code.py`；审批 diff：`agentd/diff_preview.py`。
- 注册：`agentd/main.py`（`/api/workspace/*` + `/api/workspace/term`）。
- 前端：`agentd/web/src/components/WorkspacePage.tsx`、`WorkspaceEditor.tsx`、`WorkspaceTerminal.tsx`。
- API：`agentd/web/src/api.ts`。
- 导航：`BottomTabs.tsx`、`App.tsx`。
- 样式：`styles.css`。
- 文档：`docs/DEV-WORKSPACE.md`（M1.0 + M1.1）、本文（总方案）。

---

## 十二、结论

**先做「能改、能跑、能看 diff、能恢复」的闭环，再做合并/PRoot/上下文。**

这条路线的好处是：每一阶段都能单独演示、单独交付、单独回滚；不会把「手机开发环境」做成一个只能看不能用的壳。
