#!/usr/bin/env bash
# 下载 1.9.0 的离线 Alpine 开发工具链到 assets。
# 176MB，不入库；普通构建没有它也能跑（自动回退 9MB minirootfs）。
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${POCKETMIND_DEV_ROOTFS_URL:-https://aka.doubaocdn.com/s/OULckASXOI}"
SHA256="${POCKETMIND_DEV_ROOTFS_SHA256:-f4618eedfbe1d597f6a131636e0c3a7cf4af5a2a1966759c09ef8d531c1bb5c9}"
OUT="termux/apk-shell/app/src/main/assets/proot/alpine-dev-rootfs.tar.gzip"
TMP="${OUT}.part"

mkdir -p "$(dirname "$OUT")"
echo "downloading $URL"
curl -fL --retry 3 --connect-timeout 20 -o "$TMP" "$URL"
echo "$SHA256  $TMP" | sha256sum -c -
mv "$TMP" "$OUT"
ls -lh "$OUT"
