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

export interface CapItem {
  id: string;
  name: string;
  status: "available" | "unavailable" | "degraded" | "conditional";
  reason: string;
  workaround: string;
}

export interface CapMatrix {
  platform: { kind: string; abi: string; is_android: boolean; is_emulator: boolean };
  available: CapItem[];
  unavailable: CapItem[];
  degraded: CapItem[];
  summary: string;
}

export interface DiagReport {
  ok: boolean;
  summary: string;
  items: DiagItem[];
  capabilities?: CapMatrix;
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

      {report?.capabilities && <CapabilityList caps={report.capabilities} />}
    </div>
  );
}


/**
 * 能力清单：把「这台设备能做什么」讲在使用之前。
 *
 * 为什么需要：有些能力缺失不是配置问题，而是环境给不了（模拟器没有真短信、
 * PC 没有 termux-api、断网就没有实时信息）。用户不该发一句话才发现工具永远返回空。
 * 不可用项必须带「怎么办」，只写"不支持"等于把问题丢回给用户。
 */
function CapabilityList({ caps }: { caps: CapMatrix }) {
  const [open, setOpen] = useState(false);
  const platformLabel: Record<string, string> = {
    termux: "Android / Termux",
    container: "Docker 容器",
    windows: "Windows",
    linux: "Linux",
  };
  const label = platformLabel[caps.platform.kind] || caps.platform.kind;
  const bad = caps.unavailable.length;

  return (
    <div className="caps">
      <button className="caps-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="caps-title">设备能力</span>
        <span className="caps-summary">
          {label} · {caps.summary}
        </span>
        <span className={`caps-chev ${open ? "open" : ""}`} aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="caps-body">
          <div className="caps-sec">
            <div className="caps-sec-title ok">可用（{caps.available.length}）</div>
            <div className="caps-tags">
              {caps.available.map((c) => (
                <span key={c.id} className="cap-tag ok">{c.name}</span>
              ))}
            </div>
          </div>

          {caps.degraded.length > 0 && (
            <div className="caps-sec">
              <div className="caps-sec-title warn">有条件可用（{caps.degraded.length}）</div>
              {caps.degraded.map((c) => (
                <div key={c.id} className="cap-row warn">
                  <div className="cap-name">{c.name}</div>
                  {c.reason && <div className="cap-reason">{c.reason}</div>}
                  {c.workaround && <div className="cap-fix">→ {c.workaround}</div>}
                </div>
              ))}
            </div>
          )}

          {bad > 0 && (
            <div className="caps-sec">
              <div className="caps-sec-title bad">不可用（{bad}）</div>
              {caps.unavailable.map((c) => (
                <div key={c.id} className="cap-row bad">
                  <div className="cap-name">{c.name}</div>
                  {c.reason && <div className="cap-reason">{c.reason}</div>}
                  {c.workaround && <div className="cap-fix">→ {c.workaround}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
