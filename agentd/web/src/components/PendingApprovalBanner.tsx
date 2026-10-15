import { useEffect, useState } from "react";
import type { ApprovalDecision, PendingApproval } from "../api";
import { useAgentStore } from "../store/useAgentStore";

/**
 * 后台审批浮层：显示由定时任务 / 后台触发的待审批操作。
 *
 * 为什么单独做一层：普通对话的审批卡走 SSE 流，直接渲染在消息里；
 * 但定时任务没有 SSE 消费者，它的审批请求只会进入无人读取的队列 ——
 * 用户在界面上完全看不到，任务只能等超时被自动拒绝（安全但静默失败）。
 * 这里轮询 `/api/approvals/pending` 把那些卡片补显示出来。
 */
export function PendingApprovalBanner() {
  const pending = useAgentStore((s) => s.pendingApprovals);
  const decideApproval = useAgentStore((s) => s.decideBackgroundApproval);
  const messages = useAgentStore((s) => s.messages);
  const sessionId = useAgentStore((s) => s.sessionId);
  // 当前会话里已有内联审批卡（等待中的工具）时，同一审批不要再在横幅里重复一张
  const inlineWaiting = new Set<string>();
  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type === "tool" && p.tool.status === "waiting") inlineWaiting.add(p.tool.id);
    }
  }
  // 只在「属于当前会话且内联卡正等待」时排除：后台会话的审批（无 SSE 通路）必须继续显示
  const shown = pending.filter(
    (p) => !(p.session_id === sessionId && inlineWaiting.has(p.tool_call_id)),
  );
  // 后端 5 秒才轮询一次，秒数不会自己走。用「收到快照的时刻 + 秒表」推算当前剩余，
  // 这样倒计时在两次轮询之间也能平滑递减（否则用户看到卡住不动的数字）。
  const [snapshotAt, setSnapshotAt] = useState(() => Date.now());
  const [, setTick] = useState(0);

  useEffect(() => {
    setSnapshotAt(Date.now());
    setTick(0);
  }, [pending]);

  useEffect(() => {
    if (shown.length === 0) return;
    const t = setInterval(() => setTick((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, [shown.length]);

  const elapsed = Math.floor((Date.now() - snapshotAt) / 1000);

  if (shown.length === 0) return null;

  return (
    <div className="bg-approvals" role="region" aria-label="等待确认的后台操作">
      {shown.map((p) => (
        <BackgroundApprovalCard
          key={`${p.session_id}:${p.tool_call_id}`}
          item={p}
          remaining={Math.max(0, p.remaining - elapsed)}
          decide={decideApproval}
        />
      ))}
    </div>
  );
}

function BackgroundApprovalCard({
  item,
  remaining,
  decide,
}: {
  item: PendingApproval;
  /** 已按本地秒表推算过的剩余秒数 */
  remaining: number;
  decide: (sessionId: string, toolCallId: string, decision: ApprovalDecision) => Promise<boolean>;
}) {
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  // 提交失败（限流/断网/令牌失效）不能把卡片藏掉——服务端这条审批还在等，
  // 藏掉就退回到"静默失败"（超时被自动拒绝，用户全程无感知）。保留卡片并提示可重试。
  const [failed, setFailed] = useState(false);
  if (dismissed) return null;

  const act = async (decision: ApprovalDecision) => {
    setBusy(true);
    setFailed(false);
    try {
      // 带该审批自己的 session_id：后台审批常常不属于当前打开的会话，
      // 用当前会话提交会让后端找不到 gate（用户点了没反应）
      const sent = await decide(item.session_id, item.tool_call_id, decision);
      if (sent) setDismissed(true);
      else setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const riskLabel = item.risk === "danger" ? "危险操作" : item.risk === "write" ? "需要确认" : "只读操作";

  return (
    <div className="bg-approval">
      <div className="bg-approval-head">
        <span className="tool-dot waiting" aria-hidden="true" />
        <b>后台任务需要确认</b>
        <span className="bg-approval-risk">{riskLabel}</span>
      </div>
      <p className="bg-approval-body">
        <code>{item.name}</code>
        {item.summary ? `：${item.summary}` : ""}
      </p>
      <p className="bg-approval-note">
        {remaining > 0
          ? `不处理将在约 ${remaining} 秒后自动拒绝（该操作由定时任务触发）。`
          : "已超时，正在自动拒绝。"}
      </p>
      {failed && <p className="bg-approval-fail" role="alert">提交失败，审批仍在等待——请重试或检查后端连接。</p>}
      <div className="bg-approval-btns">
        <button className="btn ok" disabled={busy} onClick={() => void act("allow_once")}>
          允许一次
        </button>
        <button className="btn" disabled={busy} onClick={() => void act("allow_always")}>
          始终允许
        </button>
        <button className="btn danger" disabled={busy} onClick={() => void act("deny")}>
          拒绝
        </button>
      </div>
    </div>
  );
}
