// ⚠ 已废弃（legacy）：旧版 goose ACP 桥。依赖 goose 二进制（原沙箱路径
//   /workspace/goose/target/release/goose），真机不可用。新版后端为 Python
//   agentd（FastAPI + SSE，8787 端口），启动请用：bash web/start.sh
//
// 单端口服务器：静态站(dist/) + /api 轮询接口 + 内部连 goose ACP WS。
// 浏览器只发短请求（静态资源 / health / chat / state 轮询），无 SSE、无长连接，
// 彻底绕开预览代理对 wss/EventSource 不稳定的问题。
import { spawn, execSync } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocket } from "ws";

const GOOSE_WS = process.env.GOOSE_WS || "ws://127.0.0.1:3284/acp";
const PORT = Number(process.env.PORT || 5173);
const DIST = path.join(path.dirname(fileURLToPath(import.meta.url)), "dist");
const MAX_MSG = 100000;
const SETTINGS_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), "settings.json");
const GOOSE_BIN = process.env.GOOSE_BIN || "/workspace/goose/target/release/goose";
const GOOSE_PORT = Number(process.env.GOOSE_PORT || 3284);
const GOOSE_HOME = process.env.GOOSE_PATH_ROOT || "/root/.goose-test";
const HISTORY_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), "history.jsonl");

function appendHistory(entry) {
  try {
    const line = JSON.stringify({ ...entry, ts: Date.now() });
    fs.appendFileSync(HISTORY_FILE, line + "\n");
  } catch {
    /* ignore */
  }
}

function loadHistory(limit = 200) {
  try {
    const lines = fs.readFileSync(HISTORY_FILE, "utf8").trim().split("\n").filter(Boolean);
    const out = lines
      .slice(-limit)
      .map((l) => {
        try {
          return JSON.parse(l);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
    return out;
  } catch {
    return [];
  }
}

function clearHistory() {
  try {
    if (fs.existsSync(HISTORY_FILE)) fs.writeFileSync(HISTORY_FILE, "");
    return true;
  } catch {
    return false;
  }
}

function persistTurn(prompt, finalState) {
  const userText =
    typeof prompt === "string" ? prompt : prompt?.update?.content ?? "";
  if (!userText) return;
  appendHistory({
    role: "user",
    content: userText,
  });
  appendHistory({
    role: "assistant",
    content: finalState.content ?? "",
    stopReason: finalState.stopReason,
    tools: state.tools.map((t) => ({
      name: t.toolName,
      title: t.title,
      status: t.status,
    })),
  });
}
let gooseProc = null;

function loadSettings() {
  try {
    const s = JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf8"));
    const apiKey =
      typeof s.apiKey === "string" && s.apiKey ? s.apiKey : process.env.LLM_API_KEY || "";
    return {
      model: typeof s.model === "string" && s.model ? s.model : "agnes-3.0-flash",
      temperature:
        typeof s.temperature === "number" && s.temperature >= 0 ? s.temperature : 0,
      maxTokens:
        typeof s.maxTokens === "number" && Number.isFinite(s.maxTokens)
          ? Math.max(0, Math.min(32768, Math.trunc(s.maxTokens)))
          : 0,
      thinking:
        typeof s.thinking === "string"
          ? ["", "low", "medium", "high"].includes(s.thinking)
            ? s.thinking
            : ""
          : "",
      permissionMode:
        typeof s.permissionMode === "string"
          ? ["auto", "approve", "smart_approve", "chat"].includes(s.permissionMode)
            ? s.permissionMode
            : "auto"
          : "auto",
      apiKey,
    };
  } catch {
    return {
      model: "agnes-3.0-flash",
      temperature: 0,
      maxTokens: 0,
      thinking: "",
      permissionMode: "auto",
      apiKey: process.env.LLM_API_KEY || "",
    };
  }
}

function saveSettings(input) {
  const cur = loadSettings();
  const next = {
    model:
      typeof input.model === "string" && input.model.trim()
        ? input.model.trim().slice(0, 128)
        : cur.model,
    temperature:
      typeof input.temperature === "number" &&
      Number.isFinite(input.temperature) &&
      input.temperature >= 0 &&
      input.temperature <= 2
        ? input.temperature
        : cur.temperature,
    maxTokens:
      typeof input.maxTokens === "number" && Number.isFinite(input.maxTokens)
        ? Math.max(0, Math.min(32768, Math.trunc(input.maxTokens)))
        : cur.maxTokens,
    thinking:
      typeof input.thinking === "string" &&
      ["", "low", "medium", "high"].includes(input.thinking)
        ? input.thinking
        : cur.thinking,
    permissionMode:
      typeof input.permissionMode === "string" &&
      ["auto", "approve", "smart_approve", "chat"].includes(input.permissionMode)
        ? input.permissionMode
        : cur.permissionMode,
    apiKey:
      typeof input.apiKey === "string"
        ? input.apiKey.trim().slice(0, 512) || cur.apiKey
        : cur.apiKey,
  };
  fs.writeFileSync(
    SETTINGS_FILE,
    JSON.stringify(
      {
      model: next.model,
      temperature: next.temperature,
      maxTokens: next.maxTokens,
      thinking: next.thinking,
      permissionMode: next.permissionMode,
      apiKey: next.apiKey,
      },
      null,
      2,
    ),
  );
  return next;
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".map": "application/json",
};

// ---------- goose ACP 客户端 ----------
let ws = null;
let nextId = 0;
let handshaked = false;
let sessionId = null;
let retryMs = 1000;
const pending = new Map();
let activePromptId = null;
let promptWatchdog = 0;
let lastPrompt = null; // { message, retry } 超时/坏会话时重放

// ---------- 运行状态（供前端轮询） ----------
const state = {
  status: "idle", // idle | running | done | error
  text: "",
  thought: "",
  tools: [], // {toolCallId,toolName,title,status,input,liveOutput,result,error,exitCode}
  error: "",
  stopReason: "",
  updatedAt: 0,
};

function resetState() {
  state.status = "running";
  state.text = "";
  state.thought = "";
  state.tools = [];
  state.error = "";
  state.stopReason = "";
  state.updatedAt = Date.now();
}

function finishState(status, extra = {}) {
  state.status = status;
  Object.assign(state, extra);
  state.updatedAt = Date.now();
}

function formatInput(raw) {
  if (raw == null) return undefined;
  if (typeof raw === "string") return raw;
  if (typeof raw === "object") {
    if (raw.path != null && typeof raw.command === "string")
      return `${raw.path} · ${raw.command}`;
    if (typeof raw.command === "string") return raw.command;
    try {
      const s = JSON.stringify(raw);
      return s.length > 500 ? s.slice(0, 500) + "…" : s;
    } catch {
      return String(raw);
    }
  }
  return String(raw);
}

function upsertTool(part) {
  const idx = state.tools.findIndex((t) => t.toolCallId === part.toolCallId);
  if (idx === -1) state.tools.push(part);
  else state.tools[idx] = { ...state.tools[idx], ...part };
}

function applyUpdate(update) {
  if (!update) return;
  const kind = update.sessionUpdate;
  if (kind === "agent_message_chunk") {
    state.text += update.content?.text ?? "";
  } else if (kind === "agent_thought_chunk") {
    state.thought += update.content?.text ?? "";
  } else if (kind === "tool_call") {
    upsertTool({
      toolCallId: String(update.toolCallId),
      toolName: (update.title ?? "").split(" · ")[0] || "tool",
      title: update.title,
      status: "in_progress",
      input: formatInput(update.rawInput),
    });
  } else if (kind === "tool_call_update") {
    const id = String(update.toolCallId);
    const prev = state.tools.find((t) => t.toolCallId === id);
    const status =
      update.status === "completed"
        ? "completed"
        : update.status === "failed" || update.status === "error"
          ? "failed"
          : "in_progress";
    const patch = {
      toolCallId: id,
      toolName: update.title?.split(" · ")[0] ?? prev?.toolName ?? "tool",
      title: update.title ?? prev?.title,
      status,
    };
    const live = update._meta?.toolNotification?.params?.chunks;
    if (Array.isArray(live)) {
      patch.liveOutput =
        (prev?.liveOutput ?? "") + live.map((c) => c.output ?? "").join("");
    }
    const ro = update.rawOutput;
    if (ro && typeof ro === "object") {
      patch.result = ro.stdout ?? "";
      patch.error = ro.stderr || (status === "failed" ? "执行失败" : "");
      if (typeof ro.exit_code === "number") patch.exitCode = ro.exit_code;
    }
    upsertTool(patch);
  }
  state.updatedAt = Date.now();
}

function gooseUrl() {
  return "http://127.0.0.1:" + GOOSE_PORT + "/acp";
}

function gooseAlive() {
  return new Promise((resolve) => {
    const req = http
      .get(gooseUrl(), (res) => {
        res.resume();
        resolve(res.statusCode === 200 || res.statusCode === 406);
      })
      .on("error", () => resolve(false));
    req.setTimeout(3000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function gooseChildEnv() {
  const s = loadSettings();
  const env = { ...process.env };
  delete env.LLM_API_KEY;
  env.GOOSE_PATH_ROOT = GOOSE_HOME;
  env.GOOSE_PROVIDER = "custom_llm";
  env.LLM_API_KEY = s.apiKey;
  env.GOOSE_MODEL = s.model;
  if (s.temperature > 0) env.GOOSE_TEMPERATURE = String(s.temperature);
  else delete env.GOOSE_TEMPERATURE;
  if (s.maxTokens > 0) env.GOOSE_MAX_TOKENS = String(s.maxTokens);
  else delete env.GOOSE_MAX_TOKENS;
  if (s.thinking) env.GOOSE_THINKING_EFFORT = s.thinking;
  else delete env.GOOSE_THINKING_EFFORT;
  env.GOOSE_MODE = s.permissionMode || "auto";
  env.GOOSE_DISABLE_KEYRING = "1";
  env.GOOSE_DISABLE_TELEMETRY = "1";
  return env;
}

async function startGoose() {
  if (await gooseAlive()) return { started: false, alreadyRunning: true };
  stopGoose();
  const logStream = fs.openSync("/tmp/goose_wd.log", "a");
  gooseProc = spawn(GOOSE_BIN, ["serve", "--dangerously-unauthenticated", "--host", "127.0.0.1", "--port", String(GOOSE_PORT)], {
    cwd: path.dirname(GOOSE_BIN),
    env: gooseChildEnv(),
    detached: true,
    stdio: ["ignore", logStream, logStream],
  });
  gooseProc.unref();
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (await gooseAlive()) return { started: true };
  }
  stopGoose();
  throw new Error("goose 启动失败，请查看 /tmp/goose_wd.log");
}

function stopGoose() {
  if (gooseProc) {
    try {
      process.kill(-gooseProc.pid, "SIGTERM");
    } catch {
      /* 进程已退出 */
    }
    gooseProc = null;
  }
  try {
    execSync("pkill -f 'goose serve --dangerously-unauthenticated' || true", {
      timeout: 5000,
    });
  } catch {
    /* ignore */
  }
}

// 启动后 30s 看门狗：如果 goose WS 一直没握手成功，检查 goose 进程是否在位，
// 挂了则拉活（settings 环境变量），避免 server 半死状态（WS 断开重试，但 goose 已退出）
let gooseWatchdog = 0;
function scheduleGooseWatchdog() {
  clearTimeout(gooseWatchdog);
  gooseWatchdog = setTimeout(async () => {
    if (!handshaked) {
      const alive = await gooseAlive();
      if (!alive) {
        console.log("[server] watchdog: goose not alive, restarting");
        try {
          await startGoose();
          retryMs = 1000;
        } catch (e) {
          console.error("[server] watchdog restart failed:", e);
        }
        scheduleReconnect();
      }
    }
    scheduleGooseWatchdog(); // 持续检查
  }, 30 * 1000);
}

function connectGoose() {
  try {
    ws = new WebSocket(GOOSE_WS);
  } catch (e) {
    scheduleReconnect(e);
    return;
  }
  ws.on("open", async () => {
    retryMs = 1000;
    try {
      await handshake();
      console.log("[server] goose ready, session =", sessionId);
    } catch (e) {
      handshaked = false;
      console.error("[server] handshake failed:", e);
    }
  });
  ws.on("close", () => {
    handshaked = false;
    sessionId = null;
    if (state.status === "running") {
      finishState("error", { error: "与 goose 的连接断开，请重试" });
    }
    activePromptId = null;
    clearTimeout(promptWatchdog);
    scheduleReconnect();
  });
  ws.on("error", (e) => console.error("[server] ws error:", e.message));
  ws.on("message", (d) => handleMessage(String(d)));
}

async function handshake() {
  await rpc("initialize", {
    protocolVersion: 1,
    clientCapabilities: {},
    clientInfo: { name: "server", version: "1.0" },
  });
  const res = await rpc("session/new", { cwd: "/tmp", mcpServers: [] });
  sessionId = res?.sessionId ?? null;
  handshaked = !!sessionId;
  if (!handshaked) throw new Error("no sessionId");
}

/** 坏会话：丢弃旧 sessionId，开新会话；成功则返回 true */
async function recreateSession() {
  console.log("[server] recreate session, old =", sessionId);
  sessionId = null;
  handshaked = false;
  try {
    await handshake();
    console.log("[server] new session =", sessionId);
    return true;
  } catch (e) {
    console.error("[server] recreate failed:", e);
    return false;
  }
}

function sendPrompt(message) {
  resetState();
  const pid = nextId++;
  activePromptId = pid;
  lastPrompt = message;
  clearTimeout(promptWatchdog);
  promptWatchdog = setTimeout(() => {
    if (activePromptId === pid) {
      activePromptId = null;
      lastPrompt = null;
      finishState("error", { error: "回复超时（5 分钟），请重试" });
      // 会话可能卡死，后台重建
      void recreateSession();
    }
  }, 5 * 60 * 1000);
  ws.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: pid,
      method: "session/prompt",
      params: { sessionId, prompt: [{ type: "text", text: message }] },
    }),
  );
  return pid;
}

// ACP 中止：发 cancelled 通知（session/update, sessionUpdate="cancelled"）
function cancelPrompt() {
  if (!sessionId || !ws || ws.readyState !== 1) return false;
  try {
    ws.send(
      JSON.stringify({
        jsonrpc: "2.0",
        method: "session/update",
        params: { sessionId, update: { sessionUpdate: "cancelled" } },
      }),
    );
    if (activePromptId !== null) {
      clearTimeout(promptWatchdog);
      activePromptId = null;
      lastPrompt = null;
      finishState("error", { error: "已停止" });
    }
    return true;
  } catch {
    return false;
  }
}

function scheduleReconnect(e) {  if (e) console.error("[server] connect error:", e.message ?? e);
  console.log(`[server] goose retry in ${retryMs}ms`);
  setTimeout(connectGoose, retryMs);
  retryMs = Math.min(retryMs * 2, 15000);
}

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== 1) return reject(new Error("goose ws not open"));
    const id = nextId++;
    ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(method + " timeout"));
      }
    }, 60000);
  });
}

function handleMessage(raw) {
  let m;
  try {
    m = JSON.parse(raw);
  } catch {
    return;
  }
  if (m.id !== undefined && m.method === undefined && pending.has(m.id)) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    if (m.error) p.reject(new Error(m.error.message ?? "rpc error"));
    else p.resolve(m.result);
    return;
  }
  if (m.id !== undefined && m.method === undefined && m.id === activePromptId) {
    activePromptId = null;
    clearTimeout(promptWatchdog);
    if (m.error) {
      const msg = m.error.message ?? "prompt failed";
      const replay = lastPrompt;
      lastPrompt = null;
      // 会话坏掉（Invalid params 等）：开新会话并自动重放一次
      if (replay && /invalid params|session|not found|invalid/i.test(msg)) {
        console.log("[server] prompt failed (", msg, "), recreate + replay");
        void (async () => {
          if (await recreateSession()) {
            if (ws && ws.readyState === 1 && sessionId) sendPrompt(replay);
            else finishState("error", { error: "会话重建失败，请重试" });
          } else {
            finishState("error", { error: "会话重建失败，请重试" });
          }
        })();
        return;
      }
      const errText = state.error || msg;
      finishState("error", { error: msg });
      persistTurn(lastPrompt, {
        status: "error",
        content: state.text || errText,
        stopReason: "error",
      });
    } else {
      const stop = m.result?.stopReason ?? "end_turn";
      persistTurn(lastPrompt, {
        status: "done",
        content: state.text,
        stopReason: stop,
      });
      lastPrompt = null;
      finishState("done", { stopReason: stop });
    }
    return;
  }
  if (m.method === "requestPermission") {
    ws?.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: m.id,
        result: { outcome: { outcome: "selected", optionId: "allow_once" } },
      }),
    );
    return;
  }
  if (m.method === "session/update" && state.status === "running") {
    applyUpdate(m.params?.update ?? m.params);
  }
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function serveStatic(req, res) {
  let urlPath = decodeURIComponent((req.url ?? "/").split("?")[0]);
  if (urlPath === "/") urlPath = "/index.html";
  // 防路径穿越
  const filePath = path.normalize(path.join(DIST, urlPath));
  if (!filePath.startsWith(DIST)) {
    res.writeHead(403);
    res.end("forbidden");
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      // SPA 兜底：无扩展名的路径回 index.html
      if (!path.extname(urlPath)) {
        fs.readFile(path.join(DIST, "index.html"), (e2, html) => {
          if (e2) {
            res.writeHead(404);
            res.end("not found");
            return;
          }
          res.writeHead(200, { "Content-Type": MIME[".html"] });
          res.end(html);
        });
        return;
      }
      res.writeHead(404);
      res.end("not found");
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, {
      "Content-Type": MIME[ext] ?? "application/octet-stream",
      "Cache-Control": ext === ".html" ? "no-store" : "public, max-age=3600",
    });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const url = req.url ?? "/";

  if (url.startsWith("/api/health")) {
    json(res, 200, {
      ok: true,
      handshaked,
      sessionId,
      busy: activePromptId !== null,
    });
    return;
  }

  if (req.method === "POST" && url.startsWith("/api/settings/apply")) {
    let body = "";
    let overflow = false;
    req.on("data", (c) => {
      body += c;
      if (body.length > 64 * 1024) {
        overflow = true;
        req.destroy();
      }
    });
    req.on("end", async () => {
      if (overflow) return json(res, 413, { error: "too large" });
      let input = {};
      try {
        input = JSON.parse(body || "{}");
      } catch {
        return json(res, 400, { error: "invalid json" });
      }
      try {
        const s = saveSettings(input);
        stopGoose();
        // 关闭当前 ACP 连接，等待 startGoose 后自动重连
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
        handshaked = false;
        sessionId = null;
        activePromptId = null;
        clearTimeout(promptWatchdog);
        if (state.status === "running") {
          finishState("error", { error: "设置已更新，goose 正在重启…" });
        }
        const r = await startGoose();
        // 重启后重新建立 WS 连接
        retryMs = 1000;
        scheduleReconnect();
        json(res, 200, { ok: true, settings: s, ...r });
      } catch (e) {
        json(res, 500, { error: String(e?.message ?? e) });
      }
    });
    return;
  }

  if (req.method === "GET" && url.startsWith("/api/settings")) {
    const s = loadSettings();
    json(res, 200, {
      ...s,
      gooseRunning: gooseProc !== null,
      goosePort: GOOSE_PORT,
      gooseHome: GOOSE_HOME,
    });
    return;
  }

  if (url.startsWith("/api/history")) {
    if (req.method === "POST") {
      let body = "";
      req.on("data", (c) => {
        body += c;
        if (body.length > 16 * 1024) req.destroy();
      });
      req.on("end", () => {
        let input = {};
        try {
          input = JSON.parse(body || "{}");
        } catch {
          input = {};
        }
        if (input.action === "clear") {
          json(res, 200, { ok: clearHistory() });
        } else {
          json(res, 200, { ok: true });
        }
      });
      return;
    }
    json(res, 200, { ok: true, items: loadHistory(500) });
    return;
  }

  if (url.startsWith("/api/stop")) {
    const ok = cancelPrompt();
    json(res, ok ? 200 : 409, ok ? { ok: true } : { error: "没有正在运行的回复" });
    return;
  }

  if (url.startsWith("/api/state")) {
    const body = JSON.stringify({ ...state });
    res.writeHead(200, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Length": Buffer.byteLength(body),
    });
    res.end(body);
    return;
  }

  if (req.method === "POST" && url.startsWith("/api/chat")) {
    let body = "";
    let overflow = false;
    req.on("data", (c) => {
      body += c;
      if (body.length > 1024 * 1024) {
        overflow = true;
        req.destroy();
      }
    });
    req.on("end", () => {
      if (overflow) {
        if (!res.writableEnded) json(res, 413, { error: "request too large" });
        return;
      }
      let message = "";
      try {
        message = JSON.parse(body || "{}").message ?? "";
      } catch {
        message = body;
      }
      if (typeof message !== "string") message = String(message);
      if (!message.trim()) return json(res, 400, { error: "empty message" });
      if (message.length > MAX_MSG)
        return json(res, 400, { error: "message too long (max 100000 chars)" });
      if (!handshaked || !sessionId || !ws || ws.readyState !== 1)
        return json(res, 503, { error: "goose not ready, retry in a moment" });
      if (activePromptId !== null || state.status === "running")
        return json(res, 409, { error: "上一轮回复还在进行中，请稍候再发" });
      if (!sessionId) return json(res, 503, { error: "会话重建中，稍后再发" });

      sendPrompt(message);
      json(res, 200, { ok: true });
    });
    return;
  }

  if (req.method === "GET") {
    serveStatic(req, res);
    return;
  }

  res.writeHead(405);
  res.end();
});

connectGoose();
scheduleGooseWatchdog();
server.listen(PORT, "0.0.0.0", () =>
  console.log(`[server] :${PORT} static=${DIST} goose=${GOOSE_WS}`),
);
