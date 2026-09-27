import { useCallback, useEffect, useRef, useState } from "react";
import { api, exportAll, renameSession, undoToolCall, type ChatEvent, type Session, type StoredMessage } from "./api";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { IconGear, IconSessions, IconTimer } from "./components/icons";
import { MessageItem } from "./components/MessageItem";
import { PocketLogo } from "./components/PocketLogo";
import { SessionList } from "./components/SessionList";
import { SettingsPanel } from "./components/SettingsPanel";
import { TimerPanel } from "./components/TimerPanel";
import { getThemePref, setThemePref, type ThemePref } from "./theme";
import { uid, type Msg, type Part, type ToolPart } from "./types";

/* ---------- 事件 → 消息状态 ---------- */

function appendTextPart(msg: Msg, kind: "text" | "thinking", text: string): Msg {
  const parts = [...msg.parts];
  const last = parts[parts.length - 1];
  if (last && last.type === kind) {
    parts[parts.length - 1] = { ...last, text: last.text + text };
  } else {
    parts.push({ type: kind, text });
  }
  return { ...msg, parts };
}

function applyEvent(msg: Msg, ev: ChatEvent): Msg {
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
    case "done":
      return { ...msg, done: true };
    case "error":
      return { ...msg, done: true, error: ev.message };
    default:
      return msg;
  }
}

/* ---------- 历史恢复 ---------- */

function safeJson(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

function restore(msgs: StoredMessage[]): Msg[] {
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

/* ---------- App ---------- */

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [mock, setMock] = useState(false);
  const [error, setError] = useState("");
  const [showSessions, setShowSessions] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showTimers, setShowTimers] = useState(false);
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<HTMLDivElement>(null);

  // system 模式下跟随系统主题切换
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const fn = () => setThemePref("system");
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [theme]);

  // 消息列表自动滚动到底部（流式输出时跟随）
  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.get<Session[]>("/api/sessions");
      setSessions(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  const loadSession = useCallback(async (sid: string) => {
    setSessionId(sid);
    setMessages([]);
    setError("");
    try {
      const msgs = await api.get<StoredMessage[]>(`/api/sessions/${sid}/messages`);
      // 竞态保护：期间用户已切到别的会话，则丢弃这次过期响应
      setSessionId((cur) => {
        if (cur !== sid) return cur;
        setMessages(restore(msgs));
        return cur;
      });
    } catch {
      /* 会话可能已删 */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const h = await api.get<{ ok: boolean; ready: boolean; mock: boolean }>("/api/health");
        setReady(h.ready);
        setMock(h.mock);
      } catch {
        setReady(false);
      }
      const list = await refreshSessions();
      if (list.length === 0) {
        const s = await api.post<{ session_id: string }>("/api/sessions", {});
        void loadSession(s.session_id);
      } else {
        void loadSession(list[0].id);
      }
    })();
  }, [loadSession, refreshSessions]);

  const stop = () => {
    abortRef.current?.abort();
    // 停止：同时清空排队中的消息，并移除对应占位
    sendQueueRef.current = [];
    setMessages((prev) => prev.map((m) => (m.queued ? { ...m, done: true, error: "已停止" } : m)));
    void api.post("/api/stop", { session_id: sessionId }).catch(() => {});
  };

  // 前端消息队列：同一会话连续发送时排队执行（服务端亦有队列作为 API 层保险）
  const sendQueueRef = useRef<Array<{ text: string; aid: string }>>([]);
  const runningRef = useRef(false);

  const doRun = async (text: string, aid: string) => {
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await api.sseChat(
        sessionId,
        text,
        (ev) => {
          if (ev.type === "session" && ev.session_id !== sessionId) setSessionId(ev.session_id);
          // 开始产出时清除「排队中」标记
          if (ev.type === "text" || ev.type === "tool_start" || ev.type === "thinking") {
            setMessages((prev) => prev.map((m) => (m.id === aid ? { ...m, queued: false } : m)));
          }
          setMessages((prev) => prev.map((m) => (m.id === aid ? applyEvent(m, ev) : m)));
        },
        ac.signal
      );
    } catch (e) {
      const err = e as Error;
      if (err.name === "AbortError") {
        setMessages((prev) => prev.map((m) => (m.id === aid ? { ...m, done: true } : m)));
      } else {
        setError(err.message || "请求失败");
        setMessages((prev) =>
          prev.map((m) => (m.id === aid ? { ...m, done: true, error: err.message || "请求失败" } : m))
        );
      }
    } finally {
      // 兜底：SSE 正常结束但未收到 done 事件时（如连接中途断开/后端重启），
      // 标记消息完成，避免永久停在"正在思考…"
      setMessages((prev) => prev.map((m) => (m.id === aid && !m.done ? { ...m, done: true } : m)));
      abortRef.current = null;
    }
  };

  const send = async (text: string) => {
    const t = text.trim();
    if (!t || !sessionId) return;
    const aid = uid("a");
    // 队列里已有在等/正在执行的请求时，新消息立即显示并排队
    const queued = runningRef.current || sendQueueRef.current.length > 0;
    setMessages((prev) => [
      ...prev,
      { id: uid("u"), role: "user", parts: [{ type: "text", text: t }], done: true },
      { id: aid, role: "assistant", parts: [], done: false, queued },
    ]);
    sendQueueRef.current.push({ text: t, aid });
    if (runningRef.current) return; // 正在跑，由循环消化队列
    runningRef.current = true;
    setBusy(true);
    setError("");
    while (sendQueueRef.current.length > 0) {
      const item = sendQueueRef.current.shift()!;
      await doRun(item.text, item.aid);
      void refreshSessions();
    }
    runningRef.current = false;
    setBusy(false);
    abortRef.current = null;
  };

  const onNewSession = async () => {
    const s = await api.post<{ session_id: string }>("/api/sessions", {});
    setShowSessions(false);
    void loadSession(s.session_id);
    void refreshSessions();
  };

  const onDeleteSession = async (sid: string) => {
    await api.del(`/api/sessions/${sid}`).catch(() => {});
    const list = await refreshSessions();
    if (sid === sessionId) {
      if (list.length === 0) {
        const s = await api.post<{ session_id: string }>("/api/sessions", {});
        void loadSession(s.session_id);
      } else {
        void loadSession(list[0].id);
      }
    }
  };

  const onApproval = async (toolCallId: string, decision: "allow_once" | "allow_always" | "deny") => {
    try {
      await api.post("/api/approval", { session_id: sessionId, tool_call_id: toolCallId, decision });
      if (decision !== "deny") {
        setMessages((prev) =>
          prev.map((m) => ({
            ...m,
            parts: m.parts.map((p) =>
              p.type === "tool" && p.tool.id === toolCallId
                ? { ...p, tool: { ...p.tool, status: "running" as const } }
                : p
            ),
          }))
        );
      }
    } catch (e) {
      setError((e as Error).message || "审批提交失败");
    }
  };

  // 撤销一次文件类危险操作（执行前已自动备份）
  const [undoingId, setUndoingId] = useState<string | null>(null);
  const onUndo = async (toolCallId: string) => {
    if (!sessionId) return;
    setUndoingId(toolCallId);
    try {
      const r = await undoToolCall(sessionId, toolCallId);
      if (r.ok && r.restored) {
        setMessages((prev) =>
          prev.map((m) => ({
            ...m,
            parts: m.parts.map((p) =>
              p.type === "tool" && p.tool.id === toolCallId
                ? { ...p, tool: { ...p.tool, undoable: false, result: { ...p.tool.result, "已撤销": r.restored } } }
                : p
            ),
          }))
        );
        setError("");
        window.alert("已还原：" + r.restored);
      } else {
        setError(r.error || "撤销失败");
      }
    } catch (e) {
      setError((e as Error).message || "撤销失败");
    } finally {
      setUndoingId(null);
    }
  };

  const onExport = async () => {
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
      setError((e as Error).message || "导出失败");
    }
  };

  const onRenameSession = async (sid: string, title: string) => {
    if (!title.trim()) return;
    await renameSession(sid, title.trim()).catch(() => {});
    void refreshSessions();
  };

  const partsFor = (m: Msg): Part[] => m.parts;

  return (
    <div className="app">
      <header className="topbar">
        <button className="icon-btn" onClick={() => setShowSessions(true)} title="会话列表" aria-label="会话列表">
          <IconSessions />
        </button>
        <div className="brand">
          <PocketLogo size={38} />
          <div>
            <h1>口袋 Agent</h1>
            <p>{mock ? "离线演示模式" : "本地智能体 · 数据只在你设备上"}</p>
          </div>
        </div>
        <div className="topbar-right">
          <span className={`pill ${ready ? "ok" : "warn"}`}>{ready ? "已就绪" : "未配置"}</span>
          <button className="icon-btn" onClick={() => setShowTimers(true)} title="定时任务" aria-label="定时任务">
            <IconTimer />
          </button>
          <button className="icon-btn" onClick={() => setShowSettings(true)} title="设置" aria-label="设置">
            <IconGear />
          </button>
        </div>
      </header>

      <main className="chat">
        {messages.length === 0 ? (
          <EmptyState onPick={(t) => void send(t)} ready={ready} />
        ) : (
          <div className="msgs" ref={msgsRef}>
            {messages.map((m) => (
              <MessageItem
                key={m.id}
                msg={m}
                parts={partsFor(m)}
                onApproval={onApproval}
                onUndo={onUndo}
                undoingId={undoingId}
              />
            ))}
          </div>
        )}
      </main>

      <Composer busy={busy} ready={ready} onSend={(t) => void send(t)} onStop={stop} error={error} />

      {showSessions && (
        <SessionList
          sessions={sessions}
          currentId={sessionId}
          onClose={() => setShowSessions(false)}
          onPick={(sid) => {
            setShowSessions(false);
            void loadSession(sid);
          }}
          onNew={onNewSession}
          onDelete={onDeleteSession}
          onRename={onRenameSession}
        />
      )}
      {showSettings && (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          mock={mock}
          theme={theme}
          onTheme={(t) => {
            setTheme(t);
            setThemePref(t);
          }}
          onExport={() => void onExport()}
        />
      )}
      {showTimers && <TimerPanel onClose={() => setShowTimers(false)} />}
    </div>
  );
}
