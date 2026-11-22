import type { ReactElement } from "react";
import { IconChat, IconCode, IconGrid, IconClock } from "./icons";

export type TabKey = "chat" | "workspace" | "capability" | "tasks";

const TABS: Array<{ key: TabKey; label: string; icon: (p: { size?: number }) => ReactElement }> = [
  { key: "chat", label: "对话", icon: IconChat },
  { key: "workspace", label: "工作区", icon: IconCode },
  { key: "capability", label: "能力", icon: IconGrid },
  { key: "tasks", label: "任务", icon: IconClock },
];

export function BottomTabs({
  active,
  onChange,
  badge,
  hidden,
}: {
  active: TabKey;
  onChange: (k: TabKey) => void;
  badge?: Partial<Record<TabKey, number>>;
  hidden?: TabKey[];
}) {
  const visible = hidden?.length ? TABS.filter((t) => !hidden.includes(t.key)) : TABS;
  return (
    <nav
      className="tabs"
      role="tablist"
      aria-label="主导航"
      data-count={visible.length}
      style={{ gridTemplateColumns: `repeat(${visible.length}, 1fr)` }}
    >
      {visible.map((t) => {
        const n = badge?.[t.key] ?? 0;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={active === t.key}
            className={`tab ${active === t.key ? "on" : ""}`}
            onClick={() => onChange(t.key)}
          >
            <span className="tab-icon">
              <t.icon size={22} />
              {n > 0 && <span className="tab-badge">{n > 99 ? "99+" : n}</span>}
            </span>
            <span className="tab-label">{t.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
