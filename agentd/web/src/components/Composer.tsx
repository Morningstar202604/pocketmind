import { useEffect, useRef, useState } from "react";
import { IconSend, IconStop } from "./icons";

export function Composer({
  busy,
  ready,
  agentMode,
  providerLabel,
  onSend,
  onStop,
  error,
  onClearError,
}: {
  busy: boolean;
  ready: boolean;
  // 当前智能体模式：plan 模式下输入框提示语切换为「先制定计划」
  agentMode: "plan" | "act";
  // 当前 LLM 厂商标签（已配置时显示在输入框左上角）
  providerLabel?: string;
  onSend: (t: string) => void;
  onStop: () => void;
  error: string;
  onClearError: () => void;
}) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // 输入框随内容自动增高（最多 6 行），发送/清空后复原
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  }, [text]);

  // 「修改计划」按钮会派发该事件：把焦点拉回输入框，方便用户补充任务描述
  useEffect(() => {
    const onFocus = () => taRef.current?.focus();
    window.addEventListener("agent:focus-composer", onFocus);
    return () => window.removeEventListener("agent:focus-composer", onFocus);
  }, []);

  // Esc 停止生成：与停止按钮一致
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onStop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onStop]);

  const submit = () => {
    // busy 时不拦截：回复生成中继续发送会进入消息队列，自动排队执行
    if (!text.trim()) return;
    onSend(text);
    setText("");
  };

  // 占位文案：优先反映生成状态，其次按 plan/act 模式提示用户
  const placeholder = busy
    ? "生成中…可继续输入，将自动排队"
    : agentMode === "plan"
      ? "描述你的任务，我先制定计划"
      : "输入指令，回车发送";

  // 字符计数（超过 4000 字符给视觉提示，模型上下文窗口有限）
  const charCount = text.length;
  const overLimit = charCount > 4000;

  return (
    <footer className="composer">
      {error && (
        <div className="composer-err">
          <span>{error}</span>
          <button className="composer-err-close" onClick={onClearError} aria-label="关闭错误提示">
            ✕
          </button>
        </div>
      )}
      <div className="composer-box">
        {/* 状态标签行：左侧厂商 + 右侧字符计数 */}
        <div className="composer-meta">
          {providerLabel && ready && (
            <span className="composer-provider">{providerLabel}</span>
          )}
          <span className={`composer-count ${overLimit ? "over" : ""}`}>
            {charCount > 0 && `${charCount}`}
          </span>
        </div>
        <div className="composer-row">
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={placeholder}
            rows={1}
          />
          {busy && !text.trim() ? (
            <button className="send stop" onClick={onStop} title="停止生成（Esc）" aria-label="停止">
              <IconStop size={14} />
            </button>
          ) : (
            <button
              className="send"
              onClick={submit}
              disabled={!text.trim() || !ready}
              title="发送（Enter）"
              aria-label="发送"
            >
              <IconSend size={16} />
            </button>
          )}
        </div>
      </div>
      <p className="composer-hint">
        {busy
          ? "生成中…可继续输入，完成后自动执行"
          : agentMode === "plan"
            ? "计划模式：先出计划，你批准后再执行"
            : "Enter 发送 · Shift+Enter 换行 · 本地运行 · 数据仅在你的设备上"}
      </p>
    </footer>
  );
}