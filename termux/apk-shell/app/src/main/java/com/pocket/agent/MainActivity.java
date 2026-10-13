/* 口袋智灵 · Android WebView 壳
 *
 * 相比初代的变化：
 *   - agentd 不可达时显示中文错误页（含启动命令、复制、重试），而非白屏
 *   - 页面状态杀进程后恢复（onSaveInstanceState）
 *   - debug 版本开启 WebView 远程调试
 *   - JS Bridge：reload / copyText / vibrate / notify / launchTermux
 *   - URL 端口由 BuildConfig.AGENTD_PORT + resValue 组装，可配置
 *   - ForegroundService 监控 agentd 存活，掉线自动切错误页
 *   - onReceivedTitle / onReceivedSslError / onReceivedHttpError 全套处理
 *   - 启动前健康检查；onResume 回到前台时重新 ping
 *
 * 零 AndroidX 依赖，纯框架 API（minSdk 26 全覆盖）。
 */

package com.pocket.agent;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.http.SslError;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.util.Log;
import android.view.KeyEvent;
import android.webkit.ConsoleMessage;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity {

    private static final String TAG = "PocketAgent";
    private static final int NOTIFICATION_PERMISSION_CODE = 1001;
    private static final String CHANNEL_ID = "pocket_agent";
    private static final int NOTIFICATION_ID_BASE = 1000;
    private static final int HEALTH_TIMEOUT_MS = 2000;

    private WebView web;
    private boolean agentdUnreachable = false;
    private String pageTitle;

    // ── 监控器广播接收：实时驱动错误页切换 ──
    private BroadcastReceiver monitorReceiver;
    private boolean monitorReceiverRegistered = false;

    /** 正在做自检 / ping 中，避免重复并发 */
    private final AtomicBoolean healthInProgress = new AtomicBoolean(false);

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ensureNotificationChannel(this);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportZoom(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setLoadsImagesAutomatically(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        // 调试版本开启 WebView 远程调试，chrome://inspect 即可联调
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true);
            Log.d(TAG, "WebView remote debugging enabled");
        }

        web.setWebViewClient(new PocketWebViewClient());
        web.setWebChromeClient(new PocketChromeClient());

        // JS Bridge —— 给 Web UI 调用原生能力
        web.addJavascriptInterface(new PocketBridge(this), "PocketNative");

        // 状态恢复优先于首屏加载
        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            // 启动自检：先 ping agentd，失败则立刻切错误页
            performStartupSelfCheck();
        }

        setContentView(web);

        // 注册监控广播 + 驻活 ForegroundService
        registerMonitorReceiver();
        startMonitorService();
    }

    // ════════════════════════════════════════════════
    //  前台服务启停 + 广播监听
    // ════════════════════════════════════════════════

    private void startMonitorService() {
        Intent i = new Intent(this, MonitorService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(i);
        } else {
            startService(i);
        }
    }

    private void stopMonitorService() {
        stopService(new Intent(this, MonitorService.class));
    }

    private void registerMonitorReceiver() {
        if (monitorReceiverRegistered) return;
        monitorReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context ctx, Intent intent) {
                if (intent == null) return;
                boolean alive = intent.getBooleanExtra(MonitorService.EXTRA_IS_ALIVE, true);
                Log.d(TAG, "Monitor broadcast: alive=" + alive);
                if (!alive && !agentdUnreachable) {
                    // agentd 掉线 → 立刻切错误页
                    showErrorPage();
                }
                // 注意：alive=true 不会自动恢复，让用户点重试，避免 SSE 流中断后静默重载
            }
        };
        IntentFilter filter = new IntentFilter(MonitorService.ACTION_STATE_CHANGED);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(monitorReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(monitorReceiver, filter);
        }
        monitorReceiverRegistered = true;
    }

    // ════════════════════════════════════════════════
    //  启动自检 & onResume ping
    // ════════════════════════════════════════════════

    private void performStartupSelfCheck() {
        if (!healthInProgress.compareAndSet(false, true)) return;
        new Thread(() -> {
            final boolean alive = doHealthCheck();
            mainHandler().post(() -> {
                healthInProgress.set(false);
                if (alive) {
                    agentdUnreachable = false;
                    loadAgentdUrl();
                } else {
                    agentdUnreachable = true;
                    showSetupPage();
                }
            });
        }).start();
    }

    /** onResume：从后台回来时 ping 一下，若已恢复则静默重载 */
    @Override
    protected void onResume() {
        super.onResume();
        pingOnResume();
    }

    private void pingOnResume() {
        if (!healthInProgress.compareAndSet(false, true)) return;
        new Thread(() -> {
            final boolean alive = doHealthCheck();
            mainHandler().post(() -> {
                healthInProgress.set(false);
                if (alive && agentdUnreachable) {
                    // 服务重新上线，自动恢复
                    agentdUnreachable = false;
                    loadAgentdUrl();
                } else if (!alive && !agentdUnreachable) {
                    // 前台期间服务挂了，切错误页
                    agentdUnreachable = true;
                    showErrorPage();
                }
            });
        }).start();
    }

    /** HTTP GET 探测 agentd，2 秒超时 */
    private boolean doHealthCheck() {
        String url = getString(R.string.agentd_protocol) + "://"
                + getString(R.string.agentd_host) + ":"
                + BuildConfig.AGENTD_PORT;
        HttpURLConnection conn = null;
        try {
            URL u = new URL(url);
            conn = (HttpURLConnection) u.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(HEALTH_TIMEOUT_MS);
            conn.setReadTimeout(HEALTH_TIMEOUT_MS);
            conn.setInstanceFollowRedirects(true);
            return conn.getResponseCode() > 0;
        } catch (Exception e) {
            Log.d(TAG, "health ping failed: " + e.getMessage());
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── 根据 BuildConfig 组装 http://127.0.0.1:PORT ──
    private void loadAgentdUrl() {
        String url = getString(R.string.agentd_protocol) + "://"
                + getString(R.string.agentd_host) + ":"
                + BuildConfig.AGENTD_PORT;
        Log.d(TAG, "Loading agentd URL: " + url);
        web.loadUrl(url);
    }

    // ════════════════════════════════════════════════
    //  WebView 全套事件处理
    // ════════════════════════════════════════════════

    private class PocketWebViewClient extends WebViewClient {

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                agentdUnreachable = true;
                Log.w(TAG, "agentd unreachable: " + error.getDescription());
                showErrorPage();
            }
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            String host = error.getUrl() != null ? Uri.parse(error.getUrl()).getHost() : null;
            boolean isLocal = "127.0.0.1".equals(host) || "localhost".equals(host);
            if (isLocal) {
                // 本机自签名证书放行
                Log.w(TAG, "SSL self-signed on localhost, proceeding");
                handler.proceed();
            } else {
                // 其他域名 SSL 错误：取消加载，显示错误页
                Log.e(TAG, "SSL error for " + host + ": " + error.getPrimaryError());
                handler.cancel();
                agentdUnreachable = true;
                showErrorPage();
            }
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                        WebResourceResponse errorResponse) {
            if (request.isForMainFrame()) {
                int code = errorResponse.getStatusCode();
                Log.w(TAG, "HTTP error " + code + " for " + request.getUrl());
                // 4xx / 5xx 都当作服务异常
                agentdUnreachable = true;
                showErrorPage();
            }
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            // 正常页面加载完成后清除错误标记
            if (url != null && !url.startsWith("file:///android_asset/")) {
                agentdUnreachable = false;
            }
        }
    }

    // ════ WebChromeClient：标题 + console ════
    private class PocketChromeClient extends WebChromeClient {
        @Override
        public boolean onConsoleMessage(ConsoleMessage msg) {
            Log.d(TAG, "WebView [" + msg.messageLevel() + "] " + msg.message()
                    + " -- " + msg.sourceId() + ":" + msg.lineNumber());
            return true;
        }

        @Override
        public void onReceivedTitle(WebView view, String title) {
            pageTitle = title;
            Log.d(TAG, "Page title: " + title);
        }
    }

    private void showSetupPage() {
        web.loadUrl("file:///android_asset/agentd_setup.html");
    }

    private void showErrorPage() {
        web.loadUrl("file:///android_asset/agentd_error.html");
    }

    // ════════════════════════════════════════════════
    //  生命周期完整性
    // ════════════════════════════════════════════════

    /** Android 13+ 通知开关是否打开 */
    private boolean canPostNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                    == PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    /** 返回键：错误页直接退出；否则优先页面历史 */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (agentdUnreachable) {
                finish();
                return true;
            }
            if (web.canGoBack()) {
                web.goBack();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (web != null) {
            web.saveState(outState);
        }
    }

    // ── 通知权限回调（Android 13+ 用户在系统弹窗中点击允许后） ──
    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        // 授权成功后补发缓存的待发通知（JS 不会自动重发）
        if (requestCode == NOTIFICATION_PERMISSION_CODE
                && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            postNotification(permissionTitle, permissionBody);
        }
    }

    // 通知权限请求期间缓存的参数（PocketBridge.notify 写入，授权回调读取）
    private String permissionTitle;
    private String permissionBody;

    private void postNotification(String title, String body) {
        NotificationManager mgr = getSystemService(NotificationManager.class);
        if (mgr == null) return;
        android.app.Notification n = new android.app.Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(title != null ? title : "口袋智灵")
                .setContentText(body != null ? body : "")
                .setAutoCancel(true)
                .build();
        try {
            mgr.notify(NOTIFICATION_ID_BASE, n);
        } catch (Exception ignored) {}
    }

    @Override
    protected void onDestroy() {
        if (monitorReceiverRegistered) {
            unregisterReceiver(monitorReceiver);
            monitorReceiverRegistered = false;
        }
        stopMonitorService();
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    // ════════════════════════════════════════════════
    //  工具方法
    // ════════════════════════════════════════════════

    private Handler mainHandler() {
        return new Handler(Looper.getMainLooper());
    }

    /** 在 Termux 中执行一条命令（通过 Termux 的 RunCommandService）。 */
    private void runInTermux(String cmd) {
        try {
            Intent intent = new Intent("com.termux.RUN_COMMAND");
            intent.setClassName("com.termux", "com.termux.app.RunCommandService");
            intent.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
            intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", cmd});
            intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
            intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", false);
            startService(intent);
        } catch (SecurityException e) {
            // Termux 未授予 RUN_COMMAND 权限：提示用户授权，而不是静默失败
            Log.w(TAG, "RunCommandService permission denied: " + e.getMessage());
            Toast.makeText(this,
                    "请在系统设置中允许本应用「在 Termux 环境中运行命令」权限，并在 Termux 设置中开启 allow-external-apps 后重试",
                    Toast.LENGTH_LONG).show();
            launchTermux();
        } catch (Exception e) {
            Log.w(TAG, "RunCommandService failed: " + e.getMessage());
            launchTermux();
        }
    }

    /** 尝试唤醒 Termux（包启动 + deep link 兜底）。 */
    private void launchTermux() {
        try {
            Intent launch = getPackageManager()
                    .getLaunchIntentForPackage("com.termux");
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(launch);
                Log.d(TAG, "Launched Termux by package");
                return;
            }
        } catch (Exception ignored) {}
        try {
            Intent deep = new Intent(Intent.ACTION_VIEW,
                    Uri.parse("term://com.termux/"));
            deep.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(deep);
            Log.d(TAG, "Launched Termux by deep link");
        } catch (Exception e) {
            Log.w(TAG, "No Termux installed: " + e.getMessage());
        }
    }

    /** 打开 Termux 的 F-Droid 下载页。 */
    private void openFdroid() {
        try {
            Intent browser = new Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://f-droid.org/packages/com.termux/"));
            browser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(browser);
        } catch (Exception e) {
            Log.w(TAG, "openFdroid failed: " + e.getMessage());
        }
    }

    private static void ensureNotificationChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager mgr = ctx.getSystemService(NotificationManager.class);
            if (mgr != null && mgr.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel ch = new NotificationChannel(
                        CHANNEL_ID, "口袋智灵", NotificationManager.IMPORTANCE_DEFAULT);
                ch.setDescription("定时任务、审批等系统消息");
                mgr.createNotificationChannel(ch);
            }
        }
    }

    // ════════════════════════════════════════════════
    //  JS Bridge 原生接口（零 AndroidX，纯框架 API）

    private static class PocketBridge {
        private final MainActivity activity;

        PocketBridge(MainActivity a) { this.activity = a; }

        /** 由错误页"重试"按钮调用，切回 agentd URL */
        @android.webkit.JavascriptInterface
        public void reload() {
            new Handler(Looper.getMainLooper()).post(() -> {
                if (activity.web != null) {
                    activity.agentdUnreachable = false;
                    activity.loadAgentdUrl();
                }
            });
        }

        /** 写入系统剪贴板 */
        @android.webkit.JavascriptInterface
        public void copyText(String text) {
            if (text == null) return;
            ClipboardManager cm = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("pocket", text));
        }

        /** 触发一次振动（ms 单位） */
        @android.webkit.JavascriptInterface
        public void vibrate(long ms) {
            Vibrator v;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager vm = (VibratorManager) activity.getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                v = vm != null ? vm.getDefaultVibrator() : null;
            } else {
                v = (Vibrator) activity.getSystemService(Context.VIBRATOR_SERVICE);
            }
            if (v == null || !v.hasVibrator()) return;
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(ms);
                }
            } catch (Exception ignored) {}
        }

        /** 发一条系统通知（Android 13+ 自动申请 POST_NOTIFICATIONS） */
        @android.webkit.JavascriptInterface
        public void notify(String title, String body) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                if (activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED) {
                    activity.permissionTitle = title;
                    activity.permissionBody = body;
                    activity.requestPermissions(
                            new String[]{Manifest.permission.POST_NOTIFICATIONS},
                            NOTIFICATION_PERMISSION_CODE);
                    return;
                }
                if (!activity.canPostNotifications()) {
                    Log.w(TAG, "POST_NOTIFICATIONS permanently denied");
                    return;
                }
            }
            postNotification(title, body);
        }

        /** 尝试唤醒 Termux（deep link intent） */
        @android.webkit.JavascriptInterface
        public void launchTermux() {
            activity.launchTermux();
        }

        /** 一键部署：在 Termux 中安装依赖、克隆仓库、安装并启动 agentd。 */
        @android.webkit.JavascriptInterface
        public void deployAgent(String repoUrl) {
            String url = (repoUrl == null || repoUrl.trim().isEmpty())
                    ? "https://github.com/X33834/pocketmind.git"
                    : repoUrl.trim();
            // git clone 已存在时自动 git pull 更新；install.sh 幂等可重复执行
            String cmd = "pkg install -y git python termux-api && "
                    + "(git clone " + url + " pocketmind 2>/dev/null || (cd pocketmind && git pull)) && "
                    + "cd pocketmind && bash termux/install.sh && bash termux/start.sh";
            activity.runInTermux(cmd);
        }

        /** 仅启动已部署的 agentd 服务。 */
        @android.webkit.JavascriptInterface
        public void startAgent() {
            activity.runInTermux("cd ~/pocketmind 2>/dev/null && bash termux/start.sh");
        }

        /** 重新执行健康检查；就绪后由原生层自动切换到 Web 界面。 */
        @android.webkit.JavascriptInterface
        public void checkAgain() {
            new Handler(Looper.getMainLooper()).post(() -> {
                if (activity.web != null) {
                    activity.agentdUnreachable = false;
                    activity.performStartupSelfCheck();
                }
            });
        }

        /** 打开 Termux 的 F-Droid 下载页。 */
        @android.webkit.JavascriptInterface
        public void openFdroid() {
            activity.openFdroid();
        }

        // 通知发送统一走 MainActivity.postNotification（授权回调需访问）
        private void postNotification(String title, String body) {
            activity.postNotification(title, body);
        }
    }
}
