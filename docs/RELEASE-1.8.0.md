# PocketMind 1.8.0 发布说明（离线开发工具链）

- 版本：`1.8.0` / versionCode `180`
- APK：`PocketMind-1.8.0-arm64-debug.apk`，131 MB，debug 签名
- SHA-256：`088ddc5d1659ba4a02d68bbbeb6a3dc2c4c857dae133897fd39b4ddd855826e7`
- 下载：<https://aka.doubaocdn.com/s/XYGVpv7HKE>
- 首次解压：约 372MB；建议预留 800MB 空闲空间

1.7.0 被质疑“9MB，比同行小太多”。我拆了同行：AnyClaw 850.53MB 里装的是
OpenClaw/Codex/Claw Code 三套 agent 和完整 Linux；OpenClaw standalone 是
27.7MB APK + 300MB 首次下载；CodeTalk 的大头是 2B-7B 本地模型。PocketMind
原来的 9MB 确实只是 Alpine 空壳，Node/Python/Git 要联网装——这是真短板。

1.8.0 把这块补了：**123MB 离线开发工具链直接进 APK**，首次解压即可用，不联网
也能跑 Node、Python、Git 和 C 编译。

## APK 里现在有什么

- Alpine 3.20.3 aarch64 rootfs，62 个包，解压后 ~372MB。
- Node.js 20.15.1、npm 10.9.1
- Python 3.12.13、pip 24.0
- Git 2.45.4
- GCC 13.2.1、make 4.4.1、musl-dev、headers（`build-base` 全套）
- BusyBox、curl、bash、OpenSSL 3.3、ICU、sqlite、readline 等依赖
- PRoot + JGit + 39 个原生工具 + 只读子代理 + HTTP/stdio MCP 客户端
- 审批审计、Git 克隆/拉取、PTY、变更集撤销

## 行为变化

- 首次打开 Linux 终端：解压 123MB 包到 `filesDir/proot/rootfs`，约 30–120 秒；
  解压后 `bootstrap=done`、profile `prebuilt-full`。
- 「Linux 环境」面板的“安装”会命中预装标记，直接完成，不再 `apk update`。
- 仍保留 minimal/full 联网补包路径；预装包没有的软件可以继续 `apk add`。
- 没有大包的旧构建/单元测试继续回退 9MB Alpine minirootfs，不会 404。
- 首次解压临时目录会双份占用，存储紧张时先清空间。

## 验证

| 门禁 | 结果 |
|---|---|
| 原生单测 | 107 / 107 |
| 原生 lint / assemble | 通过 |
| 前端 tsc / test / build | 82 / 82，构建通过 |
| 后端 tests | 58 / 58 |
| 版本单源 | 1.8.0 / 180 三处一致 |
| APK 静态检查 | 131MB；`assets/proot/alpine-dev-rootfs.tar.gzip` 128.7MB；5 个 arm64 原生库；`assets/legal/` |

## 仍未验证

PRoot 在真机上解压 372MB、Node/Python/Git/gcc 实际执行、stdio MCP 和
子代理真实模型调用，都只能在你的 arm64 手机上验证。宿主没有嵌套虚拟化，
起不了 AVD，也没有 adb 设备。按 [`docs/DEVICE-TEST.md`](DEVICE-TEST.md) 跑；
失败把 Linux 环境面板日志 + `adb logcat -s PocketMind:* linker:*` 发回。

## 和同行的差距

已补：运行时/工具链。
仍未补：多 agent 运行时（Codex/Claude Code/OpenClaw）、2B-7B 本地模型、
完整 glibc 发行版、LSP/调试、手机端 APK 构建、MCP 服务端托管、deep research。
逐项拆解见 [`docs/COMPETITOR-AUDIT.md`](COMPETITOR-AUDIT.md)。

## 构建说明

123MB 包不入 Git 仓库。构建前运行：

```bash
bash scripts/fetch-dev-rootfs.sh
```

脚本默认从 <https://aka.doubaocdn.com/s/ZDBLBXbgaS> 下载并校验 SHA-256；
没有包也能构建，APK 会自动回退到 9MB minirootfs。
