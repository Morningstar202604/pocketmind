import { useEffect, useRef } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { IconGear, IconSessions, IconTimer } from "./components/icons";
import { MessageItem } from "./components/MessageItem";
import { PocketLogo } from "./components/PocketLogo";
import { SessionList } from "./components/SessionList";
import { SettingsPanel } from "./components/SettingsPanel";
import { TimerPanel } from "./components/TimerPanel";
import { useAgentStore } from "./store/useAgentStore";
import { applyTheme } from "./theme";

/* ---------- App ---------- */

export default function App() {
  // 从 Zustand store 订阅状态与 action（消除 props drilling）
  const sessions = useAgentStore((s) => s.sessions);
  const sessionId = useAgentStore((s) => s.sessionId);
  const messages = useAgentStore((s) => s.messages);
  const busy = useAgentStore((s) => s.busy);
  const ready = useAgentStore((s) => s.ready);
  const mock = useAgentStore((s) => s.mock);
  const error = useAgentStore((s) => s.error);
  const showSessions = useAgentStore((s) => s.showSessions);
  const showSettings = useAgentStore((s) => s.showSettings);
  const showTimers = useAgentStore((s) => s.showTimers);
  const theme = useAgentStore((s) => s.theme);
  const undoingId = useAgentStore((s) => s.undoingId);

  const init = useAgentStore((s) => s.init);
  const loadSession = useAgentStore((s) => s.loadSession);
  const send = useAgentStore((s) => s.send);
  const stop = useAgentStore((s) => s.stop);
  const onNewSession = useAgentStore((s) => s.onNewSession);
  const onDeleteSession = useAgentStore((s) => s.onDeleteSession);
  const onApproval = useAgentStore((s) => s.onApproval);
  const onUndo = useAgentStore((s) => s.onUndo);
  const onExport = useAgentStore((s) => s.onExport);
  const onRenameSession = useAgentStore((s) => s.onRenameSession);
  const approvePlan = useAgentStore((s) => s.approvePlan);
  const editPlan = useAgentStore((s) => s.editPlan);
  const agentMode = useAgentStore((s) => s.agentMode);
  const setShowSessions = useAgentStore((s) => s.setShowSessions);
  const setShowSettings = useAgentStore((s) => s.setShowSettings);
  const setShowTimers = useAgentStore((s) => s.setShowTimers);
  const setTheme = useAgentStore((s) => s.setTheme);

  const msgsRef = useRef<HTMLDivElement>(null);

  // 启动：拉健康状态 + 会话列表，没有会话则新建一个
  useEffect(() => {
    void init();
  }, [init]);

  // system 模式下跟随系统主题切换
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const fn = () => applyTheme();
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [theme]);

  // 消息列表自动滚动到底部（流式输出时跟随）
  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

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
                parts={m.parts}
                onApproval={onApproval}
                onUndo={onUndo}
                undoingId={undoingId}
                onApprovePlan={(aid) => void approvePlan(aid)}
                onEditPlan={editPlan}
              />
            ))}
          </div>
        )}
      </main>

      <Composer
        busy={busy}
        ready={ready}
        agentMode={agentMode}
        onSend={(t) => void send(t)}
        onStop={stop}
        error={error}
      />

      {showSessions && (
        <SessionList
          sessions={sessions}
          currentId={sessionId}
          onClose={() => setShowSessions(false)}
          onPick={(sid) => {
            setShowSessions(false);
            void loadSession(sid);
          }}
          onNew={() => void onNewSession()}
          onDelete={(sid) => void onDeleteSession(sid)}
          onRename={onRenameSession}
        />
      )}
      {showSettings && (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          mock={mock}
          theme={theme}
          onTheme={setTheme}
          onExport={() => void onExport()}
        />
      )}
      {showTimers && <TimerPanel onClose={() => setShowTimers(false)} />}
    </div>
  );
}
