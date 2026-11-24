# 第三方组件与许可

PocketMind 的 APK 分发包含以下第三方二进制/库。此文件不是法律意见；
发布前请以各上游仓库的 LICENSE 原文为准，并保留本文件与源码链接。

## PRoot

- 组件：`libproot.so`（Termux `proot` 5.1.107.102 aarch64），以及 `libproot-loader.so`
- 许可：GPL-2.0（Termux fork，以上游 LICENSE 为准）
- 源码：<https://github.com/termux/termux-packages/tree/master/packages/proot>
- 上游：<https://proot-me.github.io/>
- 本项目改动：仅用 `patchelf --replace-needed libtalloc.so.2 libtalloc.so` 调整 ELF 依赖名，未修改源码。
- 分发义务：随 APK 提供本说明与对应源码获取方式；如修改 PRoot 源码，需按 GPL 提供修改后的源码。

## libtalloc / libandroid-shmem

- 组件：`libtalloc.so`（Termux `libtalloc` 2.5.0 aarch64）、`libandroid-shmem.so`（Termux `libandroid-shmem` 0.7 aarch64）
- 许可：libtalloc 为 LGPL-3.0-or-later；libandroid-shmem 为 BSD-3-Clause（以上游 LICENSE 为准）
- 源码：
  - <https://github.com/termux/termux-packages/tree/master/packages/libtalloc>
  - <https://github.com/termux/termux-packages/tree/master/packages/libandroid-shmem>

## Alpine minirootfs

- 组件：`app/src/main/assets/proot/alpine-minirootfs.tar.gz`（Alpine 3.20.3 arm64）
- 许可：Alpine 各软件包各自许可（BusyBox GPL-2.0、musl MIT、apk-tools GPL-2.0 等）
- 来源：<https://alpinelinux.org/downloads/>（mini rootfs）
- 说明：rootfs 只在用户首次打开 Linux 终端时解压到应用私有目录；APK 内是压缩包。

## JGit / slf4j

- 组件：JGit 5.13.3、slf4j-nop
- 许可：Eclipse Distribution License 1.0（BSD-3 风格）
- 源码：<https://github.com/eclipse-jgit/jgit>

## AndroidX / Kotlin / OkHttp 等

- 通过 Gradle 依赖引入，许可见各自 POM/AAR 内的 LICENSE（Apache-2.0 等）。
- 依赖树：`cd termux/apk-shell && ./gradlew app:dependencies`

## 本项目代码

- 许可：见仓库根 `LICENSE`。
- 本项目不包含 OpenClaw、Claude Code、Codex CLI 的源码；MCP、子代理、PTY、
  PRoot 集成都为独立实现。

## 源码获取

发布 APK 时请同时提供：

1. 本文件；
2. 仓库地址（含 `libproot.so` 使用的 Termux 包版本与 patchelf 命令）；
3. 上述上游源码链接。

如需完全离线合规，可把 PRoot 源码与 Termux 包构建脚本一并附在 release assets。

## 1.8.0 离线开发工具链（Alpine 包）

`assets/proot/alpine-dev-rootfs.tar.gzip` 内打包了 Alpine 3.20.3 aarch64 的 62 个包，
包括 Node.js 20.15.1（MIT，含 ICU）、npm 10.9.1（Artistic-2.0）、Python 3.12.13
（PSF-2.0）、pip 24.0（MIT）、Git 2.45.4（GPL-2.0）、GCC 13.2.1（GPL-3.0-or-later
with GCC Runtime Library Exception）、make 4.4.1（GPL-3.0-or-later）、musl
（MIT）、BusyBox（GPL-2.0）、OpenSSL（Apache-2.0）、ICU（Unicode License）等。

逐包名称、版本、许可证与安装大小见 rootfs 内
`/usr/share/doc/pocketmind-toolchain-packages.txt`；包内 `/usr/share/licenses` 与
各包 `.PKGINFO` 保留原始许可信息。Alpine 包索引与源码：
<https://dl-cdn.alpinelinux.org/alpine/v3.20/>、<https://git.alpinelinux.org/>。

分发时请保留本文件、包清单与上述源码入口。GPL 组件的源码可从 Alpine `aports`
对应包目录获取（`main/<pkg>/APKBUILD`、`community/<pkg>/APKBUILD`）。
