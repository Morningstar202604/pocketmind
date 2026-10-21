import { useCallback, useEffect, useState } from "react";

/**
 * 环境自检面板：把「为什么它不工作」直接翻译成「你现在该做什么」。
 *
 * 设计取向：
 * - **只在有问题时主动打扰**（设置面板里随时可看）。一切正常时收起，不占地方。
 * - 每条 fail/warn 都带一句可照做的修复动作，而不是只报错。
 * - 不做自动修复：删文件 / 改权限 / 装包这类动作必须用户自己点、
 *   自己知道后果——静默自愈比报错更危险。
 */
export interface DiagItem {
  id: string;
  title: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  fix: string;
}

export interface DiagReport {
  ok: boolean;
  summary: string;
  items: DiagItem[];
}

const ICON: Record<DiagItem["status"], string> = { ok: "✓", warn: "!", fail: "✕" };

export function DiagnosticsPanel({ api }: { api: { get: <T>(p: string) => Promise<T> } }) {
  const [report, setReport] = useState<DiagReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.get<DiagReport>("/api/diagnostics");
      // 防御：旧版后端没有这个端点，或异常时返回 {}。缺 items 时不能直接
      // filter，否则整个设置面板白屏（实测：单元测试的 get mock 返回 {}）。
      if (r && Array.isArray(r.items)) {
        setReport(r);
      } else {
        setReport(null);
        setErr("当前后端不支持自检（版本较旧），不影响其他功能");
      }
    } catch (e) {
      setErr((e as Error).message || "自检失败");
    } finally {
      setBusy(false);
    }
  }, [api]);

  // 打开面板即自检一次：用户点进来通常就是遇到问题了
  useEffect(() => {
    void run();
  }, [run]);

  const problems = report?.items?.filter((i) => i.status !== "ok") ?? [];

  return (
    <div className="diag">
      <div className="diag-head">
        <span className="diag-title">环境自检</span>
        <span className={`diag-summary ${report && !report.ok ? "bad" : problems.length ? "warn" : "ok"}`}>
          {busy ? "检测中…" : err ? err : (report?.summary ?? "")}
        </span>
        <button className="btn sm" onClick={() => void run()} disabled={busy}>
          重新检测
        </button>
      </div>

      {problems.length > 0 && (
        <ul className="diag-list">
          {problems.map((i) => (
            <li key={i.id} className={`diag-item ${i.status}`}>
              <span className="diag-icon" aria-hidden="true">{ICON[i.status]}</span>
              <div className="diag-body">
                <div className="diag-line">
                  <strong>{i.title}</strong>
                  <span className="diag-detail">{i.detail}</span>
                </div>
                {i.fix && <div className="diag-fix">→ {i.fix}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}

      {report && problems.length === 0 && (
        <p className="hint">全部检查通过：Python 版本、数据目录、存储空间、端口、外网、模型配置均正常。</p>
      )}
    </div>
  );
}
