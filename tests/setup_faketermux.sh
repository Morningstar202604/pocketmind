#!/usr/bin/env bash
# 建立 /tmp/faketermux 测试桩：25 个 termux-api 命令的最小可运行替身，
# 返回真实格式的输出，供 mock 模式下的回归测试使用。
# 用法：bash tests/setup_faketermux.sh
set -euo pipefail

DIR="${FAKETERMUX_DIR:-/tmp/faketermux}"
mkdir -p "$DIR"

make_stub() {
  local name="$1" output="$2"
  cat > "$DIR/$name" <<EOF
#!/usr/bin/env bash
# auto-generated stub for $name
cat <<'STUB_EOF'
$output
STUB_EOF
EOF
  chmod +x "$DIR/$name"
}

# 系统
make_stub termux-battery-status '[{"health": "GOOD", "percentage": 85, "plugged": "UNPLUGGED", "status": "DISCHARGING", "temperature": 32.5}]'
make_stub termux-notification '{"status": "ok"}'
make_stub termux-vibrate '{"status": "ok"}'
make_stub termux-torch '{"status": "ok"}'
make_stub termux-volume '[{"stream": "music", "volume": 7}]'
make_stub termux-brightness '{"status": "ok"}'
make_stub termux-toast '{"status": "ok"}'

# 通信
make_stub termux-sms-send '{"status": "ok"}'
make_stub termux-sms-list '[{"_id": 1, "address": "10086", "body": "【测试】您的验证码是 123456", "date": 1700000000000, "read": 0}]'
make_stub termux-telephony-call '{"status": "ok"}'

# 隐私
make_stub termux-location '{"latitude": 23.0, "longitude": 113.0, "altitude": 10.0, "accuracy": 20.0}'
make_stub termux-clipboard-get '测试剪贴板内容'
make_stub termux-clipboard-set '{"status": "ok"}'

# 传感
make_stub termux-sensor-list '["accelerometer", "gyroscope", "magnetometer", "proximity", "light"]'
make_stub termux-sensor '{"accelerometer": {"values": [0.0, 0.0, 9.8]}}'

# 语音
make_stub termux-tts-speak '{"status": "ok"}'
make_stub termux-microphone-record '{"is_recording": false}'

# 网络
make_stub termux-wifi-info '{"bssid": "00:11:22:33:44:55", "frequency": 2412, "ip": "192.168.1.100", "link_speed": 72, "network_id": 0, "rssi": -55, "ssid": "TestWiFi", "supplicant_state": "COMPLETED"}'
make_stub termux-wifi-scan '[{"bssid": "00:11:22:33:44:55", "frequency": 2412, "rssi": -55, "ssid": "TestWiFi", "timestamp": 1700000000}]'
make_stub termux-wifi-enable '{"status": "ok"}'

# 媒体 / 文件
make_stub termux-camera-photo '{"status": "ok"}'
make_stub termux-share '{"status": "ok"}'
make_stub termux-download '{"status": "ok"}'
make_stub termux-open '{"status": "ok"}'

echo "faketermux stubs created at $DIR ($(ls "$DIR" | wc -l) commands)"
echo "Add to PATH: export PATH=\"$DIR:\$PATH\""
