# 口袋智灵 · APK 打包方案

本产品是「Termux 本地服务 + 浏览器 Web UI」，**PWA（添加到主屏幕）就是它的
"安装"形态，零构建、零体积开销**；下面两种路径按需选一。

---

## 路径 A：PWA 安装（推荐，零构建，开箱即用）

前置：`bash termux/start.sh` 启动 agentd，手机浏览器打开 `http://127.0.0.1:8787`。

| 浏览器 | 操作 |
|---|---|
| Chrome | 菜单 →「添加到主屏幕」（或「安装应用」） |
| Edge | 菜单 →「添加到手机」→「安装」 |
| 其他 Chromium 内核 | 同样有「添加到主屏幕」 |

安装后：主屏出现「口袋智灵」图标（渐变口袋 Logo），独立全屏窗口运行，
和 APK 体验一致。Service Worker 缓存了页面壳，断网重开也能秒开界面
（工具调用时再连回本机服务）。

## 路径 B：WebView 壳 APK（想要独立 APK 时）

`termux/apk-shell/` 是一个 **30 行 Java 的原生 WebView 壳工程**（零第三方依赖，
不引 Capacitor/Flutter），启动即全屏加载 `http://127.0.0.1:8787`。

### 构建步骤

1. 电脑安装 **Android Studio**（自带 SDK + Gradle，约 1.5GB，只需一次）；
2. File → Open → 选择 `termux/apk-shell/` 目录；
3. Build → Generate Signed APK（首次需创建签名）或直接 Run 到手机调试；
4. 把 APK 装到手机（`adb install` 或传到手机点击安装）；
5. 手机上先 `bash termux/start.sh` 启动 agentd，再打开「口袋智灵」App。

### 壳的安全细节

- 网络配置只放行 `127.0.0.1`/`localhost` 明文，其他流量保持系统默认；
- 只申请 `INTERNET` 一个权限，无任何业务逻辑在壳内，风险面最小；
- 竖屏锁定、启动深色背景，与 Web 主题一致不闪白屏；
- 返回键先走页面历史。

### 如果想改端口

改 `app/src/main/java/com/pocket/agent/MainActivity.java` 里的
`web.loadUrl("http://127.0.0.1:8787")` 端口号，与 `start.sh 自定义端口` 对应。

---

## 不推荐：PWA Builder / Bubblewrap 在线打包

这类工具生成的是 **Trusted Web Activity（TWA）**，要求站点有公网 HTTPS 域名。
我们的服务跑在手机本地 `127.0.0.1`，没有公网域名，TWA 不适用——这是架构决定的，
不是能力缺失。PWA 安装（路径 A）已覆盖同场景。
