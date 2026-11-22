import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteWorkspaceFile,
  execWorkspace,
  getWorkspaceFile,
  getWorkspaceGit,
  getWorkspaceInfo,
  getWorkspaceTree,
  makeWorkspaceDir,
  previewWorkspaceFile,
  putWorkspaceFile,
  renameWorkspace,
  searchWorkspace,
  type WorkspaceEntry,
  type WorkspaceFile,
  type WorkspaceGit,
  type WorkspaceInfo,
  type WorkspacePreview,
  type WorkspaceSearchHit,
  type WorkspaceTree,
} from "../api";
import { IconCode, IconFolder } from "./icons";
import { WorkspaceEditor } from "./WorkspaceEditor";
const WorkspaceTerminal = lazy(() =>
  import("./WorkspaceTerminal").then((m) => ({ default: m.WorkspaceTerminal })),
);
import { useAgentStore } from "../store/useAgentStore";

function joinPath(base: string, name: string): string {
  return `${base.replace(/\/+$/, "")}/${name}`;
}

function parentPath(path: string): string {
  const clean = path.replace(/\/+$/, "");
  const i = clean.lastIndexOf("/");
  return i <= 0 ? "/" : clean.slice(0, i);
}

function formatSize(n?: number | null): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 开发工作区：文件树 + 文本编辑器 + 命令台。
 *
 * 这一版刻意不接 CodeMirror / xterm：先把「能看文件、能改、能保存前看 diff、
 * 能跑命令」的闭环跑通。编辑器语法高亮和真 PTY 终端是下一阶段，不应阻塞
 * 工作区 API 与审批链路的验证。
 */
export function WorkspacePage() {
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);
  const [error, setError] = useState("");
  const [tree, setTree] = useState<WorkspaceTree | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [current, setCurrent] = useState<WorkspaceFile | null>(null);
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState<WorkspacePreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [output, setOutput] = useState("");
  const [running, setRunning] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<WorkspaceSearchHit[]>([]);
  const [git, setGit] = useState<WorkspaceGit | null>(null);
  const [jumpLine, setJumpLine] = useState<number | null>(null);
  const [consoleMode, setConsoleMode] = useState<"term" | "cmd">("term");
  const theme = useAgentStore((s) => s.theme);
  const dark = theme === "dark" || (theme === "system" && !window.matchMedia("(prefers-color-scheme: light)").matches);

  const dirty = current != null && draft !== current.content;

  const loadTree = useCallback(async (path: string) => {
    setTreeLoading(true);
    setError("");
    try {
      const t = await getWorkspaceTree(path);
      setTree(t);
      setCwd(t.path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTreeLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await getWorkspaceInfo();
        if (!alive) return;
        setInfo(data);
        await loadTree(data.root);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [loadTree]);

  const openFile = useCallback(async (path: string, line?: number) => {
    setError("");
    setStatus("");
    try {
      const f = await getWorkspaceFile(path);
      setCurrent(f);
      setDraft(f.content);
      setCwd(parentPath(f.path));
      if (line) setJumpLine(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refreshCurrentTree = () => {
    if (tree) void loadTree(tree.path);
  };

  const save = async () => {
    if (!current) return;
    setSaving(true);
    setError("");
    try {
      const p = await previewWorkspaceFile(current.path, draft);
      if (!p.changed) {
        setStatus("没有改动");
        return;
      }
      setPreview(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const confirmSave = async () => {
    if (!current || !preview) return;
    setSaving(true);
    setError("");
    try {
      const out = await putWorkspaceFile(current.path, draft, current.mtime);
      const next = { ...current, content: draft, mtime: out.mtime, size: out.bytes };
      setCurrent(next);
      setPreview(null);
      setStatus(`已保存 ${out.bytes} 字节${out.backup ? "（已备份原文件）" : ""}`);
      refreshCurrentTree();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const revert = () => {
    if (!current) return;
    setDraft(current.content);
    setStatus("已还原到上次读取的内容");
  };

  const run = async () => {
    const cmd = command.trim();
    if (!cmd || running) return;
    setRunning(true);
    setError("");
    setOutput((prev) => `${prev}${prev ? "\n" : ""}$ ${cmd}\n`);
    try {
      const r = await execWorkspace(cmd, cwd);
      const parts: string[] = [];
      if (r.stdout) parts.push(r.stdout);
      if (r.stderr) parts.push(`[stderr]\n${r.stderr}`);
      if (r.error) parts.push(`[error] ${r.error}`);
      if (r.truncated) parts.push("[输出已截断]");
      const text = parts.join("\n") || "(无输出)";
      setOutput((prev) => `${prev}${text}\n`);
      setStatus(r.ok ? `命令完成（exit ${r.exit_code ?? 0}）` : `命令失败（exit ${r.exit_code ?? "?"}）`);
      refreshCurrentTree();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setOutput((prev) => `${prev}[error] ${msg}\n`);
      setError(msg);
    } finally {
      setRunning(false);
    }
  };

  const doSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setError("");
    try {
      const r = await searchWorkspace(q, tree?.path || info?.root || ".");
      setHits(r.results);
      setStatus(`搜索到 ${r.results.length} 条${r.truncated ? "（已截断）" : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const refreshGit = async () => {
    setError("");
    try {
      const g = await getWorkspaceGit(cwd);
      setGit(g);
      setStatus(g.available ? "已刷新 Git 状态" : g.message || "当前环境没有 git");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const newFile = async () => {
    const name = window.prompt("新文件名（相对当前目录）", "untitled.txt");
    if (!name) return;
    const path = joinPath(tree?.path || cwd, name);
    try {
      await putWorkspaceFile(path, "", null);
      await loadTree(tree?.path || cwd);
      await openFile(path);
      setStatus(`已创建 ${path}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const newDir = async () => {
    const name = window.prompt("新目录名（相对当前目录）", "src");
    if (!name) return;
    const path = joinPath(tree?.path || cwd, name);
    try {
      await makeWorkspaceDir(path);
      await loadTree(tree?.path || cwd);
      setStatus(`已创建 ${path}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const rename = async () => {
    if (!current) return;
    const name = window.prompt("重命名为", current.path);
    if (!name || name === current.path) return;
    try {
      await renameWorkspace(current.path, name);
      setCurrent(null);
      setDraft("");
      await loadTree(tree?.path || cwd);
      await openFile(name);
      setStatus("已重命名");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async () => {
    if (!current) return;
    if (!window.confirm(`删除 ${current.path}？文件会先备份，目录只允许删空目录。`)) return;
    try {
      await deleteWorkspaceFile(current.path);
      setCurrent(null);
      setDraft("");
      refreshCurrentTree();
      setStatus("已删除");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const entries = useMemo(() => tree?.entries ?? [], [tree]);
  const rootLabel = info?.roots.find((r) => r.path === info.root)?.label || "工作区";

  if (!info) {
    return (
      <div className="ws-page">
        <div className="ws-loading">
          <p>{error || "正在连接工作区…"}</p>
          {error && (
            <button className="btn sm" onClick={() => window.location.reload()}>
              重试
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="ws-page">
      <div className="ws-head">
        <div>
          <h2>开发工作区</h2>
          <p className="ws-sub">
            {rootLabel} · {info.runtime === "termux" ? "Termux 运行时" : "Python 运行时"} · 写操作只允许在工作区内
          </p>
        </div>
        <div className="ws-head-actions">
          <button className="btn sm" onClick={newDir}>
            新建目录
          </button>
          <button className="btn sm" onClick={newFile}>
            新建文件
          </button>
        </div>
      </div>

      {error && (
        <div className="ws-error" role="alert">
          {error}
          <button className="ws-x" onClick={() => setError("")} aria-label="关闭错误">
            ×
          </button>
        </div>
      )}
      {status && <div className="ws-status">{status}</div>}

      <div className="ws-body">
        <aside className="ws-tree">
          <div className="ws-tree-head">
            <button className="ws-up" onClick={() => void loadTree(parentPath(tree?.path || info.root))} title="上一级">
              ↑
            </button>
            <span title={tree?.path}>{tree?.path || info.root}</span>
          </div>
          {treeLoading ? (
            <p className="ws-muted">加载中…</p>
          ) : entries.length === 0 ? (
            <p className="ws-muted">空目录；点「新建文件」开始。</p>
          ) : (
            <ul>
              {entries.map((e: WorkspaceEntry) => (
                <li key={e.name}>
                  <button
                    className={`ws-entry ${e.type}`}
                    onClick={() => {
                      const p = joinPath(tree?.path || info.root, e.name);
                      if (e.type === "dir") void loadTree(p);
                      else void openFile(p);
                    }}
                  >
                    <span className="ws-entry-icon">
                      {e.type === "dir" ? <IconFolder size={16} /> : <IconCode size={16} />}
                    </span>
                    <span className="ws-entry-name">{e.name}</span>
                    <span className="ws-entry-size">{formatSize(e.size)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="ws-search">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void doSearch();
              }}
              placeholder="在当前目录搜索…"
              aria-label="搜索工作区"
            />
            <button className="btn sm" onClick={() => void doSearch()} disabled={!query.trim()}>
              搜索
            </button>
          </div>
          {hits.length > 0 && (
            <ul className="ws-hits">
              {hits.map((h) => (
                <li key={`${h.path}:${h.line}`}>
                  <button onClick={() => void openFile(h.path, h.line)} title={`${h.path}:${h.line}`}>
                    <span className="ws-hit-path">{h.path.replace(`${info.root}/`, "")}</span>
                    <span className="ws-hit-line">:{h.line}</span>
                    <span className="ws-hit-text">{h.text}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="ws-main">
          {current ? (
            <>
              <div className="ws-filebar">
                <span className="ws-filepath" title={current.path}>
                  {current.path}
                </span>
                {dirty && <span className="ws-dirty">未保存</span>}
                <div className="ws-file-actions">
                  <button className="btn sm" onClick={revert} disabled={!dirty || saving}>
                    还原
                  </button>
                  <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
                    {saving ? "保存中…" : "保存"}
                  </button>
                  <button className="btn sm" onClick={() => void rename()}>
                    重命名
                  </button>
                  <button className="btn sm danger" onClick={() => void remove()}>
                    删除
                  </button>
                </div>
              </div>
              <WorkspaceEditor
                path={current.path}
                value={draft}
                dark={dark}
                jumpLine={jumpLine}
                onChange={(next) => setDraft(next)}
                onSave={() => void save()}
              />
            </>
          ) : (
            <div className="ws-empty">
              <IconCode size={38} />
              <h3>选一个文件开始</h3>
              <p>左侧是工作区文件树；打开文件后可以直接编辑，保存前会先给你看 diff。</p>
            </div>
          )}

          <div className={`ws-console ${consoleMode}`}>
            <div className="ws-console-head">
              <div className="ws-mode" role="group" aria-label="终端模式">
                <button
                  className={consoleMode === "term" ? "on" : ""}
                  onClick={() => setConsoleMode("term")}
                >
                  终端
                </button>
                <button
                  className={consoleMode === "cmd" ? "on" : ""}
                  onClick={() => setConsoleMode("cmd")}
                >
                  命令台
                </button>
              </div>
              <span className="ws-cwd" title={cwd}>
                {cwd}
              </span>
              {consoleMode === "cmd" && (
                <button className="ws-x" onClick={() => setOutput("")} title="清空输出">
                  清空
                </button>
              )}
            </div>
            {consoleMode === "term" ? (
              <Suspense fallback={<div className="ws-muted">终端加载中…</div>}>
                <WorkspaceTerminal cwd={cwd} dark={dark} />
              </Suspense>
            ) : (
              <>
                <pre className="ws-output" aria-live="polite">
                  {output || "在这里运行 git / python / npm test；命令会在上方工作目录里执行。"}
                </pre>
                <div className="ws-command-row">
                  <span className="ws-prompt">$</span>
                  <input
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void run();
                      }
                    }}
                    placeholder="例如：python3 hello.py"
                    aria-label="命令"
                    spellCheck={false}
                  />
                  <button className="btn primary sm" onClick={() => void run()} disabled={running || !command.trim()}>
                    {running ? "运行中…" : "运行"}
                  </button>
                </div>
                <div className="ws-git">
                  <button className="btn sm" onClick={() => void refreshGit()}>
                    刷新 Git 状态
                  </button>
                  {git && (
                    <pre className="ws-git-out">
                      {git.available ? git.stdout || git.stderr || "（无输出）" : git.message}
                    </pre>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      </div>

      {preview && (
        <div className="ws-modal" role="dialog" aria-modal="true" aria-label="保存前确认">
          <div className="ws-modal-card">
            <div className="ws-modal-head">
              <h3>保存前确认</h3>
              <button className="ws-x" onClick={() => setPreview(null)} aria-label="取消">
                ×
              </button>
            </div>
            <p className="ws-muted">
              {preview.path} · {preview.changed ? "有改动" : "无改动"}
              {preview.truncated ? " · diff 已截断" : ""}
            </p>
            <pre className="ws-diff">{preview.diff || "（没有差异）"}</pre>
            <div className="ws-modal-actions">
              <button className="btn sm" onClick={() => setPreview(null)} disabled={saving}>
                取消
              </button>
              <button className="btn primary sm" onClick={() => void confirmSave()} disabled={saving || !preview.changed}>
                {saving ? "保存中…" : "确认保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
