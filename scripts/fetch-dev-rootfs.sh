#!/usr/bin/env bash
# 下载 1.8.0 的离线 Alpine 开发工具链到 assets。
# 123MB，不入库；普通构建没有它也能跑（自动回退 9MB minirootfs）。
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${POCKETMIND_DEV_ROOTFS_URL:-https://aka.doubaocdn.com/s/ZDBLBXbgaS}"
SHA256="${POCKETMIND_DEV_ROOTFS_SHA256:-062143f386f7f0ab7f039681d7a569533f4d8b750016ebfe6ff41eb5094f181b}"
OUT="termux/apk-shell/app/src/main/assets/proot/alpine-dev-rootfs.tar.gzip"
TMP="${OUT}.part"

mkdir -p "$(dirname "$OUT")"
echo "downloading $URL"
curl -fL --retry 3 --connect-timeout 20 -o "$TMP" "$URL"
echo "$SHA256  $TMP" | sha256sum -c -
mv "$TMP" "$OUT"
ls -lh "$OUT"
