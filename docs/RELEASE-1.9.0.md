# PocketMind 1.9.0 发布说明（内置 Qwen Code + MCP 文件系统）

- 版本：`1.9.0` / versionCode `190`
- APK：`PocketMind-1.9.0-arm64-debug.apk`，183 MB（191,803,231 字节），debug 签名
- SHA-256：`28a7b295b18b02adced8e311bd6b0a863c4ecebed14919657f94ae0401c06460`
- 下载：<https://aka.doubaocdn.com/s/hwRCtyNGCR>
- 离线 rootfs 包：176 MB，解压后约 585 MB；建议预留 1.2 GB 空闲空间
- rootfs SHA-256：`04bb8f838cc7b3a2797e017eebdd915ce7efbd524fce808f69449805fe42c9b2`
- rootfs 下载：<https://aka.doubaocdn.com/s/XHqYyWP37u>

1.8.0 把 Node/Python/Git/GCC 装进了 APK，但用户仍然指出：**“你直接运行的
agent CLI 呢？不能装完什么都没有、再让我自己下载。”** 这是对的。1.8 的工具链
只是运行时，Qwen Code、MCP server 这类真正会被执行的程序仍要用户自己 `npm i`。

1.9.0 补的就是这一层：**把会直接运行的 agent CLI 和 MCP server 也塞进 rootfs。**

## APK 里现在有什么

- Alpine 3.21.3 aarch64 rootfs，64 个包，解压后约 585 MB。
- Node.js 22.23.5、npm 11
- Python 3.12.16、pip
- Git 2.47.3
- GCC 14.2、make、musl-dev、headers（`build-base` 全套）
- **Qwen Code CLI 0.28.4**（Apache-2.0），`/usr/bin/qwen` 直接在终端可执行。
- **MCP Filesystem Server 2025.8.21**（MIT），`mcp-server-filesystem` 直接在
  stdio MCP 配置里可用，不需要 `npx`。
- PRoot + JGit + 40 个原生/动态工具（39 个原生静态工具 + `qwen_code`）+
  只读子代理 + HTTP/stdio MCP 客户端。

## 怎么用 Qwen Code

### 终端

打开工作区 → Linux 终端，直接：

```bash
qwen --version
qwen -p "帮我审一下当前仓库的 Kotlin 代码" --approval-mode=plan
```

Qwen Code 读取 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`。PocketMind
的 `qwen_code` 工具会自动把设置页里那组 OpenAI-compatible 配置注入 guest，
不会把 key 写进 rootfs 或日志。

### Agent 工具

模型可以调用 `qwen_code`：

- `prompt`：交给 Qwen Code 的任务。
- `mode=plan`（默认）：只读分析，不允许改文件。
- `mode=write`：`--approval-mode=yolo`，可改 `/workspace`、跑命令；外层
  PocketMind 审批卡必须先批准，批准后 Qwen 才启动。
- `timeout`：默认 600 秒，最大 1800 秒。

工具返回 `output`、`exit_code`、`stderr`、`mode`，不是只给一句“成功”。

## 怎么用内置 MCP 文件系统

设置 → MCP Client → 启用 → 服务器列表：

```text
文件系统 | mcp-server-filesystem | /workspace
```

保存重启后，`read_file` / `write_file` / `list_directory` 等工具会出现在
「MCP 扩展」分组。`/workspace` 是当前工作区在 PRoot 里的绑定路径。

## 行为变化

- 首次打开 Linux 终端：解压 176MB 包到 `filesDir/proot/rootfs`，约 60–180 秒；
  解压后 `bootstrap=done`、profile `prebuilt-full-qwen`。
- 「Linux 环境」面板的“安装”命中预装标记，直接完成，不再 `apk update`。
- 自检新增 `qwen --version`；Node/Python/Git/GCC/make 仍逐项检查。
- 没有大包的旧构建/单元测试继续回退 9MB Alpine minirootfs；Qwen 工具在回退
  rootfs 上会返回 `capability_unavailable`，不会假装可用。
- 预装包没有的软件仍可 `apk add`；`npx` 拉未内置的包仍需要网络。

## 验证

| 门禁 | 结果 |
|---|---|
| 原生单测 | 109 / 109 |
| 原生 lint / assemble | 通过 |
| 前端 tsc / test / build | 82 / 82，构建通过 |
| 后端 tests | 58 / 58 |
| 版本单源 | 1.9.0 / 190 三处一致 |
| APK 静态检查 | 183MB；`assets/proot/alpine-dev-rootfs.tar.gzip` 183.7MB；5 个 arm64 原生库；`assets/legal/` |
| Qwen Code | qemu-aarch64 下 `--version` 输出 0.28.4，`--help` 正常，headless 失败路径退出码 1 |
| MCP Filesystem | qemu-aarch64 下真实 `initialize` JSON-RPC 返回 serverInfo `secure-filesystem-server` |

## 仍未验证

PRoot 在真机上解压 585MB、Qwen Code 真实模型调用、MCP 工具调用、stdio 生命周期，
都只能在你的 arm64 手机上验证。宿主没有嵌套虚拟化，起不了 AVD，也没有 adb 设备。
按 [`docs/DEVICE-TEST.md`](DEVICE-TEST.md) 跑；失败把 Linux 环境面板日志 +
`adb logcat -s PocketMind:* linker:*` 发回。

## 和同行的差距

已补：运行时、工具链、**单 agent CLI、文件系统 MCP server**。
仍未补：多 agent 编排（Codex/Claude Code/OpenClaw 同时跑）、2B–7B 本地模型、
完整 glibc 发行版、LSP/调试、手机端 APK 构建、MCP 服务端托管、deep research。
逐项拆解见 [`docs/COMPETITOR-AUDIT.md`](COMPETITOR-AUDIT.md)。

## 构建说明

176MB 包不入 Git 仓库。构建前运行：

```bash
bash scripts/fetch-dev-rootfs.sh
```

脚本默认从 <https://aka.doubaocdn.com/s/XHqYyWP37u> 下载并校验 SHA-256；
没有包也能构建，APK 会自动回退到 9MB minirootfs。
