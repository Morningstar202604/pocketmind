// App 组件冒烟测试：渲染不崩溃即可。
// 通过 mock api 模块避免真实网络请求。
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// mock 掉整个 api 模块：init 流程会依次打 /api/health、/api/sessions、POST /api/sessions、GET messages
vi.mock("./api", () => {
  return {
    api: {
      get: vi.fn((path: string) => {
        if (path === "/api/health") {
          return Promise.resolve({ ok: true, ready: true, mock: false });
        }
        if (path === "/api/sessions") {
          return Promise.resolve([]); // 无会话 → 触发新建
        }
        if (path.endsWith("/messages")) {
          return Promise.resolve([]);
        }
        return Promise.resolve({});
      }),
      post: vi.fn((path: string) => {
        if (path === "/api/sessions") return Promise.resolve({ session_id: "new-sid" });
        return Promise.resolve({});
      }),
      put: vi.fn(() => Promise.resolve({})),
      del: vi.fn(() => Promise.resolve({})),
      sseChat: vi.fn(() => Promise.resolve()),
    },
    exportAll: vi.fn(() => Promise.resolve({})),
    renameSession: vi.fn(() => Promise.resolve({})),
    undoToolCall: vi.fn(() => Promise.resolve({})),
  };
});

import App from "./App";
import { useAgentStore } from "./store/useAgentStore";

describe("App 冒烟测试", () => {
  beforeEach(() => {
    // 重置 store，避免上一个用例残留状态
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
    });
  });

  it("渲染不崩溃，并显示品牌名", async () => {
    render(<App />);
    // 顶栏品牌名一定在
    expect(await screen.findByText("口袋智灵")).toBeInTheDocument();
  });

  it("ready 状态下顶栏显示「已就绪」", async () => {
    render(<App />);
    expect(await screen.findByText("已就绪")).toBeInTheDocument();
  });
});
