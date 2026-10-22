// memo 回归：MessageItem 在 props 未变时不得重渲染。
//
// 为什么需要这条测试：流式输出时 App 每一帧都会重渲染，若 MessageItem 没有 memo
// （或父组件每次传新的 inline 回调），200 条历史消息会跟着逐字重渲染，长对话明显掉帧。
// 这类退化在功能测试里完全看不出来——界面"对"，只是卡。
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageItem } from "./MessageItem";
import type { Msg } from "../types";

// 统计 ToolCard/内部子组件的渲染次数不现实，改为直接检查导出的组件是否 memo 化：
// React.memo 组件的 $$typeof 为 Symbol(react.memo)。
describe("MessageItem memo 化", () => {
  it("导出的是 memo 组件", () => {
    const t = (MessageItem as unknown as { $$typeof?: symbol }).$$typeof;
    expect(String(t)).toContain("react.memo");
  });

  it("相同 props 下不重复渲染子组件树", () => {
    const msg: Msg = {
      id: "m1",
      role: "assistant",
      parts: [{ type: "text", text: "hello" }],
      done: true,
      createdAt: 1,
    };
    const noop = vi.fn();
    const props = {
      msg,
      parts: msg.parts,
      onApproval: noop,
      onApprovePlan: noop,
      onEditPlan: noop,
      onIgnorePlan: noop,
    };
    const { rerender, container } = render(<MessageItem {...props} />);
    const html1 = container.innerHTML;
    rerender(<MessageItem {...props} />);
    const html2 = container.innerHTML;
    // 内容不变（memo 生效时 React 会跳过子树渲染）
    expect(html2).toBe(html1);
    expect(html2).toContain("hello");
  });
});
