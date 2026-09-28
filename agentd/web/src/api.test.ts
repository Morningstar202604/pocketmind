// API 客户端契约测试：后端列表接口返回 {recipes:[...]} / {jobs:[...]} 包裹，
// 客户端必须解包成数组，避免 UI 层 .map/.length 崩溃。
import { describe, expect, it, vi, afterEach } from "vitest";
import { getRecipes, getJobs } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("API 列表接口解包", () => {
  it("getRecipes 从 {recipes:[...]} 解包为数组", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ recipes: [{ id: "battery_check", name: "低电量提醒" }] }),
    }));
    const rs = await getRecipes();
    expect(Array.isArray(rs)).toBe(true);
    expect(rs[0].id).toBe("battery_check");
  });

  it("getJobs 从 {jobs:[...]} 解包为数组", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ jobs: [{ id: "j1", name: "每日总结" }] }),
    }));
    const js = await getJobs();
    expect(Array.isArray(js)).toBe(true);
    expect(js[0].id).toBe("j1");
  });
});
