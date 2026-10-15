import { useState } from "react";
import type { TodoItem } from "../api";

/**
 * 任务清单面板：渲染 todo_write 工具维护的清单（跨 step 存活，落库可恢复）。
 *
 * 设计取向（设备仪表感而非"卡片"）：标题行即仪表——计数 + 进度条 + 两个动作
 * （清空 / 收起）。清单本体用细线分隔，不用独立卡片与阴影；完成后整体降饱和，
 * 只留一个可点动作。位置固定在输入框上方，不占消息流。
 */
export function TodoPanel({ todos, onClear }: { todos: TodoItem[]; onClear: () => void }) {
  const [open, setOpen] = useState(true);

  if (todos.length === 0) return null;

  const done = todos.filter((t) => t.status === "completed").length;
  const allDone = done === todos.length;
  const pct = Math.round((done / todos.length) * 100);

  return (
    <div className={`todo-panel ${allDone ? "all-done" : ""}`} aria-label="任务清单">
      <div className="todo-head">
        <span className="todo-title">任务清单</span>
        <span className="todo-count">
          {done}/{todos.length}
        </span>
        <span
          className="todo-track"
          role="progressbar"
          aria-valuenow={done}
          aria-valuemin={0}
          aria-valuemax={todos.length}
          aria-label="完成进度"
        >
          <span className="todo-fill" style={{ width: `${pct}%` }} />
        </span>
        {allDone && (
          <button className="todo-action" onClick={onClear} title="清空清单">
            清空
          </button>
        )}
        <button
          className="todo-action"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={open ? "收起任务清单" : "展开任务清单"}
        >
          {open ? "收起" : "展开"}
        </button>
      </div>
      {open && (
        <ul className="todo-list">
          {todos.map((t, i) => (
            <li key={i} className={`todo-item ${t.status}`}>
              <span className="todo-mark" aria-hidden="true">
                {t.status === "completed" ? "✓" : t.status === "in_progress" ? "▶" : "○"}
              </span>
              <span className="todo-text">{t.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
