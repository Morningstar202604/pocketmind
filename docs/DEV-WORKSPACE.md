# Dev Workspace：手机开发工作区（Phase 1）

本文档对应 `feat/dev-workspace` 分支的第一版实现。目标不是做「手机 IDE」，而是给 PocketMind 补上开发环境最缺的那一层：**文件树、编辑器、保存前 diff、命令台**，并把它接进现有 Agent 宿主。

## 1. 现在能做什么

启动 agentd 后，底部导航会多出「工作区」页签（仅 Python/Termux 运行时显示；原生 APK 的 Kotlin 引擎暂未实现工作区 API，会自动隐藏）。

- **文件树**：浏览 `AGENT_WORKSPACE`（默认 `~/pocketmind-workspace`），支持进入目录、返回上级、新建文件/目录、重命名、删除。
- **编辑器**：纯文本编辑器，支持 Ctrl/Cmd+S 保存；保存前先调用 `/api/workspace/preview` 显示 unified diff，确认后才写入。
- **命令台**：在指定工作目录执行命令，显示 stdout/stderr/exit code；复用现有 `run_shell` 的超时、进程组回收、输出截断与资源限制。
- **搜索**：当前目录下逐行搜索，跳过 `.git`、`node_modules`、`dist`、`.venv`、缓存目录；最多 200 条。
- **Git 状态**：`/api/workspace/git` 返回当前分支、`git status --porcelain` 与 `git diff --stat`。

## 2. 后端接口

前缀 `/api/workspace`，全部挂在 `check_token` 下（与其它 API 一致）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/info` | 工作区根、可选根、运行时、能力位 |
| GET | `/tree?path=` | 单层目录列表（最多 500 项） |
| GET | `/file?path=` | 读文本文件（上限 1 MB） |
| PUT | `/file` | 写文件（上限 2 MB，支持 `expected_mtime` 冲突检测） |
| POST | `/preview` | 返回 unified diff，不落盘 |
| GET | `/search?q=&path=&case_sensitive=` | 逐行搜索 |
| POST | `/exec` | 在 `cwd` 执行命令，上限 300 秒 |
| POST | `/mkdir` | 新建目录 |
| POST | `/rename` | 重命名 |
| DELETE | `/file?path=` | 删除文件（先备份）；目录只允许删空目录 |
| GET | `/git?cwd=` | Git 分支/状态/diffstat |

### 路径与权限

- **读**：复用 `agentd/tools/files.py` 的白名单（`$HOME`、`~/storage`、`/sdcard`），并拒绝 `.agent`、`.ssh`、`.gnupg` 等凭据目录。
- **写/删/改名**：只允许 `AGENT_WORKSPACE` 内部。即使 Web UI 被 XSS 或误操作，也改不到 `~/.ssh`、配置目录或系统文件。
- **命令台**：先拦明显破坏性命令（`rm -rf /`、`mkfs`、`dd of=/dev/...`、fork bomb、`reboot` 等）；这不是完整沙箱，真正的细粒度权限仍由 Agent 审批与参数级规则负责。
- **冲突检测**：保存时带 `expected_mtime`；文件被其他程序改过会返回 409，让用户刷新后再保存。

## 3. 前端结构

- `agentd/web/src/components/WorkspacePage.tsx`：工作区页面，懒加载，不进入首屏 chunk。
- `agentd/web/src/api.ts`：工作区类型与 API 封装。
- `agentd/web/src/components/BottomTabs.tsx`：新增「工作区」页签；`hidden` 用于原生运行时隐藏。
- `agentd/web/src/App.tsx`：探测 `/api/workspace/info`，可用才显示页签；不可用自动回落到对话。
- `agentd/web/src/styles.css`：工作区样式，手机端单列、桌面双列。

## 4. 如何运行与验证

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

- `ruff check`：工作区后端通过；
- `python3 -m compileall`：通过；
- FastAPI TestClient：`info / tree / file / preview / exec` 全链路通过；`/etc/passwd` 写入、`rm -rf /`、越界 `cwd` 均被拒绝；
- `npx tsc -b`：通过；
- `npx vitest run src/App.test.tsx`：3/3 通过；
- `npm run build`：生成 `WorkspacePage` 独立 chunk（约 10.45 KB，gzip 3.80 KB）。

## 5. 已知边界

- **不是完整 IDE**：没有语法高亮、LSP、补全、调试、测试面板；编辑器是纯 textarea。
- **不是真 PTY**：命令台每次独立执行，`cd`/环境变量不会跨命令保留；下一步用 `pty` + WebSocket + xterm.js。
- **原生 APK 暂不可用**：Kotlin `ApiRouter` 还没有工作区端点；当前会自动隐藏页签。下一阶段要么在原生侧实现 app-private 工作区，要么等 PRoot 内置。
- **PRoot 未接**：目前工作区跑在 Termux 的 Python 运行时里；内置 Debian/Alpine rootfs 是 Phase 2。
- **没有代码索引**：搜索是逐行扫，不是 repo map；大项目会慢，下一步加 ignore/索引/上下文预算。
- **命令台不是沙箱**：它只做明显破坏性命令拦截，不能替代 Android 应用沙箱/PRoot 隔离。

## 6. 下一阶段

1. **CodeMirror 6**：按扩展名加载语言包，保留纯文本回退；替换 textarea。
2. **PTY 终端**：`pty.openpty` + WebSocket + xterm.js，支持 `cd`、交互式命令、Ctrl+C。
3. **代码工具**：`edit_file` patch、`grep`、`glob`、`git diff`、`run_tests`，并把这些工具接进 Agent 审批卡（写文件展示 diff，跑命令展示 cwd/命令/风险）。
4. **PRoot 内置**：首次下载 Alpine/Debian rootfs，`proot` 启动；工作区 API 增加 runtime 适配层。
5. **原生 APK**：在 Kotlin 侧实现 app-private workspace API，或通过 PRoot 提供 Linux 用户态；让「一个 APK」也能打开工作区。
6. **上下文**：repo map、ignore、CJK 友好 token 估算、长对话压缩；修掉 `CHARS_PER_TOKEN = 4` 的偏差。

## 7. 与现有项目的关系

不推翻现有 41 工具 / 审批 / 调度 / MCP / 手机能力；工作区是**新增的一层**，不是换赛道。对外定位仍是：

> 手机里的 Agent 工作站：能写代码、能跑命令、能打电话发短信、每一步有审批、后台常驻。

工作区让人能直接看/改代码；Agent 仍通过原有工具与审批改代码。两条路径共享同一个工作区目录与同一套备份/撤销。
