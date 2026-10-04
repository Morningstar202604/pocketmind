// SettingsPanel 契约回归：
// 1) 「保存」必须把面板里编辑的 MCP 列表一起提交（曾因只发 token/approval_timeout 而静默丢弃）
// 2) /api/settings 返回 401 时必须给出令牌输入口，而不是永远停在「加载中…」（自锁）
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
const setTokenMock = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual, // 保留 ApiError / isUnauthorized 的真实实现
    api: {
      get: (path: string) => get(path),
      put: (path: string, body: unknown) => put(path, body),
      post: vi.fn(() => Promise.resolve({})),
      del: vi.fn(() => Promise.resolve({})),
    },
    setToken: (t: string) => setTokenMock(t),
    getToken: () => "",
  };
});

import { ApiError } from "../api";
import { SettingsPanel } from "./SettingsPanel";

const SETTINGS = {
  llm: {
    provider: "volc",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    api_key: "****",
    model: "doubao-mini",
    temperature: 0.7,
    max_tokens: 4096,
  },
  permission_mode: "approve",
  agent_mode: "act",
  server: {
    token: "",
    approval_timeout: 120,
    mcp: [{ name: "本地demo", command: "python3", args: ["tests/mcp_demo_server.py"] }],
  },
  user_prefs: "",
};

const renderPanel = () =>
  render(
    <SettingsPanel
      onClose={vi.fn()}
      mock
      theme="dark"
      onTheme={vi.fn()}
      onExport={vi.fn()}
    />
  );

beforeEach(() => {
  get.mockReset();
  put.mockReset();
  setTokenMock.mockReset();
  put.mockResolvedValue({ ok: true });
  get.mockImplementation((path: string) => {
    if (path === "/api/settings") return Promise.resolve(SETTINGS);
    if (path === "/api/providers") return Promise.resolve({ providers: [] });
    if (path === "/api/tools") return Promise.resolve({ tools: [] });
    return Promise.resolve({});
  });
});

describe("SettingsPanel 保存契约", () => {
  it("编辑 MCP 列表后保存，请求体必须带上清洗后的 mcp", async () => {
    renderPanel();
    const box = (await screen.findByPlaceholderText(/本地demo/)) as HTMLTextAreaElement;
    expect(box.value).toContain("python3");
    fireEvent.change(box, { target: { value: "文件系统 | npx | -y server-fs /sdcard" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const body = put.mock.calls[0][1] as { server: Record<string, unknown> };
    expect(body.server.mcp).toEqual([
      { name: "文件系统", command: "npx", args: ["-y", "server-fs", "/sdcard"] },
    ]);
  });

  it("未编辑 MCP 时保存，原有的 mcp 不能被丢掉", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "保存" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const body = put.mock.calls[0][1] as { server: { mcp: unknown[] } };
    expect(body.server.mcp).toEqual(SETTINGS.server.mcp);
  });
});

describe("SettingsPanel 令牌门禁", () => {
  it("401 时显示令牌输入口并可解锁后加载设置", async () => {
    get.mockImplementation((path: string) => {
      if (path === "/api/settings") return Promise.reject(new ApiError("需要访问令牌", 401));
      if (path === "/api/providers") return Promise.resolve({ providers: [] });
      if (path === "/api/tools") return Promise.reject(new ApiError("需要访问令牌", 401));
      return Promise.resolve({});
    });
    renderPanel();
    expect(await screen.findByText("需要访问令牌")).toBeInTheDocument();
    expect(screen.queryByText("加载中…")).not.toBeInTheDocument();

    // 第二次拉取放行：模拟"填对令牌后"的正常响应
    get.mockImplementation((path: string) => {
      if (path === "/api/settings") return Promise.resolve(SETTINGS);
      if (path === "/api/providers") return Promise.resolve({ providers: [] });
      return Promise.resolve({ tools: [] });
    });
    fireEvent.change(screen.getByPlaceholderText(/服务令牌/), { target: { value: "abc123" } });
    fireEvent.click(screen.getByRole("button", { name: "解锁设置" }));

    await waitFor(() => expect(setTokenMock).toHaveBeenCalledWith("abc123"));
    expect(await screen.findByRole("button", { name: "保存" })).toBeInTheDocument();
  });

  it("非 401 的失败要显示错误文案，不静默卡在加载中", async () => {
    get.mockImplementation((path: string) => {
      if (path === "/api/settings") return Promise.reject(new ApiError("服务重启中", 503));
      return Promise.resolve({ providers: [], tools: [] });
    });
    renderPanel();
    expect(await screen.findByText("服务重启中")).toBeInTheDocument();
  });
});
