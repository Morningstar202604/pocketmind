// 消息 part 折叠 / 历史重建 等纯函数（可单测、无 React 依赖）。
import type { ChatEvent, StoredMessage } from "../api";
import { uid, type Msg, type ToolPart } from "../types";

export function appendTextPart(msg: Msg, kind: "text" | "thinking", text: string): Msg {
  const parts = [...msg.parts];
  const last = parts[parts.length - 1];
  if (last && last.type === kind) {
    parts[parts.length - 1] = { ...last, text: last.text + text };
  } else {
    parts.push({ type: kind, text });
  }
  return { ...msg, parts };
}

export function applyEvent(msg: Msg, ev: ChatEvent): Msg {
  switch (ev.type) {
    case "text":
      return appendTextPart(msg, "text", ev.text);
    case "thinking":
      return appendTextPart(msg, "thinking", ev.text);
    case "tool_start": {
      const tool: ToolPart = {
        id: ev.id,
        name: ev.name,
        input: ev.input,
        status: "running",
        // 记下本地开始时刻：后端不发耗时字段，前端自算即可（同机、精度足够）
        startedAt: Date.now(),
        startedFor: "running",
      };
      return { ...msg, parts: [...msg.parts, { type: "tool", tool }] };
    }
    case "approval":
      // 进入等待即作废计时起点：用户思考的时间不能算进"工具耗时"，
      // 否则一个 echo 会显示 12 秒。批准后由 onApproval 在重开 running 时重新起表；
      // 被拒绝则起点保持作废，耗时不显示。
      return {
        ...msg,
        parts: msg.parts.map((p) =>
          p.type === "tool" && p.tool.id === ev.id
            ? { ...p, tool: { ...p.tool, status: "waiting", summary: ev.summary, risk: ev.risk, startedAt: undefined } }
            : p
        ),
      };
    case "tool_update":
      return {
        ...msg,
        parts: msg.parts.map((p) => {
          if (p.type !== "tool" || p.tool.id !== ev.id) return p;
          const { startedAt, startedFor } = p.tool;
          let ms = p.tool.ms;
          // 只在首次结算时记耗时，避免后续 update 覆盖成更长区间。
          // 审批等待期间 startedAt 被作废（见 approval 分支），批准后由决策方重新起表；
          // 若执行段没有起表（如被直接拒绝、从未真正执行），耗时保持 undefined 不显示。
          if (ms === undefined && startedAt !== undefined && startedFor === p.tool.status) {
            ms = Date.now() - startedAt;
          }
          return {
            ...p,
            tool: {
              ...p.tool,
              status: ev.status,
              result: ev.result,
              undoable: ev.undoable,
              startedAt: undefined,
              startedFor: undefined,
              ms,
            },
          };
        }),
      };
    case "queued":
      return { ...msg, done: true, queued: true };
    case "plan":
      return {
        ...msg,
        parts: [...msg.parts, { type: "plan" as const, plan: ev.plan, tool_calls: ev.tool_calls ?? [] }],
      };
    case "done":
      return { ...msg, done: true };
    case "error":
      return { ...msg, done: true, error: ev.message };
    default:
      return msg;
  }
}

export function safeJson(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

export function restore(msgs: StoredMessage[]): Msg[] {
  const out: Msg[] = [];
  let currentAsst: Msg | null = null;
  for (const m of msgs) {
    if (m.role === "user") {
      currentAsst = null;
      out.push({ id: uid("u"), serverId: m.id, role: "user", parts: [{ type: "text", text: m.content }], done: true, createdAt: m.created_at });
    } else if (m.role === "assistant") {
      const msg: Msg = { id: uid("a"), serverId: m.id, role: "assistant", parts: [], done: true, createdAt: m.created_at };
      if (m.content.trim()) msg.parts.push({ type: "text", text: m.content });
      const tcs = (m.meta?.tool_calls as Array<{ id: string; name: string; args: string }> | undefined) ?? [];
      for (const tc of tcs) {
        msg.parts.push({
          type: "tool",
          tool: { id: tc.id, name: tc.name, input: safeJson(tc.args || "{}"), status: "completed" },
        });
      }
      out.push(msg);
      currentAsst = msg;
    } else if (m.role === "tool" && currentAsst) {
      const meta = m.meta as { tool_call_id?: string; status?: string };
      const target = currentAsst.parts.find((p) => p.type === "tool" && p.tool.id === meta.tool_call_id);
      if (target && target.type === "tool") {
        target.tool.result = safeJson(m.content);
        target.tool.status = meta.status === "denied" ? "denied" : meta.status === "failed" ? "failed" : "completed";
      }
    }
  }
  return out;
}

// uid 复用 types.ts 中的实现（已在此文件顶部导入）
