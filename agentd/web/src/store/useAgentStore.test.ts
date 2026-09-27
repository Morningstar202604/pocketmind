// 纯函数单测：事件折叠 / 历史恢复 / 宽松 JSON。
// 这些函数不依赖网络与 React，是前端消息管线的核心。
import { describe, expect, it } from "vitest";
import {
  appendTextPart,
  applyEvent,
  restore,
  safeJson,
} from "./useAgentStore";
import type { Msg } from "../types";
import type { StoredMessage } from "../api";

describe("applyEvent：SSE 事件折叠进 assistant 消息", () => {
  const base: Msg = { id: "a1", role: "assistant", parts: [], done: false };

  it("text 事件追加新文本 part", () => {
    const m = applyEvent(base, { type: "text", text: "你好" });
    expect(m.parts).toHaveLength(1);
    expect(m.parts[0]).toEqual({ type: "text", text: "你好" });
  });

  it("连续 text 事件合并到同一个 part", () => {
    let m = applyEvent(base, { type: "text", text: "你" });
    m = applyEvent(m, { type: "text", text: "好" });
    expect(m.parts).toHaveLength(1);
    expect(m.parts[0]).toEqual({ type: "text", text: "你好" });
  });

  it("tool_start 新增 tool part，状态 running", () => {
    const m = applyEvent(base, {
      type: "tool_start",
      id: "t1",
      name: "shell",
      input: { cmd: "ls" },
    });
    expect(m.parts).toHaveLength(1);
    expect(m.parts[0]).toMatchObject({
      type: "tool",
      tool: { id: "t1", name: "shell", status: "running" },
    });
  });

  it("done 事件标记消息完成", () => {
    const m = applyEvent(base, { type: "done", stop_reason: "stop" });
    expect(m.done).toBe(true);
  });

  it("error 事件写入错误并标记完成", () => {
    const m = applyEvent(base, { type: "error", message: "连接断开" });
    expect(m.done).toBe(true);
    expect(m.error).toBe("连接断开");
  });
});

describe("appendTextPart：同类 part 合并", () => {
  it("text 后接 thinking 会新开 part", () => {
    const m: Msg = {
      id: "a1",
      role: "assistant",
      parts: [{ type: "text", text: "hi" }],
      done: false,
    };
    const m2 = appendTextPart(m, "thinking", "思考中");
    expect(m2.parts).toHaveLength(2);
  });
});

describe("safeJson：宽松解析", () => {
  it("合法 JSON 正常返回", () => {
    expect(safeJson('{"a":1}')).toEqual({ a: 1 });
  });
  it("坏 JSON 返回空对象而不抛错", () => {
    expect(safeJson("not json{")).toEqual({});
  });
});

describe("restore：历史消息重建", () => {
  it("user/assistant 交替重建为 Msg[]", () => {
    const stored: StoredMessage[] = [
      { role: "user", content: "列出文件", meta: {} },
      {
        role: "assistant",
        content: "这是文件列表",
        meta: { tool_calls: [{ id: "t1", name: "shell", args: '{"cmd":"ls"}' }] },
      },
      { role: "tool", content: '["a.txt"]', meta: { tool_call_id: "t1", status: "completed" } },
    ];
    const out = restore(stored);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    // assistant 消息应带上 tool part，且 result 被回填
    const toolPart = out[1].parts.find((p) => p.type === "tool");
    expect(toolPart).toBeDefined();
    if (toolPart?.type === "tool") {
      expect(toolPart.tool.status).toBe("completed");
      expect(toolPart.tool.result).toEqual(["a.txt"]);
    }
  });
});
