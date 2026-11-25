# PocketMind 1.9.2 手机可用性修复

- 版本：`1.9.2` / versionCode `192`
- APK：`PocketMind-1.9.2-arm64-debug.apk`，183 MB（191,778,368 字节），debug 签名
- SHA-256：`d5c91d0aaf4eaf644cb9332d27328c9ac055099f01d81672f7778f41816ddff0`
- rootfs：`alpine-dev-rootfs.tar.gzip`，183,752,450 字节
- rootfs URL：<https://aka.doubaocdn.com/s/OULckASXOI>
- rootfs SHA-256：`f4618eedfbe1d597f6a131636e0c3a7cf4af5a2a1966759c09ef8d531c1bb5c9`

1.9.1 解决的是“知道东西在哪”；1.9.2 解决的是“知道在哪但真机上跑不起来”。
这一版没有再堆功能，全部改动都落在安装、解压、终端环境和启动路径上。

## 修了什么

- **`qwen` 不再依赖 npm 符号链接**：旧 rootfs 的 `/usr/bin/qwen` 指向
  `cli-entry.js`，靠 `#!/usr/bin/env node` 启动；PRoot 下这层符号链接和 shebang
  都可能失败。现在 `/usr/bin/qwen` 是普通 shell 启动器，直接
  `exec /usr/bin/node .../cli-entry.js "$@"`。
- **PTY 环境变量覆盖**：`NativePty` 原先把 `PATH`/`HOME` 先写成 Android 宿主值，
  再把 `NativeProot.env` 追加成同名变量。`getenv` 取到的是宿主 `PATH`
  （`/system/bin:...`），所以终端里可能找不到 `/usr/bin/qwen`。现在 `put_env`
  对同名键做替换，`NativeProot.env` 显式给 guest
  `HOME=/root` 和 `/usr/local/bin:/usr/bin:...`；终端输入 `qwen` 才能命中。
- **rootfs 解压后校验**：新增 `validateRootfs()`，检查 `bin/busybox`、`usr/bin/node`、
  `usr/bin/python3.12`、`usr/bin/git`、`usr/bin/qwen`、`mcp-server-filesystem`
  以及 `bin/sh`、`usr/bin/python3` 两个符号链接。失败就回退 minirootfs，
  并保留“缺少哪个文件/哪个链接没建起来”的具体原因，避免用户只看到 127 或黑屏。
- **旧 rootfs 强制失效**：`.pocketmind-rootfs` 标记从 `alpine 3.21.3` 改成带
  `pocketmind toolchain r2` 的内容。从 1.9.1 升级的用户不会继续复用旧包，
  首次进入 Linux 会重新解压一次；之后版本若 rootfs 不变则不会重复解压。
- **Qwen 工作区遮罩**：`qwen_code` 通过 PRoot 绑定时，用空文件/空目录覆盖
  `/workspace` 里的 `config.json`、`pocketmind.db`、`rules.json`、
  `checkpoints`、`audit`、`proot` 等敏感项。Qwen 仍能读写项目文件，
  但读不到宿主 API Key 和 rootfs。
- **启动前释放旧空间**：检测到旧 rootfs 先删除再解压，避免 585MB 旧包和新包
  同时占空间；解压失败仍回退 9MB minirootfs。

## 验证

| 门禁 | 结果 |
|---|---|
| 原生单测 | 109 / 109 |
| 原生 lint / assemble | 通过 |
| 前端 tsc + test + build | 83 / 83，构建通过 |
| 后端 tests | 58 / 58，全绿 |
| 版本单源 | 1.9.2 / 192 三处一致 |
| APK 静态检查 | versionCode 192；APK 内 rootfs SHA 与源文件一致；v2 签名通过 |
| rootfs 内容 | `usr/bin/qwen` 为普通 755 启动器；`bin/sh`、`usr/bin/python3` 为符号链接 |
| 宿主 qemu-aarch64 | Qwen `--version`/`--help`、无 Key 失败路径、Node 22 `node:sqlite`、MCP `initialize` 已通过 |

## 仍未真机验证

宿主没有嵌套虚拟化，也没有 adb 设备，AVD 起不来；qemu 不能替代 PRoot 的
ptrace/路径翻译。以下只能在 arm64 手机上验收：

- 585MB rootfs 首次解压耗时与存储不足路径
- PRoot 启动、`/bin/sh` 符号链接、`qwen` shell 启动器
- `mcp-server-filesystem` 的 stdio 生命周期与工具调用
- Qwen 通过用户配置的 OpenAI-compatible 端点真实调用模型

安装后建议先在「工作区 → Linux 环境」点一次准备，再运行自检；自检通过后
再试 `qwen --version`。如果解压失败，状态里会显示具体缺失项，不会只给一个
无上下文的退出码。
