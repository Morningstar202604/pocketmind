// 展示层格式化回归：相对时间与工具结果文本。
// 这两个函数决定手机窄屏上"用户看到什么"，且都被 UI 直接使用，值得锁住行为。
import { describe, expect, it } from "vitest";
import { formatToolResult, relativeTime } from "./format";

/** 固定"现在"为 2026-10-14 12:00:00 本地时间，避免测试随时钟漂移。 */
const NOW = new Date(2026, 9, 14, 12, 0, 0).getTime() / 1000;

describe("relativeTime：会话列表的时间口径", () => {
  it("一分钟内显示「刚刚」", () => {
    expect(relativeTime(NOW - 5, NOW)).toBe("刚刚");
    expect(relativeTime(NOW, NOW)).toBe("刚刚");
  });

  it("一小时内按分钟，一天内按小时", () => {
    expect(relativeTime(NOW - 120, NOW)).toBe("2 分钟前");
    expect(relativeTime(NOW - 3600 * 3, NOW)).toBe("3 小时前");
  });

  it("昨天带具体时刻，更早只给日期", () => {
    // 12:00 往前 1 天 = 昨天 12:00（同一时刻的前一天）
    expect(relativeTime(NOW - 86400, NOW)).toBe("昨天 12:00");
    // 往前 1.5 天落到前一天 00:00，仍属"昨天"分支
    expect(relativeTime(NOW - 86400 * 1.5, NOW)).toBe("昨天 00:00");
    expect(relativeTime(NOW - 86400 * 4, NOW)).toBe("10月10日");
  });

  it("缺失或非法值返回空串（调用方据此决定不渲染）", () => {
    expect(relativeTime(undefined, NOW)).toBe("");
    expect(relativeTime(0, NOW)).toBe("");
    expect(relativeTime(NaN, NOW)).toBe("");
  });

  it("未来时间不出现负数（时钟偏差时收敛为「刚刚」）", () => {
    expect(relativeTime(NOW + 600, NOW)).toBe("刚刚");
  });
});

describe("formatToolResult：工具结果的可读化", () => {
  it("扁平对象逐行「键：值」，不再甩 JSON", () => {
    const out = formatToolResult({ ok: true, exit_code: 0, stdout: "口袋智灵工具链路OK" });
    expect(out).toBe("ok：是\nexit_code：0\nstdout：口袋智灵工具链路OK");
  });

  it("null 显示为占位符，空对象返回空串", () => {
    expect(formatToolResult({ a: null })).toBe("a：—");
    expect(formatToolResult({})).toBe("");
  });

  it("嵌套/数组退回 JSON（信息不丢）", () => {
    const nested = { items: [{ name: "a" }] };
    expect(formatToolResult(nested)).toBe(JSON.stringify(nested, null, 2));
  });

  it("超长字符串被截断，避免撑爆卡片", () => {
    const out = formatToolResult({ text: "啊".repeat(500) });
    expect(out.length).toBeLessThan(220);
    expect(out.endsWith("…")).toBe(true);
  });

  it("字符串/数字/布尔原样处理", () => {
    expect(formatToolResult("done")).toBe("done");
    expect(formatToolResult(3)).toBe("3");
    expect(formatToolResult(null)).toBe("");
  });
});
