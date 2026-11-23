# PocketMind 1.7.0 发布说明（M1.17）

- 版本：`1.7.0` / versionCode `170`
- 提交：`9d82979`（已推 GitCode / Gitee / GitHub 两个账号的 `main`）
- APK：`PocketMind-1.7.0-arm64-debug.apk`，8.94 MB，debug 签名
- SHA-256：`f49262b0b0a2844fb54703c384c7bb4de92483746b276d826cb13d8c77ee122f`
- 安装包下载：<https://aka.doubaocdn.com/s/msbNezhkkl>

本次把 APK 从「Termux/Python 后端的 Web UI」推进到「手机本地 Agent 工作站」：
原生 PTY、PRoot Alpine、Git 远程、MCP HTTP/stdio、只读子代理、审批审计都在
一个 arm64 APK 里；核心功能不需要 Termux。

## 1. 手机里的 Linux 工作站

- **Linux 环境面板**：显示 rootfs 是否就绪、占用空间、安装状态与最近日志。
- **一键安装开发依赖**：minimal（Node/npm/Python3/pip3/Git/curl/bash）或
  full（再加 build-base：gcc/make/headers），在 PRoot 内执行 `apk update && apk add`。
- **真实自检**：启动 proot 后逐项检查 `alpine-release`、uid、`apk`、`node`、`npm`、
  `python3`、`pip3`、`git`，full 还检查 `gcc`、`make`；失败会给出具体项与日志。
- **Linux 终端**：当前工作目录绑定为 guest `/workspace`，可以直接编辑/运行项目；
  与 Android Shell 分开会话，断线 30 秒内可接管。
- **边界**：PRoot 是 app uid 下的 ptrace 模拟，不是 root；`apk add` 只改 rootfs，
  不改宿主 Android。应用根目录开 Linux 终端会看到应用私有配置，面板已提示。

## 2. Git 远程

- `POST /api/workspace/git/clone`：支持 `http(s)` 与 `file:`，目标目录必须为空，
  路径 jail 到工作区；失败会清理半截目录。
- `POST /api/workspace/git/pull`：fetch + pull origin；冲突不吞现场，返回
  `state.merging`，继续走三路冲突块面板。
- Git 面板直接输入仓库地址/目标目录/分支，旁边保留初始化空仓库、分支、提交、
  预合并、ours/theirs/manual 选择与合并撤销。

## 3. MCP：HTTP + stdio

- **HTTP**：Streamable HTTP，支持 JSON 与 SSE 响应、`Mcp-Session-Id`。
- **stdio**：`command` + `args` 经 PRoot 启动，按行 JSON-RPC，进程随刷新/退出回收；
  需要先在「Linux 环境」装 Node 或 Python。`/workspace` 绑定工作区。
- 外部工具统一注册为 `mcp__server__tool`，默认 `danger`，仍走审批。
- 服务端 stdio MCP 与 Telegram 网关仍属 Termux 专属；本 APK 是 MCP **客户端**。

## 4. 只读子代理与审计

- `subagent` 原生工具：独立子会话、只暴露 `list_dir`/`read_file`，headless 写/执行
  一律拒绝，子会话结束即删除；父代理继续收尾。
- 审批审计：`GET /api/approvals/audit` 只读 append-only；工作区「审计」面板显示
  最近 80 条 tool/risk/decision/时间/session，不展示命令参数与文件内容。

## 5. 验证结果

| 门禁 | 结果 |
|---|---|
| 原生 `testDebugUnitTest` | **107 / 107**，0 失败（15 个测试类） |
| 原生 `lintDebug` | 通过 |
| 原生 `assembleDebug` | 通过 |
| 前端 `tsc -b` + `npm test -- --run` + `npm run build` | **82 / 82**，构建通过 |
| 后端 `tests/run_all.sh --backend` | **58 / 58**，全绿 |
| 版本单源 `tests/check_version.py` | 三处一致 `1.7.0` |

APK 内已确认：5 个 arm64 原生库（`libproot.so`、`libproot-loader.so`、`libtalloc.so`、
`libandroid-shmem.so`、`libpty.so`）、`assets/proot/alpine-minirootfs.tar`、
`assets/legal/`、`extractNativeLibs=true`。

## 6. 还没验证的部分（真机）

宿主是无嵌套虚拟化的 KVM guest，AVD 起不来、没有 adb 设备，所以以下只能由用户在
物理 arm64 手机上验证：

1. PRoot 首次解压与 `proot -0` 启动（loader、ptrace、SELinux）。
2. `apk add` 网络与 Node/Python 实际可执行。
3. stdio MCP 服务器在 guest 内启动、工具调用与进程回收。
4. 真实模型下子代理的只读拒绝与父代理收尾。
5. 后台保活、重启恢复、权限弹窗。

按 [`docs/DEVICE-TEST.md`](DEVICE-TEST.md) 从 0 到 6 顺序跑；失败把面板日志、
`adb logcat -s PocketMind:* linker:*` 和相关步骤发回即可定位。

## 7. 安装

```bash
adb install -r PocketMind-1.7.0-arm64-debug.apk
```

或直接把 APK 传到手机点击安装。首次打开完成提供商 → API Key → 权限引导；
需要 Linux 环境时进工作区 →「Linux 环境」→ 安装 minimal。

## 8. 合规

APK 内含 PRoot（GPL-2.0）、libtalloc（LGPL-3.0-or-later）、libandroid-shmem
（BSD-3-Clause）与 Alpine rootfs（各包各自许可）。许可说明与源码链接随
APK 的 `assets/legal/THIRD_PARTY_NOTICES.md` 分发，仓库根也有同名文件。
