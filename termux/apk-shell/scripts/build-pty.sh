#!/usr/bin/env bash
# 重新编译原生 PTY（NDK 27）。产物提交到 app/src/main/jniLibs，Gradle 不再依赖 NDK。
set -euo pipefail
ANDROID_HOME="${ANDROID_HOME:-/home/user/android-sdk}"
NDK="${ANDROID_NDK_HOME:-$ANDROID_HOME/ndk/27.0.12077973}"
[ -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang" ] || {
  echo "找不到 NDK 27：$NDK" >&2; exit 1;
}
cd "$(dirname "$0")/.."
BIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
mkdir -p app/src/main/jniLibs/arm64-v8a app/src/main/jniLibs/x86_64
"$BIN/aarch64-linux-android26-clang" -D_GNU_SOURCE -shared -fPIC -O2 -Wall \
  -o app/src/main/jniLibs/arm64-v8a/libpty.so app/src/main/cpp/pty.c
"$BIN/x86_64-linux-android26-clang" -D_GNU_SOURCE -shared -fPIC -O2 -Wall \
  -o app/src/main/jniLibs/x86_64/libpty.so app/src/main/cpp/pty.c
echo "已生成："
ls -lh app/src/main/jniLibs/*/libpty.so
