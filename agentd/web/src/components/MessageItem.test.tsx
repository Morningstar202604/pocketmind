// MessageItem / 审批卡契约回归（组件层，jsdom）：
// 1) 工具卡从「运行中」变为「需要确认」时必须自动展开——否则审批按钮与倒计时被折叠藏起来，
//    而等待会超时自动拒绝（手机上用户可能完全没看到要审批什么）。这个 bug 曾经是「看时序」的：
//    挂载时就已是 waiting 才展开，先 running 后 waiting 则不展开。
// 2) 五个决策按钮齐全，点击回调带上正确的决策值。
// 3) 倒计时用后端下发的 approvalTimeout，而不是写死的 120。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageItem } from "./MessageItem";
import type { Msg, ToolPart } from "../types";

const toolPart = (status: ToolPart["status"]): ToolPart => ({
  id: "call_1",
  name: "run_shell",
  input: { command: "echo hi" },
  status,
  summary: "执行 shell 命令（可运行任意命令，请确认是你想要的）",
  risk: "danger",
  result: status === "denied" ? { denied: "用户拒绝了该操作" } : undefined,
});

const msg = (tools: ToolPart[]): Msg => ({
  id: "m1",
  role: "assistant",
  parts: tools.map((t) => ({ type: "tool" as const, tool: t })),
  done: tools.every((t) => t.status !== "running" && t.status !== "waiting"),
});

const renderMsg = (tools: ToolPart[], approvalTimeout?: number) => {
  const onApproval = vi.fn();
  const { rerender } = render(
    <MessageItem
      msg={msg(tools)}
      parts={msg(tools).parts}
      onApproval={onApproval}
      onApprovePlan={vi.fn()}
      onEditPlan={vi.fn()}
      onIgnorePlan={vi.fn()}
      approvalTimeout={approvalTimeout}
    />
  );
  return { onApproval, rerender };
};

describe("MessageItem 审批卡", () => {
  it("工具先「运行中」后「需要确认」时，审批按钮自动可见（不再需要手动展开）", () => {
    const { rerender } = renderMsg([toolPart("running")]);
    // 刚运行时按钮不该出现
    expect(screen.queryByText("允许一次")).toBeNull();
    // 状态推进到 waiting（tool_start 与 approval 属不同渲染批次）
    const waiting = msg([toolPart("waiting")]);
    rerender(
      <MessageItem
        msg={waiting}
        parts={waiting.parts}
        onApproval={vi.fn()}
        onApprovePlan={vi.fn()}
        onEditPlan={vi.fn()}
        onIgnorePlan={vi.fn()}
      />
    );
    expect(screen.getByText("允许一次")).toBeInTheDocument();
    expect(screen.getByText("始终允许")).toBeInTheDocument();
    expect(screen.getByText("拒绝")).toBeInTheDocument();
    expect(screen.getByText("全局允许")).toBeInTheDocument();
    expect(screen.getByText("始终拒绝")).toBeInTheDocument();
  });

  it("五个决策各自回调正确的 decision 值", () => {
    const { onApproval } = renderMsg([toolPart("waiting")]);
    for (const [label, decision] of [
      ["允许一次", "allow_once"],
      ["始终允许", "allow_always"],
      ["拒绝", "deny"],
      ["全局允许", "allow_global"],
      ["始终拒绝", "deny_always"],
    ] as const) {
      onApproval.mockClear();
      fireEvent.click(screen.getByText(label));
      expect(onApproval).toHaveBeenCalledWith("call_1", decision);
    }
  });

  it("倒计时显示后端配置的超时值（不是写死的 120）", () => {
    renderMsg([toolPart("waiting")], 30);
    expect(screen.getByText(/30 秒后自动拒绝/)).toBeInTheDocument();
  });

  it("未传 approvalTimeout 时退回 120 秒兜底", () => {
    renderMsg([toolPart("waiting")]);
    expect(screen.getByText(/120 秒后自动拒绝/)).toBeInTheDocument();
  });

  it("被拒绝的工具卡展示「已拒绝」且不再出现审批按钮", () => {
    renderMsg([toolPart("denied")]);
    expect(screen.getByText("已拒绝")).toBeInTheDocument();
    expect(screen.queryByText("允许一次")).toBeNull();
  });
});
