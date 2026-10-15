// TodoPanel 契约回归：
// 1) 无清单时不占位；2) 进度可读（数字 + 进度条 aria）；3) 状态可辨识；
// 4) 全部完成才给「清空」（避免误清运行中的清单）；5) 可收起/展开（移动端省屏）。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TodoPanel } from "./TodoPanel";

type S = "pending" | "in_progress" | "completed";
const T = (content: string, status: S) => ({ content, status });

describe("TodoPanel：任务清单面板", () => {
  it("无清单时不渲染（不占聊天区）", () => {
    const { container } = render(<TodoPanel todos={[]} onClear={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it("显示完成进度：数字与进度条都要有", () => {
    render(
      <TodoPanel
        todos={[T("查电量", "completed"), T("发通知", "in_progress"), T("写文件", "pending")]}
        onClear={() => {}}
      />
    );
    expect(screen.getByText("1/3")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("1");
    expect(bar.getAttribute("aria-valuemax")).toBe("3");
  });

  it("三种状态在 DOM 上可辨识（供样式上色）", () => {
    render(
      <TodoPanel
        todos={[T("甲", "completed"), T("乙", "in_progress"), T("丙", "pending")]}
        onClear={() => {}}
      />
    );
    expect(screen.getByText("甲").closest("li")?.className).toContain("completed");
    expect(screen.getByText("乙").closest("li")?.className).toContain("in_progress");
    expect(screen.getByText("丙").closest("li")?.className).toContain("pending");
  });

  it("全部完成时才出现「清空」，点击回调一次", () => {
    const onClear = vi.fn();
    const { unmount } = render(<TodoPanel todos={[T("甲", "completed")]} onClear={onClear} />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(onClear).toHaveBeenCalledTimes(1);
    unmount();
    render(<TodoPanel todos={[T("甲", "pending")]} onClear={() => {}} />);
    expect(screen.queryByRole("button", { name: /清空/ })).toBeNull();
  });

  it("可收起/展开：收起后列表不渲染，标题仍在", () => {
    render(<TodoPanel todos={[T("甲", "pending")]} onClear={() => {}} />);
    const toggle = screen.getByRole("button", { name: /收起|展开/ });
    fireEvent.click(toggle);
    expect(screen.queryByText("甲")).toBeNull();
    expect(screen.getByText("任务清单")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /收起|展开/ }));
    expect(screen.getByText("甲")).toBeInTheDocument();
  });
});
