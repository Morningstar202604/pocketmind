// 配方触发文案翻译纯函数单测：interval/cron/date 三类表达式。
import { describe, expect, it } from "vitest";
import { humanizeTrigger } from "./TimerPanel";

describe("humanizeTrigger：触发表达式 → 人类可读文案", () => {
  it("interval 秒数按整分/整小时换算", () => {
    expect(humanizeTrigger({ trigger_type: "interval", expr: "1800" })).toBe("每 30 分钟");
    expect(humanizeTrigger({ trigger_type: "interval", expr: "3600" })).toBe("每 1 小时");
    expect(humanizeTrigger({ trigger_type: "interval", expr: "30" })).toBe("每 30 秒");
  });

  it("简单 cron（每天定点）翻译成每天 HH:MM", () => {
    expect(humanizeTrigger({ trigger_type: "cron", expr: "0 8 * * *" })).toBe("每天 08:00");
    expect(humanizeTrigger({ trigger_type: "cron", expr: "30 18 * * *" })).toBe("每天 18:30");
  });

  it("复杂 cron 原样展示，date 到点展示", () => {
    expect(humanizeTrigger({ trigger_type: "cron", expr: "0 8 * * 1" })).toBe("cron 0 8 * * 1");
    expect(humanizeTrigger({ trigger_type: "date", expr: "2026-10-01 09:00" })).toBe(
      "到点 2026-10-01 09:00"
    );
  });
});
