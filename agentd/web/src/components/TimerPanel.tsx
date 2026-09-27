import { useEffect, useState } from "react";
import {
  applyRecipe,
  createJob,
  deleteJob,
  getJobs,
  getRecipes,
  updateJob,
  type Job,
  type Recipe,
} from "../api";
import { IconClose, IconTimer } from "./icons";

const TRIGGERS: Array<{ v: Job["trigger_type"]; label: string; hint: string; ph: string }> = [
  { v: "interval", label: "间隔", hint: "每隔 N 秒", ph: "3600" },
  { v: "cron", label: "定时", hint: "标准 5 段 cron", ph: "0 8 * * *" },
  { v: "date", label: "一次性", hint: "到点执行一次", ph: "2026-10-01 09:00" },
];

const TRIGGER_LABEL: Record<string, string> = {
  interval: "每隔 ",
  cron: "cron ",
  date: "到点 ",
};

/**
 * 把配方/任务的触发表达式翻成人类可读文案。
 * 纯函数，便于单测：interval 秒数 → 每 N 分钟/小时；简单 cron → 每天 HH:MM。
 */
export function humanizeTrigger(t: { trigger_type: string; expr: string }): string {
  if (t.trigger_type === "interval") {
    const sec = Number(t.expr) || 0;
    if (sec >= 3600 && sec % 3600 === 0) return `每 ${sec / 3600} 小时`;
    if (sec >= 60 && sec % 60 === 0) return `每 ${sec / 60} 分钟`;
    return `每 ${sec} 秒`;
  }
  if (t.trigger_type === "cron") {
    const parts = t.expr.trim().split(/\s+/);
    // 形如「分 时 * * *」= 每天定点，直接翻译成每天 HH:MM
    if (parts.length === 5 && parts[2] === "*" && parts[3] === "*" && parts[4] === "*") {
      const hh = parts[1].padStart(2, "0");
      const mm = parts[0].padStart(2, "0");
      return `每天 ${hh}:${mm}`;
    }
    return `cron ${t.expr}`;
  }
  return `到点 ${t.expr}`;
}

export function TimerPanel({ onClose }: { onClose: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [applyingId, setApplyingId] = useState("");
  const [msg, setMsg] = useState("");
  const [form, setForm] = useState({
    name: "",
    trigger_type: "interval" as Job["trigger_type"],
    expr: "",
    message: "",
    condition: "",
  });

  const load = async () => {
    try {
      setJobs(await getJobs());
    } catch (e) {
      setMsg((e as Error).message || "加载失败");
    }
  };

  // 拉取配方列表：页面打开即获取；接口尚未就绪（后端并行开发）时静默降级为空
  const loadRecipes = async () => {
    try {
      setRecipes(await getRecipes());
    } catch {
      setRecipes([]);
    }
  };

  useEffect(() => {
    void load();
    void loadRecipes();
  }, []);

  // 一键应用配方：创建定时任务后刷新任务列表与配方状态
  const apply = async (r: Recipe) => {
    setMsg("");
    setApplyingId(r.id);
    try {
      await applyRecipe(r.id);
      setRecipes((rs) => rs.map((x) => (x.id === r.id ? { ...x, applied: true } : x)));
      setMsg("已创建定时任务");
      await load();
    } catch (e) {
      setMsg((e as Error).message || "创建失败");
    } finally {
      setApplyingId("");
    }
  };

  const submit = async () => {
    setMsg("");
    if (!form.name.trim() || !form.expr.trim() || !form.message.trim()) {
      setMsg("请填名称、触发表达式和执行内容");
      return;
    }
    try {
      await createJob({
        name: form.name.trim(),
        trigger_type: form.trigger_type,
        expr: form.expr.trim(),
        message: form.message.trim(),
        condition: form.condition.trim(),
      });
      setForm({ name: "", trigger_type: "interval", expr: "", message: "", condition: "" });
      setMsg("已创建");
      await load();
    } catch (e) {
      setMsg((e as Error).message || "创建失败");
    }
  };

  const toggle = async (j: Job) => {
    try {
      await updateJob(j.id, !j.enabled);
      await load();
    } catch (e) {
      setMsg((e as Error).message || "操作失败");
    }
  };

  const remove = async (j: Job) => {
    try {
      await deleteJob(j.id);
      await load();
    } catch (e) {
      setMsg((e as Error).message || "删除失败");
    }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>
            <IconTimer size={16} /> 定时任务
          </h2>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose />
          </button>
        </div>

        <div className="group">
          <p className="group-title">配方（一键创建）</p>
          {recipes.length === 0 ? (
            <p className="hint">暂无预置配方。</p>
          ) : (
            <ul className="tools-list">
              {recipes.map((r) => (
                <li key={r.id} className="tool-item safe">
                  <code>
                    {r.name}
                    {r.applied ? "（已启用）" : ""}
                  </code>
                  <span className={`tool-risk ${r.applied ? "safe" : ""}`}>
                    {r.applied ? "运行中" : "未应用"}
                  </span>
                  <div className="recipe-desc">
                    <p>{r.description}</p>
                    <p className="hint">触发：{humanizeTrigger(r)}</p>
                    <div className="job-actions">
                      {r.applied ? (
                        <button className="btn sm" disabled title="对应任务已在下方列表">
                          已启用 · 查看任务
                        </button>
                      ) : (
                        <button
                          className="btn sm primary"
                          disabled={applyingId === r.id}
                          onClick={() => void apply(r)}
                        >
                          {applyingId === r.id ? "创建中…" : "一键创建"}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="group">
          <p className="group-title">已有任务（{jobs.length}）</p>
          {jobs.length === 0 ? (
            <p className="hint">还没有定时任务。定时任务到点后会自动新建/复用会话执行，结果可在会话里回看。</p>
          ) : (
            <ul className="tools-list">
              {jobs.map((j) => (
                <li key={j.id} className="tool-item safe">
                  <code>
                    {j.name}
                    {j.enabled ? "" : "（已停用）"}
                  </code>
                  <span className="tool-risk">{j.enabled ? "运行中" : "已停用"}</span>
                  <p>
                    {TRIGGER_LABEL[j.trigger_type]}
                    {j.expr}
                    {j.condition ? ` · 条件 ${j.condition}` : ""}
                    {j.next_run ? ` · 下次 ${j.next_run.replace("T", " ").slice(5, 16)}` : ""}
                  </p>
                  <div className="job-actions">
                    <button className="btn sm" onClick={() => void toggle(j)}>
                      {j.enabled ? "停用" : "启用"}
                    </button>
                    <button className="btn sm danger" onClick={() => void remove(j)}>
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="group">
          <p className="group-title">新建任务</p>
          <div className="field">
            <label>名称</label>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="例如：每天早上看天气"
            />
          </div>
          <div className="field">
            <label>触发方式</label>
            <div className="mode-row">
              {TRIGGERS.map((t) => (
                <label key={t.v} className={`mode ${form.trigger_type === t.v ? "on" : ""}`}>
                  <input
                    type="radio"
                    name="trigger_type"
                    checked={form.trigger_type === t.v}
                    onChange={() => setForm((f) => ({ ...f, trigger_type: t.v }))}
                  />
                  <b>{t.label}</b>
                  <span>{t.hint}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="field">
            <label>触发表达式</label>
            <input
              value={form.expr}
              onChange={(e) => setForm((f) => ({ ...f, expr: e.target.value }))}
              placeholder={TRIGGERS.find((t) => t.v === form.trigger_type)?.ph}
            />
            <p className="hint">
              {form.trigger_type === "cron" && "5 段：分 时 日 月 周（如 0 8 * * * = 每天 8 点）"}
              {form.trigger_type === "interval" && "秒数（如 3600 = 每小时）"}
              {form.trigger_type === "date" && "格式：YYYY-MM-DD HH:MM"}
            </p>
          </div>
          <div className="field">
            <label>到点要执行的任务</label>
            <textarea
              rows={2}
              value={form.message}
              onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
              placeholder="例如：看一下电池电量；查一下明天的天气"
            />
          </div>
          <div className="field">
            <label>触发条件（可选）</label>
            <input
              value={form.condition}
              onChange={(e) => setForm((f) => ({ ...f, condition: e.target.value }))}
              placeholder="battery < 20（电量低于 20% 才执行）"
            />
            <p className="hint">留空 = 每次到点都执行</p>
          </div>
          <button className="btn primary" onClick={() => void submit()}>
            创建任务
          </button>
        </div>

        {msg && <p className="save-msg">{msg}</p>}
      </div>
    </div>
  );
}
