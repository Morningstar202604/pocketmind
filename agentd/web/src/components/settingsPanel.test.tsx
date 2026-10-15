// SettingsPanel 契约回归：
// 1) 「保存」必须把面板里编辑的 MCP 列表一起提交（曾因只发 token/approval_timeout 而静默丢弃）
// 2) /api/settings 返回 401 时必须给出令牌输入口，而不是永远停在「加载中…」（自锁）
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
const del = vi.fn();
const setTokenMock = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual, // 保留 ApiError / isUnauthorized 的真实实现
    api: {
      get: (path: string) => get(path),
      put: (path: string, body: unknown) => put(path, body),
      post: vi.fn(() => Promise.resolve({})),
      del: (path: string) => del(path),
    },
    // 组件使用的是 helper 风格 API（listKb/getSkills/...），它们内部引用真实 api 对象，
    // 因此 mock 必须把这些 helper 也接到同一组 spy 上，否则测试会真的去发请求并静默失败。
    listKb: () => get("/api/kb").then((d: { items?: unknown[] }) => d.items ?? []),
    deleteKb: (id: number) => del(`/api/kb/${id}`),
    getSkills: () => get("/api/skills"),
    getProfile: () => get("/api/memory/profile"),
    putProfile: (body: unknown) => put("/api/memory/profile", body),
    getApprovalRules: () => get("/api/approvals"),
    clearApprovalRules: (scope: string) =>
      del(`/api/approvals?scope=${scope}`).then(() => ({
        ok: true,
        rules: { global: [], deny: [], sessions: {} },
      })),
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

/**
 * 展开指定分组后再断言。
 * 设置面板改为折叠式后，内容默认不渲染；测试关注的是"能力是否具备"，
 * 因此先按标题展开对应分组，而不是把断言放宽。
 */
const openGroup = async (name: RegExp) => {
  const head = await screen.findByRole("button", { name });
  if (head.getAttribute("aria-expanded") === "false") fireEvent.click(head);
  return head;
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
    await openGroup(/MCP 服务器/);
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
    await openGroup(/MCP 服务器/);
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

// ── 能力面：运行参数 / 上下文与记忆 / 知识库 / 技能 必须可见可调 ──
describe("SettingsPanel 能力面", () => {
  const withCapabilities = () => {
    get.mockImplementation((path: string) => {
      if (path === "/api/settings")
        return Promise.resolve({
          ...SETTINGS,
          agent: { max_tool_chain_depth: 20, max_tool_retries: 2 },
          context: { budget_tokens: 0, auto_compress: true, compress_threshold: 0.8 },
          memory: {
            auto_summarize: true,
            summarize_every_n_turns: 10,
            summary_max_chars: 400,
            search_limit: 5,
            fts_page_size: 50,
            retention_days: 90,
          },
        });
      if (path === "/api/providers") return Promise.resolve({ providers: [] });
      if (path === "/api/tools") return Promise.resolve({ tools: [] });
      if (path === "/api/kb")
        return Promise.resolve({ items: [{ id: 3, title: "保修卡", chars: 42, created_at: 1 }] });
      if (path === "/api/skills")
        return Promise.resolve({ skills: [{ name: "battery-care", description: "电池保养" }], dir: "/x" });
      if (path === "/api/memory/profile")
        return Promise.resolve({ enabled: true, user: "", facts: "", journal_lines: 0 });
      if (path === "/api/approvals") return Promise.resolve({ global: [], deny: [], sessions: {} });
      return Promise.resolve({});
    });
  };

  it("四个能力组都渲染（分组呈现而非散落）", async () => {
    withCapabilities();
    renderPanel();
    expect(await screen.findByText("运行参数")).toBeInTheDocument();
    expect(screen.getByText("上下文与记忆")).toBeInTheDocument();
    expect(screen.getByText("知识库")).toBeInTheDocument();
    expect(screen.getByText("技能")).toBeInTheDocument();
  });

  it("保存时 agent / context / memory 三块必须一起提交", async () => {
    withCapabilities();
    renderPanel();
    await openGroup(/运行参数/);
    const depth = (await screen.findByLabelText(/工具调用链上限/)) as HTMLInputElement;
    fireEvent.change(depth, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const body = put.mock.calls[0][1] as {
      agent: { max_tool_chain_depth: number };
      context: Record<string, unknown>;
      memory: Record<string, unknown>;
    };
    expect(body.agent.max_tool_chain_depth).toBe(7);
    expect(body.context).toBeDefined();
    expect(body.memory).toBeDefined();
  });

  it("知识库条目可见，删除走二次确认（防误删用户资料）", async () => {
    withCapabilities();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPanel();
    await openGroup(/知识库/);
    expect(await screen.findByText("保修卡")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("技能面板只读展示（不提供在线安装入口）", async () => {
    withCapabilities();
    renderPanel();
    await openGroup(/技能/);
    expect(await screen.findByText("battery-care")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /安装技能/ })).toBeNull();
  });
});
