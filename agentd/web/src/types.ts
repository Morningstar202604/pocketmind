// 前端消息模型

export interface ToolPart {
  id: string;
  name: string;
  input: unknown;
  status: "running" | "waiting" | "completed" | "failed" | "denied";
  result?: Record<string, unknown>;
  summary?: string;
  risk?: string;
  undoable?: boolean; // 文件类危险操作已自动备份，可一键撤销
  /** 执行耗时（毫秒）：由前端在 tool_start → tool_update 之间计时，
   *  用户需要知道"等 30 秒"是正常还是卡住。 */
  ms?: number;
  /** 本地开始时刻（仅运行期使用，用于结算 ms；不走网络、不持久化） */
  startedAt?: number;
  /** startedAt 起表时的工具状态；审批等待作废起表，批准后重开计时才结算这段 */
  startedFor?: ToolPart["status"];
}

/** 计划中预告的一次工具调用（plan 事件里的 tool_calls，仅展示用，尚未执行） */
export interface PlannedToolCall {
  name: string;
  arguments: unknown;
}

export type Part =
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool"; tool: ToolPart }
  // Plan 模式产出的执行计划卡片（不是普通消息气泡）
  | { type: "plan"; plan: string; tool_calls: PlannedToolCall[]; approved?: boolean; ignored?: boolean };

export interface Msg {
  id: string;
  /** 后端消息主键。前端本地创建的消息（尚未落库）没有这个字段。 */
  serverId?: number;
  role: "user" | "assistant";
  parts: Part[];
  done: boolean;
  error?: string;
  queued?: boolean;
  // 消息创建时间（秒级时间戳，来自后端 created_at 或本地创建时刻）
  createdAt?: number;
}

export function uid(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}