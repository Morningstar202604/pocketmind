// 全局状态仓库（Zustand）：集中管理会话/消息/设置/审批/流式状态，消除 props 层层透传。
//
// 设计说明：
// - 响应式状态（sessions/messages/busy/error 等）放在 store 里，组件用 useXxxStore(s => s.field) 订阅。
// - 不需要触发重渲染的运行时句柄（AbortController、发送队列、运行中标记）用模块级变量持有，
//   避免放进 state 导致多余渲染。
// - 所有 action 保持改造前 App.tsx 的行为完全一致。

import { create } from "zustand";
import {
  api,
  exportAll,
  renameSession,
  undoToolCall,
  type ChatEvent,
  type Session,
  type StoredMessage,
} from "../api";
import { getThemePref, setThemePref, type ThemePref } from "../theme";
import { uid, type Msg, type Part, type ToolPart } from "../types";

/* ---------- 事件 → 消息状态（纯函数，可单测） ---------- */

/** 把一段流式文本追加到消息末尾的同类型 part（text/thinking），否则新开一个 part。 */
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

/** 把后端 SSE 事件折叠进一条正在生成的 assistant 消息。 */
export function applyEvent(msg: Msg, ev: ChatEvent): Msg {
  switch (ev.type) {
    case "text":
      return appendTextPart(msg, "text", ev.text);
    case "thinking":
      return appendTextPart(msg, "thinking", ev.text);
    case "tool_start": {
      const tool: ToolPart = { id: ev.id, name: ev.name, input: ev.input, status: "running" };
      return { ...msg, parts: [...msg.parts, { type: "tool", tool }] };
    }
    case "approval":
      return {
        ...msg,
        parts: msg.parts.map((p) =>
          p.type === "tool" && p.tool.id === ev.id
            ? { ...p, tool: { ...p.tool, status: "waiting", summary: ev.summary, risk: ev.risk } }
            : p
        ),
      };
    case "tool_update":
      return {
        ...msg,
        parts: msg.parts.map((p) =>
          p.type === "tool" && p.tool.id === ev.id
            ? { ...p, tool: { ...p.tool, status: ev.status, result: ev.result, undoable: ev.undoable } }
            : p
        ),
      };
    case "queued":
      return { ...msg, done: true, queued: true };
    case "plan":
      // Plan 事件：追加一张执行计划卡片（纯展示，等待用户批准后才真正执行）
      return {
        ...msg,
        parts: [
          ...msg.parts,
          { type: "plan" as const, plan: ev.plan, tool_calls: ev.tool_calls ?? [] },
        ],
      };
    case "done":
      return { ...msg, done: true };
    case "error":
      return { ...msg, done: true, error: ev.message };
    default:
      return msg;
  }
}

/* ---------- 历史恢复（纯函数，可单测） ---------- */

/** 宽松 JSON 解析：失败返回空对象，避免坏数据炸掉整屏。 */
export function safeJson(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

/** 把后端存储的消息序列（user/assistant/tool 交替）重建为前端渲染用的 Msg[]。 */
export function restore(msgs: StoredMessage[]): Msg[] {
  const out: Msg[] = [];
  let currentAsst: Msg | null = null;
  for (const m of msgs) {
    if (m.role === "user") {
      currentAsst = null;
      out.push({ id: uid("u"), role: "user", parts: [{ type: "text", text: m.content }], done: true });
    } else if (m.role === "assistant") {
      const msg: Msg = { id: uid("a"), role: "assistant", parts: [], done: true };
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
        target.tool.status =
          meta.status === "denied" ? "denied" : meta.status === "failed" ? "failed" : "completed";
      }
    }
  }
  return out;
}

/* ---------- 非响应式运行时句柄（不触发渲染） ---------- */

let abortController: AbortController | null = null;
// 前端消息队列：同一会话连续发送时排队执行（服务端亦有队列作为 API 层保险）
let sendQueue: Array<{ text: string; aid: string }> = [];
let running = false;

/* ---------- Store 类型 ---------- */

export interface AgentStore {
  // 会话与消息
  sessions: Session[];
  sessionId: string;
  messages: Msg[];
  // 流式/连接状态
  busy: boolean;
  ready: boolean;
  mock: boolean;
  error: string;
  // 浮层开关
  showSessions: boolean;
  showSettings: boolean;
  showTimers: boolean;
  // 主题与撤销
  theme: ThemePref;
  undoingId: string | null;
  // 智能体工作模式：plan = 先出计划待批准；act = 直接执行
  agentMode: "plan" | "act";

  // 生命周期
  init: () => Promise<void>;
  refreshSessions: () => Promise<Session[]>;
  loadSession: (sid: string) => Promise<void>;

  // 发送/停止
  send: (text: string) => Promise<void>;
  stop: () => void;

  // Plan/Act 模式
  setAgentMode: (m: "plan" | "act") => void;
  /** 批准某条计划卡片：切到 act 模式，把触发该计划的用户原话重发一轮。 */
  approvePlan: (assistantMsgId: string) => Promise<void>;
  /** 修改计划：切回输入框聚焦，让用户补充/调整后重发。 */
  editPlan: (assistantMsgId: string) => void;

  // 会话操作
  onNewSession: () => Promise<void>;
  onDeleteSession: (sid: string) => Promise<void>;
  onRenameSession: (sid: string, title: string) => Promise<void>;

  // 审批 / 撤销 / 导出
  onApproval: (toolCallId: string, decision: "allow_once" | "allow_always" | "deny") => Promise<void>;
  onUndo: (toolCallId: string) => Promise<void>;
  onExport: () => Promise<void>;

  // UI 开关
  setShowSessions: (v: boolean) => void;
  setShowSettings: (v: boolean) => void;
  setShowTimers: (v: boolean) => void;
  setTheme: (t: ThemePref) => void;
}

export const useAgentStore = create<AgentStore>((set, get) => {
  /** 跑单条 SSE 流式对话：事件折叠进 id=aid 的 assistant 占位消息。 */
  const doRun = async (text: string, aid: string) => {
    const ac = new AbortController();
    abortController = ac;
    const sessionId = get().sessionId;
    try {
      await api.sseChat(sessionId, text, (ev) => {
        // 后端可能在首帧才返回真实 session_id（新建会话时），同步更新
        if (ev.type === "session" && ev.session_id !== get().sessionId) {
          set({ sessionId: ev.session_id });
        }
        // 开始产出时清除「排队中」标记（plan 事件也算正式开始产出）
        if (ev.type === "text" || ev.type === "tool_start" || ev.type === "thinking" || ev.type === "plan") {
          set((s) => ({ messages: s.messages.map((m) => (m.id === aid ? { ...m, queued: false } : m)) }));
        }
        set((s) => ({ messages: s.messages.map((m) => (m.id === aid ? applyEvent(m, ev) : m)) }));
      }, ac.signal);
    } catch (e) {
      const err = e as Error;
      if (err.name === "AbortError") {
        set((s) => ({ messages: s.messages.map((m) => (m.id === aid ? { ...m, done: true } : m)) }));
      } else {
        set({ error: err.message || "请求失败" });
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === aid ? { ...m, done: true, error: err.message || "请求失败" } : m
          ),
        }));
      }
    } finally {
      // 兜底：SSE 正常结束但未收到 done 事件时（连接中途断开/后端重启），
      // 标记消息完成，避免永久停在"正在思考…"
      set((s) => ({ messages: s.messages.map((m) => (m.id === aid && !m.done ? { ...m, done: true } : m)) }));
      abortController = null;
    }
  };

  return {
    // ---------- 初始状态 ----------
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
    theme: getThemePref(),
    undoingId: null,
    // 默认 act（直接执行）；init 时若后端保存过 plan 模式会再纠正
    agentMode: "act",

    // ---------- 生命周期 ----------
    async init() {
      try {
        const h = await api.get<{ ok: boolean; ready: boolean; mock: boolean }>("/api/health");
        set({ ready: h.ready, mock: h.mock });
      } catch {
        set({ ready: false });
      }
      // 读取后端持久化的智能体模式（plan/act），失败则保持默认 act
      try {
        const s = await api.get<{ agent_mode?: "plan" | "act" }>("/api/settings");
        if (s.agent_mode === "plan" || s.agent_mode === "act") set({ agentMode: s.agent_mode });
      } catch {
        /* 后端尚未支持该字段时静默忽略 */
      }
      const list = await get().refreshSessions();
      if (list.length === 0) {
        const s = await api.post<{ session_id: string }>("/api/sessions", {});
        await get().loadSession(s.session_id);
      } else {
        await get().loadSession(list[0].id);
      }
    },

    async refreshSessions() {
      try {
        const list = await api.get<Session[]>("/api/sessions");
        set({ sessions: list });
        return list;
      } catch {
        return [];
      }
    },

    async loadSession(sid: string) {
      set({ sessionId: sid, messages: [], error: "" });
      try {
        const msgs = await api.get<StoredMessage[]>(`/api/sessions/${sid}/messages`);
        // 竞态保护：期间用户已切到别的会话，则丢弃这次过期响应
        const cur = get().sessionId;
        if (cur === sid) set({ messages: restore(msgs) });
      } catch {
        /* 会话可能已删 */
      }
    },

    // ---------- 发送 / 停止 ----------
    async send(text: string) {
      const t = text.trim();
      if (!t || !get().sessionId) return;
      const aid = uid("a");
      // 队列里已有在等/正在执行的请求时，新消息立即显示并排队
      const queued = running || sendQueue.length > 0;
      set((s) => ({
        messages: [
          ...s.messages,
          { id: uid("u"), role: "user", parts: [{ type: "text", text: t }], done: true },
          { id: aid, role: "assistant", parts: [], done: false, queued },
        ],
      }));
      sendQueue.push({ text: t, aid });
      if (running) return; // 正在跑，由循环消化队列
      running = true;
      set({ busy: true, error: "" });
      while (sendQueue.length > 0) {
        const item = sendQueue.shift()!;
        await doRun(item.text, item.aid);
        void get().refreshSessions();
      }
      running = false;
      set({ busy: false });
      abortController = null;
    },

    stop() {
      abortController?.abort();
      // 停止：同时清空排队中的消息，并移除对应占位
      sendQueue = [];
      set((s) => ({
        messages: s.messages.map((m) => (m.queued ? { ...m, done: true, error: "已停止" } : m)),
      }));
      void api.post("/api/stop", { session_id: get().sessionId }).catch(() => {});
    },

    // ---------- 会话操作 ----------
    async onNewSession() {
      const s = await api.post<{ session_id: string }>("/api/sessions", {});
      set({ showSessions: false });
      await get().loadSession(s.session_id);
      void get().refreshSessions();
    },

    async onDeleteSession(sid: string) {
      await api.del(`/api/sessions/${sid}`).catch(() => {});
      const list = await get().refreshSessions();
      if (sid === get().sessionId) {
        if (list.length === 0) {
          const s = await api.post<{ session_id: string }>("/api/sessions", {});
          await get().loadSession(s.session_id);
        } else {
          await get().loadSession(list[0].id);
        }
      }
    },

    async onRenameSession(sid: string, title: string) {
      if (!title.trim()) return;
      await renameSession(sid, title.trim()).catch(() => {});
      void get().refreshSessions();
    },

    // ---------- 审批 / 撤销 / 导出 ----------
    async onApproval(toolCallId, decision) {
      try {
        await api.post("/api/approval", {
          session_id: get().sessionId,
          tool_call_id: toolCallId,
          decision,
        });
        if (decision !== "deny") {
          set((s) => ({
            messages: s.messages.map((m) => ({
              ...m,
              parts: m.parts.map((p) =>
                p.type === "tool" && p.tool.id === toolCallId
                  ? { ...p, tool: { ...p.tool, status: "running" as const } }
                  : p
              ),
            })),
          }));
        }
      } catch (e) {
        set({ error: (e as Error).message || "审批提交失败" });
      }
    },

    async onUndo(toolCallId: string) {
      if (!get().sessionId) return;
      set({ undoingId: toolCallId });
      try {
        const r = await undoToolCall(get().sessionId, toolCallId);
        if (r.ok && r.restored) {
          set((s) => ({
            messages: s.messages.map((m) => ({
              ...m,
              parts: m.parts.map((p) =>
                p.type === "tool" && p.tool.id === toolCallId
                  ? { ...p, tool: { ...p.tool, undoable: false, result: { ...p.tool.result, "已撤销": r.restored } } }
                  : p
              ),
            })),
          }));
          set({ error: "" });
          window.alert("已还原：" + r.restored);
        } else {
          set({ error: r.error || "撤销失败" });
        }
      } catch (e) {
        set({ error: (e as Error).message || "撤销失败" });
      } finally {
        set({ undoingId: null });
      }
    },

    async onExport() {
      try {
        const data = await exportAll();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `pocket-agent-backup-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        set({ error: (e as Error).message || "导出失败" });
      }
    },

    // ---------- UI 开关 ----------
    setShowSessions: (v) => set({ showSessions: v }),
    setShowSettings: (v) => set({ showSettings: v }),
    setShowTimers: (v) => set({ showTimers: v }),
    setTheme: (t) => {
      set({ theme: t });
      setThemePref(t);
    },

    // ---------- Plan/Act 模式 ----------
    setAgentMode: (m) => {
      // 乐观更新本地状态；后端 settings.save 是合并写入，局部 PUT 不会冲掉其他配置
      set({ agentMode: m });
      void api.put("/api/settings", { agent_mode: m }).catch(() => {});
    },

    async approvePlan(assistantMsgId: string) {
      const list = get().messages;
      const idx = list.findIndex((m) => m.id === assistantMsgId);
      // 向上找到触发该计划的最近一条用户消息，批准后原样重发一轮
      let userText = "";
      for (let i = idx - 1; i >= 0; i--) {
        const m = list[i];
        if (m.role === "user") {
          userText = m.parts
            .filter((p): p is Extract<Part, { type: "text" }> => p.type === "text")
            .map((p) => p.text)
            .join("\n");
          break;
        }
      }
      if (!userText) return;
      // 卡片置为「已批准」态：按钮变展示态，防止重复点击
      set((st) => ({
        messages: st.messages.map((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                parts: m.parts.map((p) => (p.type === "plan" ? { ...p, approved: true } : p)),
              }
            : m
        ),
      }));
      // 切到 act 模式（同步持久化），再把用户原话发出去真正执行
      get().setAgentMode("act");
      await get().send(userText);
    },

    editPlan: (assistantMsgId: string) => {
      // 仅把输入框聚焦回来，让用户补充/修改后重发；卡片保留在历史里
      void assistantMsgId; // 预留：未来可针对该计划做上下文回填
      window.dispatchEvent(new CustomEvent("agent:focus-composer"));
    },
  };
});

// 供组件订阅消息 parts 的小工具（保持原 partsFor(m) 语义）
export type { Part };
