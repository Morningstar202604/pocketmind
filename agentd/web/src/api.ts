// agentd API 客户端：REST + SSE 流式。

import { fetchEventSource } from "@microsoft/fetch-event-source";

const TOKEN_KEY = "pa-token";

/** 局域网令牌：设置面板保存 server.token 时同步写入；请求统一携带。 */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(token: string) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

export interface Session {
  id: string;
  title: string;
  message_count: number;
}

export interface StoredMessage {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  meta: Record<string, unknown>;
}

export interface Provider {
  id: string;
  label: string;
  base_url: string;
  model: string;
  note: string;
}

export interface LlmSettings {
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

export interface AppSettings {
  llm: LlmSettings;
  permission_mode: "auto" | "approve" | "chat";
  server: {
    allow_lan?: boolean;
    token?: string;
    approval_timeout?: number;
    mcp?: Array<{ name: string; command: string; args: string[] }>;
  };
  user_prefs?: string;
}

export interface Job {
  id: string;
  name: string;
  trigger_type: "cron" | "interval" | "date";
  expr: string;
  message: string;
  session_id: string;
  condition: string;
  enabled: boolean;
  next_run?: string | null;
}

export const getJobs = () => api.get<{ jobs: Job[] }>("/api/jobs").then((d) => d.jobs);
export const createJob = (body: Partial<Job>) => api.post<Job>("/api/jobs", body);
export const updateJob = (id: string, enabled: boolean) => api.put<Job>(`/api/jobs/${id}`, { enabled });
export const deleteJob = (id: string) => api.del<{ ok: boolean }>(`/api/jobs/${id}`);

export interface ToolInfo {
  name: string;
  description: string;
  risk: "safe" | "write" | "danger";
  summary: string;
  timeout: number;
  group: string;
}

export type ChatEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "approval"; id: string; name: string; summary: string; risk: string }
  | {
      type: "tool_update";
      id: string;
      name: string;
      status: "completed" | "failed" | "denied";
      result: Record<string, unknown>;
      undoable?: boolean;
    }
  | { type: "queued"; message: string }
  | { type: "done"; stop_reason: string }
  | { type: "error"; message: string };

/** 撤销一次文件类危险操作（写文件/删文件前已自动备份）。 */
export const undoToolCall = (sessionId: string, toolCallId: string) =>
  api.post<{ ok?: boolean; restored?: string; error?: string }>("/api/undo", {
    session_id: sessionId,
    tool_call_id: toolCallId,
  });

/** 会话重命名。 */
export const renameSession = (sessionId: string, title: string) =>
  api.put<{ ok: boolean; title: string }>(`/api/sessions/${sessionId}`, { title });

/** 导出全部数据（会话/消息/配置）为 JSON 下载。 */
export const exportAll = () => api.get<Record<string, unknown>>("/api/export");

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = j.detail;
      else if (j?.error) msg = j.error;
    } catch {
      /* keep http status */
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export const api = {
  async get<T>(path: string): Promise<T> {
    return parse<T>(await fetch(path, { cache: "no-store", headers: headers() }));
  },
  async post<T>(path: string, body?: unknown): Promise<T> {
    return parse<T>(
      await fetch(path, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    );
  },
  async put<T>(path: string, body: unknown): Promise<T> {
    return parse<T>(
      await fetch(path, {
        method: "PUT",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      })
    );
  },
  async del<T>(path: string): Promise<T> {
    return parse<T>(await fetch(path, { method: "DELETE", headers: headers() }));
  },

  /** POST /api/chat 并消费 SSE（@microsoft/fetch-event-source），事件交给 onEvent。 */
  async sseChat(
    sessionId: string,
    message: string,
    onEvent: (ev: ChatEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    // 注意：聊天是一次性 POST，网络错误时不能自动重连（否则会重复发送用户消息）。
    // 因此 onerror 一律抛错终止重连；正常流结束由 onclose 收尾。
    await fetchEventSource("/api/chat", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
      async onopen(res) {
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try {
            const j = await res.json();
            msg = j?.detail || j?.error || msg;
          } catch {
            /* keep */
          }
          throw new Error(msg);
        }
      },
      onmessage(ev) {
        // ev.data 即 SSE data: 后的原文，逐帧 JSON 解析后分发
        try {
          onEvent(JSON.parse(ev.data) as ChatEvent);
        } catch {
          /* 忽略坏帧 */
        }
      },
      onerror(err) {
        // 抛错 = 终止 fetch-event-source 的默认重连，让外层 catch 统一处理
        throw err instanceof Error ? err : new Error("SSE 连接异常");
      },
      onclose() {
        // 服务端正常关闭流：无需额外动作，外层 finally 兜底标记消息完成
      },
    });
  },
};
