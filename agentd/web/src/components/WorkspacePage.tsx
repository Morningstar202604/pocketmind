import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelWorkspaceTask,
  deleteWorkspaceFile,
  execWorkspace,
  abortWorkspaceMerge,
  checkoutWorkspaceBranch,
  commitWorkspaceMerge,
  getWorkspaceChanges,
  getWorkspaceConflicts,
  getWorkspaceFile,
  getWorkspaceGitStatus,
  getWorkspaceMergeCheckpoint,
  getWorkspaceInfo,
  getWorkspaceTasks,
  getWorkspaceTree,
  makeWorkspaceDir,
  previewWorkspaceFile,
  previewWorkspaceMerge,
  putWorkspaceFile,
  renameWorkspace,
  resolveWorkspaceConflict,
  searchWorkspace,
  startWorkspaceTask,
  undoWorkspaceChange,
  undoWorkspaceMerge,
  type WorkspaceChange,
  type WorkspaceCheckpoint,
  type WorkspaceConflict,
  type WorkspaceEntry,
  type WorkspaceFile,
  type WorkspaceGitStatus,
  type WorkspaceInfo,
  type WorkspacePreview,
  type WorkspaceSearchHit,
  type WorkspaceTask,
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

function changeKindLabel(kind: WorkspaceChange["kind"]): string {
  return kind === "create" ? "新建" : kind === "delete" ? "删除" : "修改";
}

function taskStatusLabel(status: WorkspaceTask["status"]): string {
  return {
    running: "运行中",
    cancelling: "取消中",
    done: "已完成",
    failed: "失败",
    cancelled: "已取消",
  }[status];
}

function shortTime(ts?: number | null): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

/**
 * 开发工作区：文件树 + CodeMirror + PTY 终端 + 命令台 + 变更/任务面板。
 *
 * 写操作会记录到 workspace_changes，支持逐文件撤销；长命令可以转到后台任务，
 * 不占住 HTTP 请求，也能在任务面板里取消。
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
  const [git, setGit] = useState<WorkspaceGitStatus | null>(null);
  const [gitTarget, setGitTarget] = useState("");
  const [gitMessage, setGitMessage] = useState("Merge branch");
  const [gitBusy, setGitBusy] = useState("");
  const [mergeCheckpoint, setMergeCheckpoint] = useState<WorkspaceCheckpoint | null>(null);
  const [conflicts, setConflicts] = useState<WorkspaceConflict[]>([]);
  const [conflictOpen, setConflictOpen] = useState<string | null>(null);
  const [manualDrafts, setManualDrafts] = useState<Record<string, string>>({});
  const [jumpLine, setJumpLine] = useState<number | null>(null);
  const [consoleMode, setConsoleMode] = useState<"term" | "cmd">("term");
  const [panel, setPanel] = useState<"changes" | "tasks" | "git" | null>(null);
  const [changes, setChanges] = useState<WorkspaceChange[]>([]);
  const [tasks, setTasks] = useState<WorkspaceTask[]>([]);
  const [panelBusy, setPanelBusy] = useState("");
  const theme = useAgentStore((s) => s.theme);
  const dark = theme === "dark" || (theme === "system" && !window.matchMedia("(prefers-color-scheme: light)").matches);

  const dirty = current != null && draft !== current.content;
  const nativeRuntime = info?.runtime === "kotlin";

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

  const refreshChanges = useCallback(async () => {
    try {
      const r = await getWorkspaceChanges();
      setChanges(r.items ?? []);
    } catch {
      /* 变更面板失败不影响主流程 */
    }
  }, []);

  const refreshTasks = useCallback(async () => {
    try {
      const r = await getWorkspaceTasks();
      setTasks(r.items ?? []);
    } catch {
      /* 任务面板失败不影响主流程 */
    }
  }, []);

  useEffect(() => {
    void refreshChanges();
  }, [refreshChanges]);

  useEffect(() => {
    if (panel !== "tasks") return;
    void refreshTasks();
    const timer = window.setInterval(() => void refreshTasks(), 1500);
    return () => window.clearInterval(timer);
  }, [panel, refreshTasks]);

  useEffect(() => {
    if (info?.runtime === "kotlin") setConsoleMode("cmd");
  }, [info?.runtime]);

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
      void refreshChanges();
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

  const runBackground = async () => {
    const cmd = command.trim();
    if (!cmd) return;
    setError("");
    try {
      const t = await startWorkspaceTask(cmd, cwd, 300);
      setPanel("tasks");
      setStatus(`后台任务已启动：${t.id}`);
      await refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const cancelTask = async (id: string) => {
    setPanelBusy(id);
    setError("");
    try {
      await cancelWorkspaceTask(id);
      setStatus("已请求取消任务");
      await refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPanelBusy("");
    }
  };

  const undoChange = async (c: WorkspaceChange) => {
    if (!window.confirm(`撤销 ${c.path} 的这次${c.kind === "delete" ? "删除" : "修改"}？`)) return;
    setPanelBusy(c.id);
    setError("");
    try {
      const r = await undoWorkspaceChange(c.id);
      if (r.error) throw new Error(r.error);
      if (current?.path === c.path) {
        if (c.kind === "create") {
          setCurrent(null);
          setDraft("");
        } else {
          await openFile(c.path);
        }
      }
      await refreshChanges();
      refreshCurrentTree();
      setStatus("已撤销");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPanelBusy("");
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

  const refreshGit = useCallback(async (base?: string) => {
    setError("");
    try {
      const g = await getWorkspaceGitStatus(base || cwd || info?.root || ".");
      setGit(g);
      setGitTarget((prev) => {
        if (prev && g.branches?.some((b) => b.name === prev)) return prev;
        return g.branches?.find((b) => !b.current)?.name || "";
      });
      if (g.merging) {
        const c = await getWorkspaceConflicts(g.repo_root || base || cwd || ".");
        setConflicts(c.items ?? []);
        setConflictOpen(null);
      } else {
        setConflicts([]);
        setConflictOpen(null);
        if (g.available) {
          const cp = await getWorkspaceMergeCheckpoint(g.repo_root || base || cwd || ".");
          setMergeCheckpoint(cp.checkpoint ?? null);
        } else {
          setMergeCheckpoint(null);
        }
      }
      return g;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [cwd, info?.root]);

  const openGit = async () => {
    await refreshGit();
    setPanel("git");
  };

  const checkoutBranch = async (branch: string) => {
    if (!git) return;
    if (!git.clean && !window.confirm("工作区有未提交改动，切换分支会被拒绝。仍要尝试？")) return;
    setGitBusy(`checkout:${branch}`);
    setError("");
    try {
      await checkoutWorkspaceBranch(git.repo_root || cwd, branch);
      setCurrent(null);
      setDraft("");
      await loadTree(tree?.path || cwd);
      await refreshGit();
      setStatus(`已切换到 ${branch}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
    }
  };

  const startMerge = async () => {
    if (!git || !gitTarget.trim()) return;
    setGitBusy("merge");
    setError("");
    try {
      const r = await previewWorkspaceMerge(git.repo_root || cwd, gitTarget.trim());
      if (r.state) setGit(r.state);
      setMergeCheckpoint(r.checkpoint ?? null);
      if (r.state?.merging) {
        const c = await getWorkspaceConflicts(git.repo_root || cwd);
        setConflicts(c.items ?? []);
        setConflictOpen(null);
      }
      setStatus(r.clean ? "已进入合并状态：无冲突，可提交合并" : `合并有 ${r.conflicts?.length || 0} 个冲突文件，先解决冲突`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
    }
  };

  const abortMerge = async () => {
    if (!git) return;
    if (!window.confirm("中止合并并恢复到合并前状态？")) return;
    setGitBusy("abort");
    setError("");
    try {
      const r = await abortWorkspaceMerge(git.repo_root || cwd);
      setGit(r.state);
      setMergeCheckpoint(r.checkpoint ?? null);
      setConflicts([]);
      setConflictOpen(null);
      refreshCurrentTree();
      setStatus("已中止合并");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
    }
  };

  const undoMerge = async () => {
    if (!git) return;
    if (!window.confirm("撤销刚提交的合并，回到合并前？工作区会被重置。")) return;
    setGitBusy("undo");
    setError("");
    try {
      const r = await undoWorkspaceMerge(git.repo_root || cwd);
      setGit(r.state);
      setMergeCheckpoint(r.checkpoint ?? null);
      setConflicts([]);
      refreshCurrentTree();
      setStatus("已撤销合并");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
    }
  };

  const commitMerge = async () => {
    if (!git) return;
    setGitBusy("commit");
    setError("");
    try {
      const r = await commitWorkspaceMerge(git.repo_root || cwd, gitMessage.trim());
      if (r.state) setGit(r.state);
      setMergeCheckpoint(r.checkpoint ?? null);
      setConflicts([]);
      setConflictOpen(null);
      refreshCurrentTree();
      setStatus("合并已提交");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
    }
  };

  const resolveConflict = async (
    path: string, strategy: "ours" | "theirs" | "manual", content?: string,
  ) => {
    if (!git) return;
    setGitBusy(`resolve:${path}:${strategy}`);
    setError("");
    try {
      const r = await resolveWorkspaceConflict(git.repo_root || cwd, path, strategy, content);
      if (r.state) setGit(r.state);
      setManualDrafts((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
      const c = await getWorkspaceConflicts(git.repo_root || cwd);
      setConflicts(c.items ?? []);
      setConflictOpen(c.items?.[0]?.path || null);
      setStatus(r.remaining.length ? `已解决 ${path}，还剩 ${r.remaining.length} 个冲突` : "冲突已全部解决，可提交合并");
      refreshCurrentTree();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGitBusy("");
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
    if (!window.confirm(`删除 ${current.path}？${info?.features.changes !== false ? "文件会先备份；" : "原生运行时删除不可撤销；"}目录只允许删空目录。`)) return;
    try {
      await deleteWorkspaceFile(current.path);
      setCurrent(null);
      setDraft("");
      refreshCurrentTree();
      void refreshChanges();
      setStatus("已删除");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const entries = useMemo(() => tree?.entries ?? [], [tree]);
  const rootLabel = info?.roots.find((r) => r.path === info.root)?.label || "工作区";
  const panelTitle = panel === "changes" ? "工作区变更" : panel === "tasks" ? "后台任务" : "Git 分支 / 合并";

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
            {rootLabel} · {info.runtime === "termux" ? "Termux 运行时" : nativeRuntime ? "原生运行时" : "Python 运行时"} · 写操作只允许在工作区内
          </p>
        </div>
        <div className="ws-head-actions">
          <button className="btn sm" onClick={newDir}>
            新建目录
          </button>
          <button className="btn sm" onClick={newFile}>
            新建文件
          </button>
          {info.features.git !== false && (
            <button className="btn sm" onClick={() => void openGit()}>
              Git{git?.branch ? ` ${git.branch}` : ""}{git && !git.clean ? " •" : ""}
            </button>
          )}
          {info.features.changes !== false && (
            <button className="btn sm" onClick={() => setPanel("changes")}>
              变更{changes.length ? ` ${changes.length}` : ""}
            </button>
          )}
          {info.features.tasks !== false && (
            <button className="btn sm" onClick={() => setPanel("tasks")}>
              后台任务{tasks.filter((t) => t.status === "running" || t.status === "cancelling").length
                ? ` ${tasks.filter((t) => t.status === "running" || t.status === "cancelling").length}`
                : ""}
            </button>
          )}
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
            <button className="ws-up" onClick={() => void loadTree(parentPath(tree?.path || info.root))}
              disabled={tree?.path === info.root} title="上一级">
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
                {!nativeRuntime && (
                  <button
                    className={consoleMode === "term" ? "on" : ""}
                    onClick={() => setConsoleMode("term")}
                  >
                    终端
                  </button>
                )}
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
            {consoleMode === "term" && !nativeRuntime ? (
              <Suspense fallback={<div className="ws-muted">终端加载中…</div>}>
                <WorkspaceTerminal cwd={cwd} dark={dark} />
              </Suspense>
            ) : (
              <>
                <pre className="ws-output" aria-live="polite">
                  {output || (nativeRuntime
                  ? "原生运行时只有 app uid 的 /system/bin/sh：没有 python、node、git、apt；命令会在上方工作目录里执行。"
                  : "在这里运行 git / python / npm test；命令会在上方工作目录里执行。")}
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
                    placeholder={nativeRuntime ? "例如：ls -la / cat hello.txt" : "例如：python3 hello.py"}
                    aria-label="命令"
                    spellCheck={false}
                  />
                  <button className="btn primary sm" onClick={() => void run()} disabled={running || !command.trim()}>
                    {running ? "运行中…" : "运行"}
                  </button>
                  <button className="btn sm" onClick={() => void runBackground()} disabled={!command.trim()}>
                    后台
                  </button>
                </div>
                <div className="ws-git">
                  <button className="btn sm" onClick={() => void openGit()}>
                    Git 分支 / 合并
                  </button>
                  <span className="ws-git-summary-inline">
                    {git ? `${git.branch || "HEAD"} · ${git.clean ? "干净" : "有未提交改动"}` : "打开后读取分支状态"}
                  </span>
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

      {panel && (
        <div className="ws-modal" role="dialog" aria-modal="true" aria-label={panelTitle}>
          <div className="ws-modal-card ws-panel-card">
            <div className="ws-modal-head">
              <h3>{panelTitle}</h3>
              <button className="ws-x" onClick={() => setPanel(null)} aria-label="关闭">
                ×
              </button>
            </div>
            {panel === "git" ? (
              <div className="ws-git-panel">
                {git && !git.available && (
                  <p className="ws-muted">{git.message || "当前目录不是 Git 仓库；先 git init 或把项目放到工作区。"}</p>
                )}
                <div className="ws-git-summary">
                  <span className={`ws-git-dot ${git?.clean ? "ok" : "dirty"}`} />
                  <strong>{git?.branch || "未读取"}</strong>
                  {git?.detached && <span className="ws-muted">detached</span>}
                  <span className="ws-muted">
                    {git ? (git.clean ? "工作区干净" : `${git.files?.length || 0} 个未提交文件`) : "尚未读取"}
                  </span>
                  {git?.upstream && (
                    <span className="ws-muted">↑{git.ahead ?? 0} ↓{git.behind ?? 0}</span>
                  )}
                </div>
                <div className="ws-git-actions">
                  <button className="btn sm" onClick={() => void refreshGit()} disabled={gitBusy !== ""}>
                    刷新
                  </button>
                  <select
                    value={gitTarget}
                    onChange={(e) => setGitTarget(e.target.value)}
                    aria-label="合并目标分支"
                  >
                    {(git?.branches || []).filter((b) => !b.current).map((b) => (
                      <option key={b.name} value={b.name}>{b.name}</option>
                    ))}
                  </select>
                  <button
                    className="btn primary sm"
                    onClick={() => void startMerge()}
                    disabled={!git || !gitTarget || !git.clean || git.merging || gitBusy !== ""}
                  >
                    预合并
                  </button>
                  {git?.merging && (
                    <button className="btn sm danger" onClick={() => void abortMerge()} disabled={gitBusy !== ""}>
                      中止合并
                    </button>
                  )}
                </div>
                {!git?.merging && mergeCheckpoint && (
                  <div className="ws-merge-box">
                    <p>
                      上次合并已提交，可撤销到合并前 <code>{mergeCheckpoint.short}</code>
                    </p>
                    <div className="ws-merge-commit">
                      <button
                        className="btn sm danger"
                        onClick={() => void undoMerge()}
                        disabled={gitBusy !== "" || !git?.clean}
                      >
                        {gitBusy === "undo" ? "正在撤销…" : "撤销合并"}
                      </button>
                      {!git?.clean && <span className="ws-muted">先处理未提交改动</span>}
                    </div>
                  </div>
                )}
                {git?.merging && (
                  <div className="ws-merge-box">
                    <p>
                      合并中：{git.merge_head || "MERGE_HEAD"} · {conflicts.length ? `${conflicts.length} 个冲突` : "无冲突"}
                    </p>
                    <div className="ws-merge-commit">
                      <input
                        value={gitMessage}
                        onChange={(e) => setGitMessage(e.target.value)}
                        placeholder="合并提交说明"
                        aria-label="合并提交说明"
                      />
                      <button
                        className="btn primary sm"
                        onClick={() => void commitMerge()}
                        disabled={gitBusy !== "" || conflicts.length > 0}
                      >
                        {conflicts.length > 0 ? "先解冲突" : "提交合并"}
                      </button>
                    </div>
                  </div>
                )}
                {conflicts.length > 0 && (
                  <ul className="ws-conflict-list">
                    {conflicts.map((c) => (
                      <li key={c.path}>
                        <div className="ws-panel-row">
                          <div>
                            <code title={c.path}>{c.path}</code>
                            <span className="ws-muted">
                              {c.binary ? "二进制冲突，需用命令行处理" : "选择保留我的或对方的版本"}
                            </span>
                          </div>
                          <button
                            className="ws-x"
                            onClick={() => setConflictOpen(conflictOpen === c.path ? null : c.path)}
                          >
                            {conflictOpen === c.path ? "收起" : "查看"}
                          </button>
                        </div>
                        {conflictOpen === c.path && (
                          <div className="ws-conflict-cols">
                            <div>
                              <span className="ws-conflict-label">我的（ours）</span>
                              <pre>{c.ours || "（空）"}</pre>
                            </div>
                            <div>
                              <span className="ws-conflict-label">对方的（theirs）</span>
                              <pre>{c.theirs || "（空）"}</pre>
                            </div>
                          </div>
                        )}
                        {conflictOpen === c.path && (
                          <div className="ws-conflict-manual">
                            <span className="ws-conflict-label">手动编辑（保存后 git add）</span>
                            <textarea
                              value={manualDrafts[c.path] ?? c.work}
                              onChange={(e) => setManualDrafts((prev) => ({ ...prev, [c.path]: e.target.value }))}
                              spellCheck={false}
                              aria-label={`手动解决 ${c.path}`}
                            />
                          </div>
                        )}
                        <div className="ws-conflict-actions">
                          <button
                            className="btn sm"
                            disabled={gitBusy !== "" || c.binary}
                            onClick={() => void resolveConflict(c.path, "ours")}
                          >
                            用我的
                          </button>
                          <button
                            className="btn sm"
                            disabled={gitBusy !== "" || c.binary}
                            onClick={() => void resolveConflict(c.path, "theirs")}
                          >
                            用对方的
                          </button>
                          <button
                            className="btn primary sm"
                            disabled={gitBusy !== "" || c.binary}
                            onClick={() => void resolveConflict(c.path, "manual", manualDrafts[c.path] ?? c.work)}
                          >
                            保存手动解决
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {(git?.branches?.length || 0) > 0 && (
                  <ul className="ws-branch-list">
                    {(git?.branches || []).map((b) => (
                      <li key={b.name}>
                        <div className="ws-panel-row">
                          <div>
                            <code>{b.name}</code>
                            <span className="ws-muted">
                              {b.sha}{b.upstream ? ` · ${b.upstream}` : " · 本地分支"}
                            </span>
                          </div>
                          {b.current ? (
                            <span className="ws-branch-current">当前</span>
                          ) : (
                            <button
                              className="btn sm"
                              disabled={!git?.clean || git?.merging || gitBusy !== ""}
                              onClick={() => void checkoutBranch(b.name)}
                            >
                              切换
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="ws-muted">
                  合并会在工作区留下冲突文件；先选 ours/theirs，再提交合并。中止会恢复合并前状态。
                </p>
              </div>
            ) : panel === "changes" ? (
              changes.length === 0 ? (
                <p className="ws-muted">还没有变更。保存文件、新建或删除后会出现在这里。</p>
              ) : (
                <ul className="ws-change-list">
                  {changes.map((c) => (
                    <li key={c.id}>
                      <div className="ws-panel-row">
                        <div>
                          <code title={c.path}>{c.path}</code>
                          <span className="ws-muted">
                            {changeKindLabel(c.kind)} · {shortTime(c.created_at)}
                            {c.bytes ? ` · ${formatSize(c.bytes)}` : ""}
                          </span>
                        </div>
                        <button
                          className="btn sm danger"
                          disabled={panelBusy === c.id}
                          onClick={() => void undoChange(c)}
                        >
                          {panelBusy === c.id ? "撤销中…" : "撤销"}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )
            ) : tasks.length === 0 ? (
              <p className="ws-muted">还没有后台任务。命令台里点「后台」即可启动，长任务不会卡住页面。</p>
            ) : (
              <ul className="ws-task-list">
                {tasks.map((t) => (
                  <li key={t.id}>
                    <div className="ws-panel-row">
                      <div>
                        <code title={t.command}>{t.command}</code>
                        <span className="ws-muted">
                          <span className={`ws-task-status ${t.status}`}>{taskStatusLabel(t.status)}</span>
                          {" · "}{t.cwd}{t.exit_code != null ? ` · exit ${t.exit_code}` : ""}
                          {t.log_path ? " · 日志已落盘" : ""}
                        </span>
                      </div>
                      {(t.status === "running" || t.status === "cancelling") && (
                        <button
                          className="btn sm danger"
                          disabled={panelBusy === t.id || t.status === "cancelling"}
                          onClick={() => void cancelTask(t.id)}
                        >
                          {panelBusy === t.id || t.status === "cancelling" ? "取消中…" : "取消"}
                        </button>
                      )}
                    </div>
                    {(t.error || t.stderr || t.stdout) && (
                      <pre className="ws-task-log">
                        {[t.error, t.stderr, t.stdout].filter(Boolean).join("\n").slice(-1200)}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="ws-modal-actions">
              <button className="btn sm" onClick={() => setPanel(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
