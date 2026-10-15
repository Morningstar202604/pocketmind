// 展示层格式化 helper：同一种信息在多处出现时保持同一口径。

/**
 * 相对时间（会话列表用）：刚刚 / N 分钟前 / N 小时前 / 昨天 HH:MM / M月D日。
 * 手机上浏览列表时"多久以前"比绝对时间戳更有用。
 */
export function relativeTime(ts?: number, now: number = Date.now() / 1000): string {
  if (!ts || !Number.isFinite(ts)) return "";
  const diff = Math.max(0, now - ts);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  const d = new Date(ts * 1000);
  const hhmm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (diff < 172800) return `昨天 ${hhmm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/**
 * 工具执行结果的展示文本：能结构化成"键 → 值"时就不要甩一段 JSON。
 * 手机窄屏上 raw JSON 可读性很差，而结果往往是几个短字段。
 * 无法结构化时（数组 / 嵌套 / 超长）退回 JSON 文本，保证信息不丢。
 */
export function formatToolResult(result: unknown): string {
  if (result === null || result === undefined) return "";
  if (typeof result === "string") return result;
  if (typeof result !== "object") return String(result);
  const entries = Object.entries(result as Record<string, unknown>);
  if (entries.length === 0) return "";
  const flat = entries.every(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v));
  if (!flat) return JSON.stringify(result, null, 2);
  return entries
    .map(([k, v]) => {
      const text = v === null ? "—" : typeof v === "boolean" ? (v ? "是" : "否") : String(v);
      return `${k}：${text.length > 200 ? text.slice(0, 200) + "…" : text}`;
    })
    .join("\n");
}
