import { useState } from "react";
import type { Session } from "../api";
import { relativeTime } from "../lib/format";
import { IconClose, IconPlus, IconTrash } from "./icons";

export function SessionList({
  sessions,
  currentId,
  onClose,
  onPick,
  onNew,
  onDelete,
  onRename,
}: {
  sessions: Session[];
  currentId: string;
  onClose: () => void;
  onPick: (sid: string) => void;
  onNew: () => void;
  onDelete: (sid: string) => void;
  onRename: (sid: string, title: string) => void;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const filtered = q
    ? sessions.filter((s) => s.title.toLowerCase().includes(q))
    : sessions;

  const rename = (s: Session) => {
    const title = window.prompt("给会话改个名字（留空 = 不改）", s.title);
    if (title && title.trim() && title.trim() !== s.title) onRename(s.id, title.trim());
  };

  const handleDelete = (s: Session) => {
    const ok = window.confirm(`确定删除会话「${s.title}」吗？相关消息与定时任务将一并删除。`);
    if (ok) onDelete(s.id);
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2>会话</h2>
          <div className="drawer-actions">
            <button className="btn primary sm" onClick={onNew}>
              <IconPlus size={13} /> 新建
            </button>
            <button className="icon-btn" onClick={onClose} aria-label="关闭">
              <IconClose />
            </button>
          </div>
        </div>
        <div className="drawer-search">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话…"
            aria-label="搜索会话"
          />
        </div>
        <ul className="sessions">
          {filtered.length === 0 && (
            <li className="s-empty">{q ? "没有匹配的会话" : "还没有会话"}</li>
          )}
          {filtered.map((s) => (
            <li
              key={s.id}
              className={`session ${s.id === currentId ? "active" : ""}`}
              onClick={() => onPick(s.id)}
            >
              <div className="session-main">
                <span className="s-title" title="双击重命名" onDoubleClick={() => rename(s)}>
                  {s.title}
                </span>
                <span className="s-count">
                  {s.message_count} 条消息
                  {relativeTime(s.updated_at) && (
                    <span className="s-time">{` · ${relativeTime(s.updated_at)}`}</span>
                  )}
                </span>
              </div>
              <div className="s-ops">
                <button
                  className="s-del s-rename"
                  onClick={(e) => {
                    e.stopPropagation();
                    rename(s);
                  }}
                  title="重命名会话"
                  aria-label="重命名会话"
                >
                  ✎
                </button>
                <button
                  className="s-del"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(s);
                  }}
                  title="删除会话"
                  aria-label="删除会话"
                >
                  <IconTrash size={15} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}