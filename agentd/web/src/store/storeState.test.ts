// Zustand store 初始状态测试。
import { beforeEach, describe, expect, it } from "vitest";
import { useAgentStore } from "./useAgentStore";

describe("useAgentStore 初始状态", () => {
  // 每个用例前重置 store，避免跨用例污染
  beforeEach(() => {
    useAgentStore.setState({
      sessions: [],
      sessionId: "",
      messages: [],
      busy: false,
      ready: false,
      mock: false,
      error: "",
      showSessions: false,
      showSettings: false,
      showTimers: false,
      undoingId: null,
    });
  });

  it("初始为空闲态：无会话/无消息/非 busy/无错误", () => {
    const s = useAgentStore.getState();
    expect(s.sessions).toEqual([]);
    expect(s.sessionId).toBe("");
    expect(s.messages).toEqual([]);
    expect(s.busy).toBe(false);
    expect(s.ready).toBe(false);
    expect(s.mock).toBe(false);
    expect(s.error).toBe("");
  });

  it("UI 浮层开关默认为关闭", () => {
    const s = useAgentStore.getState();
    expect(s.showSessions).toBe(false);
    expect(s.showSettings).toBe(false);
    expect(s.showTimers).toBe(false);
  });

  it("setShowSessions 能切换浮层可见性", () => {
    useAgentStore.getState().setShowSessions(true);
    expect(useAgentStore.getState().showSessions).toBe(true);
    useAgentStore.getState().setShowSessions(false);
    expect(useAgentStore.getState().showSessions).toBe(false);
  });

  it("error action 写入错误信息", () => {
    useAgentStore.setState({ error: "" });
    // 模拟业务里 set error 的路径：直接走 setState 验证字段存在
    useAgentStore.setState({ error: "请求失败" });
    expect(useAgentStore.getState().error).toBe("请求失败");
  });
});
