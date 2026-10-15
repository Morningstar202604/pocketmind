// PocketNative JS Bridge 封装：统一 Android WebView 注入对象 PocketNative 的调用。
// 在浏览器 / 非 WebView 环境下优雅降级（no-op 或 fallback），不抛错。

/**
 * PocketNative 桥接对象声明（Android WebView 注入的全局变量）。
 * 这里只声明我们用到的方法；运行时 typeof 检查决定是否走原生通道。
 */
interface PocketNativeBridge {
  /** 触发设备振动（毫秒） */
  vibrate?: (ms: number) => void;
  /** 写入系统剪贴板（Android 原生实现，不依赖 HTTPS） */
  copyText?: (text: string) => void;
  /** 触发页面重载 */
  reload?: () => void;
}

declare global {
  interface Window {
    PocketNative?: PocketNativeBridge;
  }
}

function getBridge(): PocketNativeBridge | null {
  return typeof window !== "undefined" ? window.PocketNative ?? null : null;
}

/**
 * 振动反馈（Android 原生）。
 * @param ms 振动时长（毫秒），默认 30
 */
export function vibrate(ms = 30): void {
  try {
    getBridge()?.vibrate?.(ms);
  } catch {
    /* 浏览器环境或桥调用失败：忽略 */
  }
}

/**
 * 复制文本到系统剪贴板。
 * WebView 优先走原生（无 HTTPS 限制）；浏览器降级到 Clipboard API。
 */
export async function copyText(text: string): Promise<boolean> {
  const bridge = getBridge();
  if (bridge?.copyText) {
    try {
      bridge.copyText(text);
      return true;
    } catch {
      /* 降级到浏览器 API */
    }
  }
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * 触发页面重载（WebView 走原生 reload 以保留 WebView 状态；浏览器走 location.reload）。
 */
export function reloadPage(): void {
  const bridge = getBridge();
  if (bridge?.reload) {
    try {
      bridge.reload();
      return;
    } catch {
      /* 降级 */
    }
  }
  if (typeof window !== "undefined") {
    window.location.reload();
  }
}
