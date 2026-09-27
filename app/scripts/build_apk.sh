#!/usr/bin/env bash
# ⚠ 已废弃（legacy）：本脚本构建的是旧版 goose 架构的 Flutter APK
#   （连接 ws://127.0.0.1:3284/acp）。当前新版后端为 Python agentd（SSE/8787），
#   与此 APK 不兼容。推荐使用网页版：bash termux/start.sh
#
# 在 PC 上构建安卓 APK（旧版 Termux Agent 前端）
# 依赖：自动安装 JDK + Android SDK + Flutter
# 用法：
#   bash build_apk.sh          # 构建 debug APK（可直接 adb install）
#   bash build_apk.sh release  # 构建 release APK（自动签名，debug keystore 兜底）
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
MODE="${1:-debug}"

# 1. 安装 Java (JDK 17)
if ! command -v java >/dev/null 2>&1; then
  echo "==> 安装 OpenJDK 17..."
  apt-get install -y openjdk-17-jdk
fi

# 2. 安装 Android SDK cmdline-tools
SDK_ROOT="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/sdk}}"
if [ ! -d "$SDK_ROOT/platforms" ]; then
  echo "==> 安装 Android cmdline-tools..."
  CMDLINE="$SDK_ROOT/cmdline-tools"
  mkdir -p "$CMDLINE"
  curl -fsSL -o /tmp/cmdtools.zip \
    https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip
  cd /tmp && unzip -q -o cmdtools.zip -d "$CMDLINE"
  mv "$CMDLINE/cmdline-tools" "$CMDLINE/latest"
fi

export ANDROID_HOME="$SDK_ROOT"
export ANDROID_SDK_ROOT="$SDK_ROOT"
if [ -d "$SDK_ROOT/cmdline-tools/latest/bin" ]; then
  export PATH="$SDK_ROOT/cmdline-tools/latest/bin:$PATH"
fi

# 接受许可并装组件（无人值守）
yes | sdkmanager --licenses >/dev/null 2>&1 || true
sdkmanager "platforms;android-34" "build-tools;34.0.0" "platform-tools"

# 3. 安装 Flutter
if ! command -v flutter >/dev/null 2>&1; then
  echo "==> 安装 Flutter SDK..."
  git clone --depth 1 -b stable https://github.com/flutter/flutter.git "$HOME/flutter"
  export PATH="$HOME/flutter/bin:$PATH"
fi
flutter config --no-analytics >/dev/null 2>&1 || true
flutter precache --android >/dev/null 2>&1 || true

# 4. 低内存 gradle 参数
cat > "$APP_DIR/android/gradle.properties" <<'EOF'
org.gradle.jvmargs=-Xmx2g -XX:MaxMetaspaceSize=512m
org.gradle.parallel=false
org.gradle.caching=false
android.enableJetifier=false
android.useAndroidX=true
kotlin.daemon.jvmargs=-Xmx1g
EOF

cd "$APP_DIR"
flutter pub get

if [ "$MODE" = "release" ]; then
  # 生成/复用 debug keystore 作为兜底签名（真正的分发 keystore 请用户自备）
  KEYSTORE="${KEYSTORE:-$HOME/.android/debug.keystore}"
  if [ ! -f "$KEYSTORE" ]; then
    echo "==> 生成 debug keystore..."
    mkdir -p "$(dirname "$KEYSTORE")"
    keytool -genkeypair -v -keystore "$KEYSTORE" -storepass android -keypass android \
      -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
      -dname "CN=Android Debug,O=Android,C=US" 2>/dev/null
  fi
  # 追加本地签名配置（幂等：重复执行不会重复追加）
  # 说明：只追加一次 android 块，release buildType 会被重新指向 releaseLocal；
  # 不要再用 sed 改写原有 signingConfigs.getByName("debug")，否则同名配置会被创建两次。
  if ! grep -q 'releaseLocal' "$APP_DIR/android/app/build.gradle.kts"; then
    cat >> "$APP_DIR/android/app/build.gradle.kts" <<EOF

android {
  signingConfigs {
    create("releaseLocal") {
      storeFile = file("$KEYSTORE")
      storePassword = "android"
      keyAlias = "androiddebugkey"
      keyPassword = "android"
    }
  }
  buildTypes {
    release {
      signingConfig = signingConfigs.getByName("releaseLocal")
    }
  }
}
EOF
  fi
  flutter build apk --release
  echo ""
  echo "release APK 已生成: $APP_DIR/build/app/outputs/flutter-apk/app-release.apk"
  echo "安装到手机:  adb install $APP_DIR/build/app/outputs/flutter-apk/app-release.apk"
  echo "注意：此 APK 用 debug keystore 签名，正式分发前请换自己的 keystore。"
else
  flutter build apk --debug
  echo ""
  echo "APK 已生成: $APP_DIR/build/app/outputs/flutter-apk/app-debug.apk"
  echo "安装到手机:  adb install $APP_DIR/build/app/outputs/flutter-apk/app-debug.apk"
fi

echo ""
echo "⚠ 注意：本 APK 对应旧版 goose 后端（ACP/3284），与新版 agentd（SSE/8787）不兼容。"
echo "  新版手机端使用：bash termux/start.sh → 浏览器打开 http://127.0.0.1:8787"
echo ""
echo "手机侧（旧版流程）：先跑 termux/install.legacy.sh 装好 goose + provider，"
echo "启动后在 Termux 里执行:"
echo "  source ~/.agent/termux.env && ~/.local/bin/goose serve --dangerously-unauthenticated"
echo "然后打开本 APK，默认地址 ws://127.0.0.1:3284/acp 即可连接。"