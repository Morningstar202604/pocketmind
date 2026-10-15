import { useState, type ReactNode } from "react";

/**
 * 设置面板的可折叠分组。
 *
 * 手机上一个设置页摊开十几个分组要滚七屏，用户找不到东西；
 * 这里给每个分组一个可点的标题行，收起时把"当前值"显示在标题右侧，
 * 于是折叠不是"藏起来"，而是"先给概览、想看细节再展开"。
 *
 * 组头是 div[role=button] 而不是 <button>：组头里的 action 是独立按钮
 * （如"导出备份"），HTML 不允许 <button> 嵌套 <button>（React 会告警、
 * 键盘焦点行为在各浏览器不一致）。
 */
export function CollapsibleGroup({
  title,
  summary,
  action,
  defaultOpen = false,
  children,
}: {
  title: string;
  /** 收起时显示的当前值概览（如"逐次审批 · 120s"） */
  summary?: ReactNode;
  /** 常驻组头的动作按钮（如"导出备份"）：收起时也必须能点到 */
  action?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`group collapsible ${open ? "open" : ""}`}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${open ? "收起" : "展开"}「${title}」`}
        className="group-head-btn"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
      >
        <span className="group-title">{title}</span>
        {!open && summary ? <span className="group-summary">{summary}</span> : null}
        <span className="group-grow" />
        {action ? (
          <span className="group-action" onClick={(e) => e.stopPropagation()}>
            {action}
          </span>
        ) : null}
        <span className="group-chevron" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
      </div>
      {open ? <div className="group-body">{children}</div> : null}
    </section>
  );
}
