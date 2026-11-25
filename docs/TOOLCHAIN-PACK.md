# 离线开发工具链包（1.9.0）

APK 内的 `assets/proot/alpine-dev-rootfs.tar.gzip` 是 176MB 的 Alpine 3.21.3
aarch64 开发 rootfs，含 Node 22.23.5、npm 11、Python 3.12.16、pip、Git 2.47.3、
GCC 14.2、make 4.4.1 及 64 个依赖包。解压后约 585MB。

它不是“只装运行时”：rootfs 里还有两个会直接执行的程序：

- **Qwen Code CLI 0.28.4**（`/usr/bin/qwen`，Apache-2.0）
- **MCP Filesystem Server 2025.8.21**（`/usr/local/bin/mcp-server-filesystem`，MIT）

所以用户在终端里能直接 `qwen`，在 MCP 配置里能直接写
`mcp-server-filesystem | /workspace`，不需要再 `npm i` / `npx` 下载。

## 为什么单独下载

- Git 仓库不存 176MB 二进制（也避免四个远程各存一份）。
- 没有这个包时，APK 自动回退到 9MB Alpine 3.21 minirootfs；基础 Linux 仍在，
  但 Node/Python/Git 需要联网 `apk add`，Qwen/MCP 会明确返回不可用。
- 包已压缩，扩展名用 `.gzip` 而不是 `.gz`，避免 AGP mergeAssets 自动解成 585MB `.tar`。

## 构建前

```bash
bash scripts/fetch-dev-rootfs.sh
```

可用 `POCKETMIND_DEV_ROOTFS_URL` / `POCKETMIND_DEV_ROOTFS_SHA256` 覆盖默认源与校验。
默认源：<https://aka.doubaocdn.com/s/XHqYyWP37u>
SHA-256：`04bb8f838cc7b3a2797e017eebdd915ce7efbd524fce808f69449805fe42c9b2`

## 包内容校验

```bash
tar tzf termux/apk-shell/app/src/main/assets/proot/alpine-dev-rootfs.tar.gzip | head
# 应看到 ./bin/sh, ./usr/bin/node, ./usr/bin/python3, ./usr/bin/git,
# ./usr/bin/qwen, ./usr/local/bin/mcp-server-filesystem, ./.pocketmind-dev-env
```

rootfs 里还有 `/usr/share/doc/pocketmind-toolchain-packages.txt`，逐包记录名称、版本、
许可证与安装大小。升级包时同步更新 `THIRD_PARTY_NOTICES.md` 与这个清单。

## Qwen Code 的配置边界

- 不预置 API Key；`qwen_code` 工具从 PocketMind 设置里的 OpenAI-compatible
  `base_url` / `api_key` / `model` 注入进程环境。
- 终端里手动跑 `qwen` 时，用户自己 export 同名环境变量。
- Qwen 的 `--approval-mode=plan` 只读；`mode=write` 才用 `yolo`，且外层
  PocketMind 审批先批准。
- Qwen 的会话配置写在 guest `/root/.qwen`，随 rootfs 持久化，不落到宿主存储。

## 安全边界

- 仍是 app uid 下的 PRoot，不是 root。
- 首次解压需要约 585MB 空闲空间；临时目录会双份占用，建议预留 1.2GB。
- 包内有 Node/Python/Qwen CLI/MCP server，但没有模型权重、没有 API Key。
  模型请求仍走用户自己配置的远端 OpenAI-compatible 端点。
- `npx` 仍可用于未内置的包，但那一步会联网；内置的 Qwen 与 filesystem server
  不需要。
