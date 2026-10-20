// 发送队列回归：**任何入队消息最终都必须被发出去**，不能被静默吞掉。
//
// 背景（真实事故）：旧实现是「push 后若没在跑就自己起 while 循环，循环结束后置 running=false」。
// 在「循环判空退出」与「running=false」之间的窗口入队的消息会看到 running 仍为 true 而直接 return，
// 队列从此没有驱动者——界面上停在「已排队」，实际永不执行（实测连发 3 条只有 2 条落库）。
// 现在统一走 driveQueue（含收尾接力），本测试锁定该不变量。
import { beforeEach, describe, expect, it, vi } from "vitest";

const sseChat = vi.fn();
const get = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      get: (...a: unknown[]) => get(...a),
      post: vi.fn(() => Promise.resolve({})),
      put: vi.fn(() => Promise.resolve({})),
      del: vi.fn(() => Promise.resolve({})),
      sseChat: (...a: unknown[]) => sseChat(...a),
    },
    getTodos: () => Promise.resolve([]),
  };
});

import { useAgentStore } from "./useAgentStore";

/** 每轮 SSE 用可控延迟模拟「上一轮还没结束」。 */
function mockStream(delayMs: number) {
  sseChat.mockImplementation(
    (_sid: string, _msg: string, onEvent: (ev: unknown) => void) =>
      new Promise<void>((resolve) => {
        setTimeout(() => {
          onEvent({ type: "text", text: "ok" });
          onEvent({ type: "done", stop_reason: "end_turn" });
          resolve();
        }, delayMs);
      })
  );
}

beforeEach(() => {
  sseChat.mockReset();
  get.mockReset();
  get.mockResolvedValue([]);
  useAgentStore.setState({ messages: [], busy: false, error: "", sessionId: "s1", todos: [] });
});

describe("发送队列：消息不被吞", () => {
  it("同一时刻连发三条：三条都要被送出且顺序正确", async () => {
    mockStream(20);
    const { send } = useAgentStore.getState();
    await Promise.all([send("第一条"), send("第二条"), send("第三条")]);
    await vi.waitFor(() => expect(sseChat).toHaveBeenCalledTimes(3), { timeout: 3000 });
    expect(sseChat.mock.calls.map((c) => c[1])).toEqual(["第一条", "第二条", "第三条"]);
    expect(useAgentStore.getState().busy).toBe(false);
  });

  it("上一轮进行中入队的消息，在本轮结束后立即被执行", async () => {
    mockStream(50);
    const { send } = useAgentStore.getState();
    const first = send("A");
    await new Promise((r) => setTimeout(r, 10)); // A 正在跑
    const second = send("B"); // 入队
    await Promise.all([first, second]);
    await vi.waitFor(() => expect(sseChat).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect(sseChat.mock.calls.map((c) => c[1])).toEqual(["A", "B"]);
  });

  it("队列排空后再次发送仍然工作（驱动器可重复启动）", async () => {
    mockStream(10);
    const { send } = useAgentStore.getState();
    await send("第一次");
    await vi.waitFor(() => expect(sseChat).toHaveBeenCalledTimes(1));
    await send("第二次");
    await vi.waitFor(() => expect(sseChat).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect(useAgentStore.getState().busy).toBe(false);
  });

  it("空文本不发送", async () => {
    mockStream(10);
    await useAgentStore.getState().send("   ");
    await new Promise((r) => setTimeout(r, 30));
    expect(sseChat).not.toHaveBeenCalled();
  });

  // 回归：新建会话会走 onNewSession → loadSession（先清空再异步回填）。
  // 用户在这两步之间发消息时，旧实现会把刚插入的「用户消息 + 助手占位」抹掉，
  // 界面退回空态、只剩一条停在「正在思考…」的孤儿行（E2E 用例 3/4 偶发失败）。
  it("会话加载期间的发送不会被回填覆盖", async () => {
    mockStream(10);
    // 让 /api/sessions/{id}/messages 慢一拍返回空历史
    let release: (v: unknown) => void = () => {};
    const gate = new Promise((r) => (release = r));
    get.mockImplementation((path: string) =>
      path.includes("/messages") ? gate : Promise.resolve([])
    );
    useAgentStore.setState({ sessionId: "s1", messages: [] });

    const loading = useAgentStore.getState().loadSession("s1");
    const sending = useAgentStore.getState().send("你好"); // 加载尚未返回时发送
    await sending;
    release([]);
    await loading;

    const msgs = useAgentStore.getState().messages;
    expect(msgs.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(msgs[0].parts[0]).toMatchObject({ type: "text", text: "你好" });
  });
});
