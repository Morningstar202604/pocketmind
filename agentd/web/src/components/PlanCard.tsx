// Plan 模式的执行计划卡片：区别于普通消息气泡，
// 展示模型给出的计划正文 + 预告会调用的工具，并提供「批准并执行 / 修改计划」两个动作。
import type { PlannedToolCall } from "../types";
import { Markdown } from "./MessageItem";

/** 把工具参数压成一行摘要（超长截断），供计划卡片里紧凑展示。 */
function summarizeArgs(args: unknown): string {
  let s: string;
  if (args == null || typeof args !== "object") s = String(args ?? "");
  else s = JSON.stringify(args);
  if (s.length > 80) s = s.slice(0, 80) + "…";
  return s;
}

export function PlanCard({
  plan,
  toolCalls,
  approved,
  onApprove,
  onEdit,
}: {
  plan: string;
  toolCalls: PlannedToolCall[];
  approved?: boolean;
  onApprove: () => void;
  onEdit: () => void;
}) {
  return (
    <div className="plan-card">
      <div className="plan-head">
        <span className="plan-title">执行计划</span>
        <span className="plan-tag">待你批准</span>
      </div>

      {/* 计划正文：markdown 渲染（与普通回复一致，经 DOMPurify 消毒） */}
      <div className="plan-body">
        <Markdown text={plan} />
      </div>

      {toolCalls.length > 0 && (
        <div className="plan-tools">
          <p className="plan-tools-title">计划调用的工具（{toolCalls.length}）</p>
          <ul>
            {toolCalls.map((tc, i) => (
              <li key={i}>
                <code>{tc.name}</code>
                <span className="plan-tool-args">{summarizeArgs(tc.arguments)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {approved ? (
        <p className="plan-approved">已批准，正在切换到执行模式并开始运行…</p>
      ) : (
        <div className="plan-actions">
          <button className="btn primary" onClick={onApprove}>
            批准并执行
          </button>
          <button className="btn" onClick={onEdit}>
            修改计划
          </button>
        </div>
      )}
    </div>
  );
}
