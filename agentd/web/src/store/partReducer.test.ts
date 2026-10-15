// 工具耗时与事件折叠的单元测试。
// 为什么放这里：e2e 里 mock 工具几毫秒就结束、不触发耗时显示，
// 由纯函数单测确定性地覆盖计时与格式化分支。
import { describe, expect, it, vi } from "vitest";
import { applyEvent } from "./partReducer";
import type { Msg } from "../types";

const emptyMsg = (): Msg => ({ id: "a1", role: "assistant", parts: [], done: false });

describe("工具耗时（tool_start → tool_update）", () => {
  it("结算出耗时并换算成秒/毫秒文本", () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1000);
    let msg = applyEvent(emptyMsg(), {
      type: "tool_start",
      id: "t1",
      name: "run_shell",
      input: { command: "echo hi" },
    } as never);
    expect(msg.parts[0].type).toBe("tool");

    now.mockReturnValue(1450); // 450ms
    msg = applyEvent(msg, { type: "tool_update", id: "t1", status: "completed", result: { ok: true } } as never);
    const tool = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(tool?.ms).toBe(450);
    expect(tool?.status).toBe("completed");
    now.mockRestore();
  });

  it("多次 update 不覆盖首次结算的耗时（避免区间被越拉越长）", () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(0);
    let msg = applyEvent(emptyMsg(), { type: "tool_start", id: "t2", name: "read_sms", input: {} } as never);
    now.mockReturnValue(800);
    msg = applyEvent(msg, { type: "tool_update", id: "t2", status: "completed", result: {} } as never);
    now.mockReturnValue(99999);
    msg = applyEvent(msg, { type: "tool_update", id: "t2", status: "completed", result: {} } as never);
    const tool = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(tool?.ms).toBe(800);
    now.mockRestore();
  });

  it("审批中（waiting）不结算耗时：等待用户的时间不算工具执行时间", () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(0);
    let msg = applyEvent(emptyMsg(), { type: "tool_start", id: "t3", name: "run_shell", input: {} } as never);
    now.mockReturnValue(60000);
    msg = applyEvent(msg, { type: "approval", id: "t3", summary: "执行 shell", risk: "danger" } as never);
    const tool = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(tool?.status).toBe("waiting");
    expect(tool?.startedAt).toBeUndefined();
    now.mockRestore();
  });

  it("批准后只计执行段：等待审批的 60 秒不会显示成工具耗时", () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(0);
    let msg = applyEvent(emptyMsg(), { type: "tool_start", id: "t4", name: "run_shell", input: {} } as never);
    now.mockReturnValue(60000); // 用户思考 60s
    msg = applyEvent(msg, { type: "approval", id: "t4", summary: "执行 shell", risk: "danger" } as never);
    const waiting = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(waiting?.startedAt).toBeUndefined();

    // 前端 onApproval 在批准后把状态改回 running 并从决策时刻重新计时（store 层行为，
    // 这里模拟该结果态：status=running、startedAt=批准时刻；startedFor 仍是 tool_start 留下的 running）
    if (waiting) {
      waiting.status = "running";
      waiting.startedAt = 61000;
    }
    now.mockReturnValue(61200); // 执行 200ms
    msg = applyEvent(msg, { type: "tool_update", id: "t4", status: "completed", result: { ok: true } } as never);
    const done = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(done?.ms).toBe(200);
    now.mockRestore();
  });

  it("被直接拒绝（denied）的工具不显示耗时", () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(0);
    let msg = applyEvent(emptyMsg(), { type: "tool_start", id: "t5", name: "run_shell", input: {} } as never);
    now.mockReturnValue(60000);
    msg = applyEvent(msg, { type: "approval", id: "t5", summary: "执行 shell", risk: "danger" } as never);
    now.mockReturnValue(120000);
    msg = applyEvent(msg, { type: "tool_update", id: "t5", status: "denied", result: {} } as never);
    const tool = msg.parts[0].type === "tool" ? msg.parts[0].tool : null;
    expect(tool?.status).toBe("denied");
    expect(tool?.ms).toBeUndefined();
    now.mockRestore();
  });
});
