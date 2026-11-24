# 离线开发工具链包（1.8.0）

APK 内的 `assets/proot/alpine-dev-rootfs.tar.gzip` 是 123MB 的 Alpine 3.20
aarch64 开发 rootfs，含 Node 20.15.1、npm 10.9.1、Python 3.12.13、pip 24.0、
Git 2.45.4、GCC 13.2.1、make 4.4.1 及 62 个依赖包。解压后约 372MB。

## 为什么单独下载

- Git 仓库不存 123MB 二进制（也避免四个远程各存一份）。
- 没有这个包时，APK 自动回退到 9MB Alpine minirootfs；功能仍在，但 Node/Python/Git
  需要联网 `apk add`。
- 包已压缩，扩展名用 `.gzip` 而不是 `.gz`，避免 AGP mergeAssets 自动解成 372MB `.tar`。

## 构建前

```bash
bash scripts/fetch-dev-rootfs.sh
```

可用 `POCKETMIND_DEV_ROOTFS_URL` / `POCKETMIND_DEV_ROOTFS_SHA256` 覆盖默认源与校验。
默认源：<https://aka.doubaocdn.com/s/ZDBLBXbgaS>
SHA-256：`062143f386f7f0ab7f039681d7a569533f4d8b750016ebfe6ff41eb5094f181b`

## 包内容校验

```bash
tar tzf termux/apk-shell/app/src/main/assets/proot/alpine-dev-rootfs.tar.gzip | head
# 应看到 ./bin/sh, ./usr/bin/node, ./usr/bin/python3, ./usr/bin/git, ./.pocketmind-dev-env
```

rootfs 里还有 `/usr/share/doc/pocketmind-toolchain-packages.txt`，逐包记录名称、版本、
许可证与安装大小。升级包时同步更新 `THIRD_PARTY_NOTICES.md` 与这个清单。

## 安全边界

- 仍是 app uid 下的 PRoot，不是 root。
- 首次解压需要约 372MB 空闲空间；临时目录会双份占用，建议预留 800MB。
- 包内没有模型、没有第三方 agent CLI；Node/Python 只是运行时。
