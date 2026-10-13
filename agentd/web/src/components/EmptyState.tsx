import { IconBattery, IconBell, IconClipboard, IconFolder } from "./icons";
import { PocketLogo } from "./PocketLogo";

const TASKS = [
  { icon: <IconBattery size={18} />, c: "green", label: "查看电池电量", prompt: "查看手机电池电量" },
  { icon: <IconFolder size={18} />, c: "blue", label: "列出当前目录", prompt: "列出当前目录" },
  { icon: <IconBell size={18} />, c: "amber", label: "发一条通知", prompt: "在通知栏发一条「口袋智灵已就绪」的通知" },
  { icon: <IconClipboard size={18} />, c: "cyan", label: "读取剪贴板", prompt: "读取剪贴板内容" },
];

export function EmptyState({
  onPick,
  ready,
  onOpenSettings,
}: {
  onPick: (t: string) => void;
  ready: boolean;
  onOpenSettings: () => void;
}) {
  return (
    <div className="empty">
      <div className="empty-logo">
        <PocketLogo size={88} />
      </div>
      <h2>你好，我是口袋智灵</h2>
      <p className="empty-sub">
        {ready
          ? "我在你的手机上本地运行，可以读文件、执行命令、调用手机能力。"
          : "尚未配置 LLM。配置模型后，我就能调用手机能力帮你干活。"}
      </p>
      {ready ? (
        <div className="tasks">
          {TASKS.map((t) => (
            <button key={t.label} className="task" onClick={() => onPick(t.prompt)}>
              <span className={`task-icon ${t.c}`}>{t.icon}</span>
              {t.label}
            </button>
          ))}
        </div>
      ) : (
        <button className="btn primary empty-setup" onClick={onOpenSettings}>
          前往设置
        </button>
      )}
    </div>
  );
}