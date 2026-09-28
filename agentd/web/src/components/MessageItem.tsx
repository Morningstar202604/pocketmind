import { useMemo, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { Msg, Part, ToolPart } from "../types";
import { IconChevron } from "./icons";
import { PlanCard } from "./PlanCard";
import { PocketLogo } from "./PocketLogo";

/* ---------- Markdown（含代码块/表格，DOMPurify 消毒） ---------- */

// 一次性注册 DOMPurify 钩子：消毒后强制给所有 <a> 加 rel="noopener noreferrer"，
// 防止 LLM 输出里的外链 target=_blank 带来的反向 tabnabbing（window.opener 泄漏）。
DOMPurify.addHook("afterSanitizeAttributes", (node: Element) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function Markdown({ text }: { text: string }) {
  const html = useMemo(
    // marked v12 的 parse 在 async 模式下返回 Promise；显式 { async: false } 确保同步返回 string，
    // 不再用 `as string` 类型撒谎
    () =>
      DOMPurify.sanitize(marked.parse(text, { async: false }) as string, {
        // 允许 target 属性（钩子会统一加），rel 由钩子写入
        ADD_ATTR: ["target", "rel"],
      }),
    [text]
  );
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

/* ---------- 思考块 ---------- */

function ThinkingBlock({ text, streaming, forceOpen }: { text: string; streaming: boolean; forceOpen?: boolean }) {
  const [open, setOpen] = useState<boolean>(streaming || forceOpen || false);
  if (!text.trim()) return null;
  return (
    <div className={`thinking ${open ? "open" : ""}`}>
      <button className="thinking-head" onClick={() => setOpen((v) => !v)}>
        {streaming && !open ? <span className="dot-blink" /> : <span className="dot" />}
        <span>{open ? "思考过程" : "思考过程 · 已收起"}</span>
        <IconChevron open={open} />
      </button>
      {open && <div className="thinking-body">{text}</div>}
    </div>
  );
}

/* ---------- 工具卡片（含审批） ---------- */

const STATUS_LABEL: Record<ToolPart["status"], string> = {
  running: "运行中",
  waiting: "需要确认",
  completed: "完成",
  failed: "失败",
  denied: "已拒绝",
};

function ToolCard({
  tool,
  onApproval,
  onUndo,
  undoing,
}: {
  tool: ToolPart;
  onApproval: (id: string, d: "allow_once" | "allow_always" | "deny") => void;
  onUndo?: (id: string) => void;
  undoing?: boolean;
}) {
  const [open, setOpen] = useState(tool.status === "waiting");
  const inputText =
    typeof tool.input === "string"
      ? tool.input
      : JSON.stringify(tool.input ?? {}, null, 2);
  const waiting = tool.status === "waiting";
  return (
    <div className={`tool ${tool.status}`}>
      <button className="tool-head" onClick={() => setOpen((v) => !v)}>
        <span className={`tool-dot ${tool.status}`} />
        <span className="tool-name">{tool.name}</span>
        <span className={`tool-status ${tool.status}`}>{STATUS_LABEL[tool.status]}</span>
        <IconChevron open={open} />
      </button>
      {open && (
        <div className="tool-body">
          <pre className="tool-input">$ {inputText}</pre>
          {waiting && (
            <div className="approval">
              <p className="approval-summary">
                <b>{tool.name}</b>：{tool.summary || "需要你确认"}
                {tool.risk === "danger" ? "（危险操作）" : ""}
              </p>
              <div className="approval-btns">
                <button className="btn ok" onClick={() => onApproval(tool.id, "allow_once")}>允许一次</button>
                <button className="btn" onClick={() => onApproval(tool.id, "allow_always")}>始终允许</button>
                <button className="btn danger" onClick={() => onApproval(tool.id, "deny")}>拒绝</button>
              </div>
              <p className="approval-note">「始终允许」只对当前会话的同类操作生效；不点击会在超时后自动拒绝。</p>
            </div>
          )}
          {!waiting && tool.result && (
            <pre className="tool-result">{JSON.stringify(tool.result, null, 2)}</pre>
          )}
          {!waiting && tool.undoable && onUndo && (
            <div className="undo-bar">
              <button className="btn undo" disabled={undoing} onClick={() => onUndo(tool.id)}>
                {undoing ? "正在撤销…" : "撤销此操作"}
              </button>
              <span className="undo-note">执行前已自动备份，可安全还原</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- 消息条目 ---------- */

export function MessageItem({
  msg,
  parts,
  onApproval,
  onUndo,
  undoingId,
  onApprovePlan,
  onEditPlan,
}: {
  msg: Msg;
  parts: Part[];
  onApproval: (id: string, d: "allow_once" | "allow_always" | "deny") => void;
  onUndo?: (id: string) => void;
  undoingId?: string | null;
  onApprovePlan: (assistantMsgId: string) => void;
  onEditPlan: (assistantMsgId: string) => void;
}) {
  if (msg.role === "user") {
    const text = parts
      .filter((p): p is Extract<Part, { type: "text" }> => p.type === "text")
      .map((p) => p.text)
      .join("\n");
    return (
      <div className="row user">
        <div className="bubble user">{text}</div>
      </div>
    );
  }
  const hasContent = parts.length > 0;
  // 有工具在等审批时，自动展开同消息的思考块，让用户看到模型为什么发起该操作
  const waitingApproval = parts.some((p) => p.type === "tool" && p.tool.status === "waiting");
  return (
    <div className="row agent">
      <div className="agent-head">
        <span className="avatar">
          <PocketLogo size={26} />
        </span>
        <span className="agent-name">{msg.done ? "口袋智灵" : "正在思考…"}</span>
      </div>
      {msg.error && <div className="err-banner">{msg.error}</div>}
      {msg.queued && <div className="err-banner queued-banner">已排队：上一轮回复还在进行中，这条将在完成后自动执行。</div>}
      {parts.map((p, i) =>
        p.type === "thinking" ? (
          <ThinkingBlock key={i} text={p.text} streaming={!msg.done} forceOpen={waitingApproval} />
        ) : p.type === "tool" ? (
          <ToolCard
            key={p.tool.id}
            tool={p.tool}
            onApproval={onApproval}
            onUndo={onUndo}
            undoing={undoingId === p.tool.id}
          />
        ) : p.type === "plan" ? (
          <PlanCard
            key={i}
            plan={p.plan}
            toolCalls={p.tool_calls}
            approved={p.approved}
            onApprove={() => onApprovePlan(msg.id)}
            onEdit={() => onEditPlan(msg.id)}
          />
        ) : p.text.trim() ? (
          <div className="bubble agent" key={i}>
            <Markdown text={p.text} />
          </div>
        ) : null
      )}
      {!hasContent && !msg.done && (
        <div className="dots">
          <i />
          <i />
          <i />
        </div>
      )}
    </div>
  );
}
