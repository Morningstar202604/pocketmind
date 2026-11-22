import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { getToken } from "../api";

const DARK_THEME = {
  background: "#0f1115",
  foreground: "#e4e7ec",
  cursor: "#10b981",
  cursorAccent: "#0f1115",
  selectionBackground: "rgba(16,185,129,0.28)",
  black: "#000000",
  red: "#f87171",
  green: "#34d399",
  yellow: "#fbbf24",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#e4e7ec",
};

const LIGHT_THEME = {
  background: "#ffffff",
  foreground: "#1f2937",
  cursor: "#0b805d",
  cursorAccent: "#ffffff",
  selectionBackground: "rgba(11,128,93,0.22)",
  black: "#1f2937",
  red: "#b91c1c",
  green: "#047857",
  yellow: "#b45309",
  blue: "#1d4ed8",
  magenta: "#7e22ce",
  cyan: "#0e7490",
  white: "#ffffff",
};

/**
 * 工作区终端：xterm.js + WebSocket + 后端 PTY。
 *
 * 与「命令台」的区别：这里保留一个真正的 shell 进程，`cd`、环境变量、
 * 交互式命令（vim / top / less）都能用；命令台是一次一条、无状态。
 */
export function WorkspaceTerminal({ cwd, dark }: { cwd: string; dark: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "error">("connecting");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 12,
      fontFamily: "ui-monospace, Menlo, Consolas, monospace",
      theme: dark ? DARK_THEME : LIGHT_THEME,
      scrollback: 4000,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);

    const fitSoon = () => {
      try {
        fit.fit();
      } catch {
        /* 容器还没布局好，下一次 resize 再试 */
      }
    };
    requestAnimationFrame(fitSoon);

    const token = getToken();
    const params = new URLSearchParams({ cwd, token });
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/api/workspace/term?${params.toString()}`;
    const ws = new WebSocket(url);
    setStatus("connecting");

    ws.onopen = () => {
      setStatus("open");
      term.write("\x1b[32m● 已连接工作区终端\x1b[0m\r\n");
      fitSoon();
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") term.write(ev.data);
    };
    ws.onerror = () => {
      setStatus("error");
    };
    ws.onclose = () => {
      setStatus("closed");
      term.write("\r\n\x1b[33m● 连接已断开；点「重连」重开一个 shell\x1b[0m\r\n");
    };

    const dataSub = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    const onWindowResize = () => fitSoon();
    window.addEventListener("resize", onWindowResize);
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(fitSoon);
      observer.observe(host);
    }

    return () => {
      window.removeEventListener("resize", onWindowResize);
      observer?.disconnect();
      dataSub.dispose();
      resizeSub.dispose();
      try {
        ws.close();
      } catch {
        /* already closed */
      }
      term.dispose();
    };
  }, [cwd, dark, nonce]);

  return (
    <div className="ws-term">
      <div className="ws-term-head">
        <span className={`ws-term-dot ${status}`} aria-hidden="true" />
        <span className="ws-term-status">
          {status === "open"
            ? "终端已连接"
            : status === "connecting"
              ? "连接中…"
              : status === "error"
                ? "连接失败"
                : "已断开"}
        </span>
        <span className="ws-term-cwd" title={cwd}>
          {cwd}
        </span>
        <button className="ws-x" onClick={() => setNonce((n) => n + 1)} title="重开一个 shell">
          重连
        </button>
      </div>
      <div ref={hostRef} className="ws-term-host" />
    </div>
  );
}
