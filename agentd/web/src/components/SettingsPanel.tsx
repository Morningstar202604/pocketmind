import { useEffect, useState, type ReactNode } from "react";
import { api, isUnauthorized, setToken, type AppSettings, type Provider, type ToolInfo } from "../api";
import { useAgentStore } from "../store/useAgentStore";
import type { ThemePref } from "../theme";
import { IconClose } from "./icons";

const MODES: Array<{ v: "auto" | "approve" | "chat"; label: string; hint: string }> = [
  { v: "auto", label: "全自动", hint: "所有工具直接执行（不推荐）" },
  { v: "approve", label: "逐次审批", hint: "写文件/危险操作需你确认（推荐）" },
  { v: "chat", label: "纯聊天", hint: "不调用任何工具" },
];

// 智能体工作模式（Cline 式 Plan/Act）：
// plan = 先输出执行计划待你批准；act = 收到任务直接执行
const AGENT_MODES: Array<{ v: "plan" | "act"; label: string; hint: string }> = [
  { v: "plan", label: "先计划", hint: "先给出执行计划，你批准后再动手" },
  { v: "act", label: "直接执行", hint: "收到任务即开始调用工具" },
];

const RISK_LABEL: Record<string, string> = {
  safe: "只读",
  write: "需确认",
  danger: "危险",
};

/** 工具分组展示顺序与名称 */
const GROUP_NAMES: Array<{ id: string; label: string }> = [
  { id: "phone", label: "手机能力" },
  { id: "files", label: "文件" },
  { id: "system", label: "系统" },
  { id: "voice", label: "语音" },
  { id: "mcp", label: "MCP 扩展" },
  { id: "other", label: "其他" },
];

const THEMES: Array<{ v: ThemePref; label: string; swatch: ReactNode }> = [
  {
    v: "dark",
    label: "深色",
    swatch: (
      <svg width="46" height="30" viewBox="0 0 46 30">
        <rect width="46" height="30" rx="7" fill="#161922" />
        <rect x="6" y="7" width="20" height="4" rx="2" fill="#e4e7ec" opacity="0.9" />
        <rect x="6" y="15" width="34" height="4" rx="2" fill="#9aa3b2" opacity="0.5" />
        <rect x="6" y="22" width="14" height="3" rx="1.5" fill="#10b981" />
      </svg>
    ),
  },
  {
    v: "light",
    label: "浅色",
    swatch: (
      <svg width="46" height="30" viewBox="0 0 46 30">
        <rect width="46" height="30" rx="7" fill="#ffffff" stroke="#e0e5ec" />
        <rect x="6" y="7" width="20" height="4" rx="2" fill="#1a1f2a" opacity="0.85" />
        <rect x="6" y="15" width="34" height="4" rx="2" fill="#5a6472" opacity="0.4" />
        <rect x="6" y="22" width="14" height="3" rx="1.5" fill="#0d9d6e" />
      </svg>
    ),
  },
  {
    v: "system",
    label: "跟随系统",
    swatch: (
      <svg width="46" height="30" viewBox="0 0 46 30">
        <rect width="23" height="30" rx="7" fill="#161922" />
        <rect x="23" width="23" height="30" rx="7" fill="#ffffff" stroke="#e0e5ec" />
        <rect x="6" y="7" width="14" height="4" rx="2" fill="#e4e7ec" opacity="0.9" />
        <rect x="28" y="7" width="12" height="4" rx="2" fill="#1a1f2a" opacity="0.85" />
        <rect x="6" y="15" width="16" height="4" rx="2" fill="#9aa3b2" opacity="0.5" />
        <rect x="28" y="15" width="12" height="4" rx="2" fill="#5a6472" opacity="0.4" />
        <rect x="6" y="22" width="10" height="3" rx="1.5" fill="#10b981" />
        <rect x="28" y="22" width="10" height="3" rx="1.5" fill="#0d9d6e" />
      </svg>
    ),
  },
];

export function SettingsPanel({
  onClose,
  mock,
  theme,
  onTheme,
  onExport,
}: {
  onClose: () => void;
  mock: boolean;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onExport: () => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [form, setForm] = useState<AppSettings | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");
  const [showKey, setShowKey] = useState(false);
  // 401 时不再无限停在「加载中…」：给出令牌输入口，写入本地后重新拉取
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [loadError, setLoadError] = useState("");

  // Plan/Act 模式直接走 store：切换即持久化（局部 PUT，不随「保存」按钮走）
  const agentMode = useAgentStore((s) => s.agentMode);
  const setAgentMode = useAgentStore((s) => s.setAgentMode);

  const load = async () => {
    setLoadError("");
    const [s, p, t] = await Promise.allSettled([
      api.get<AppSettings>("/api/settings"),
      api.get<{ providers: Provider[] }>("/api/providers"),
      api.get<{ tools: ToolInfo[] }>("/api/tools"),
    ]);
    if (s.status === "fulfilled") {
      setForm(s.value);
      setNeedToken(false);
    } else if (isUnauthorized(s.reason)) {
      setNeedToken(true);
    } else {
      setLoadError((s.reason as Error)?.message || "设置加载失败");
    }
    if (p.status === "fulfilled") setProviders(p.value.providers);
    if (t.status === "fulfilled") setTools(t.value.tools);
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!form) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="sheet" onClick={(e) => e.stopPropagation()}>
          <div className="sheet-head">
            <h2>设置</h2>
            <button className="icon-btn" onClick={onClose} aria-label="关闭">
              <IconClose />
            </button>
          </div>
          {needToken ? (
            <div className="group">
              <p className="group-title">需要访问令牌</p>
              <p className="hint">
                本服务已设置访问令牌（局域网保护）。在这里输入一次令牌即可打开设置；
                忘记令牌可在设备上执行 <code>python -m agentd.main --clear-token</code> 清除。
              </p>
              <div className="field">
                <label>访问令牌</label>
                <input
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="与设置中一致的服务令牌"
                />
              </div>
              <button
                className="btn"
                onClick={async () => {
                  setToken(tokenInput.trim());
                  await load();
                }}
              >
                解锁设置
              </button>
            </div>
          ) : (
            <p className="center-muted">{loadError || "加载中…"}</p>
          )}
        </div>
      </div>
    );
  }

  const pickProvider = (id: string) => {
    const p = providers.find((x) => x.id === id);
    if (!p) return;
    setForm((f) => (f ? { ...f, llm: { ...f.llm, provider: id, base_url: p.base_url, model: p.model } } : f));
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult("");
    try {
      const r = await api.post<{ ok: boolean; mode?: string; model?: string; error?: string }>(
        "/api/llm/test"
      );
      if (r.ok) {
        setTestResult(r.model ? `连接成功（${r.model}）` : "连接成功");
      } else {
        setTestResult(`连接失败：${r.error || "未知错误"}`);
      }
    } catch (e) {
      setTestResult(`请求失败：${(e as Error).message || "未知错误"}`);
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      await api.put("/api/settings", {
        llm: form.llm,
        permission_mode: form.permission_mode,
        server: {
          token: form.server?.token ?? "",
          approval_timeout: Number(form.server?.approval_timeout ?? 120),
          // MCP 列表必须一起提交：此前只发 token/approval_timeout，
          // 在面板里改的 MCP 服务器会被静默丢弃（后端其实支持写 mcp）
          mcp: form.server?.mcp ?? [],
        },
        mcp_client: form.mcp_client ?? { enabled: false, servers: [], timeout: 15 },
        user_prefs: form.user_prefs ?? "",
      });
      // 局域网令牌同步到本地，后续请求自动携带
      setToken((form.server?.token ?? "").trim());
      setMsg("已保存");
      setTimeout(onClose, 800);
    } catch (e) {
      setMsg((e as Error).message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>设置</h2>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose />
          </button>
        </div>

        {mock && <p className="mock-banner">当前是离线演示模式（--mock），下面的配置不影响演示。</p>}

        {/* 外观 */}
        <div className="group">
          <p className="group-title">外观</p>
          <div className="field">
            <label>主题</label>
            <div className="theme-row">
              {THEMES.map((t) => (
                <label key={t.v} className={`theme ${theme === t.v ? "on" : ""}`}>
                  <input
                    type="radio"
                    name="theme"
                    checked={theme === t.v}
                    onChange={() => onTheme(t.v)}
                  />
                  {t.swatch}
                  {t.label}
                </label>
              ))}
            </div>
          </div>
        </div>

        {/* 智能体工作模式：Plan 先出计划待批准 / Act 直接执行 */}
        <div className="group">
          <p className="group-title">智能体模式</p>
          <div className="field">
            <label>工作方式</label>
            <div className="mode-row">
              {AGENT_MODES.map((m) => (
                <label key={m.v} className={`mode ${agentMode === m.v ? "on" : ""}`}>
                  <input
                    type="radio"
                    name="agent_mode"
                    checked={agentMode === m.v}
                    onChange={() => setAgentMode(m.v)}
                  />
                  <b>{m.label}</b>
                  <span>{m.hint}</span>
                </label>
              ))}
            </div>
            <p className="hint">「先计划」下，复杂任务会先给你一张计划卡片，批准后才真正执行。</p>
          </div>
        </div>

        {/* 模型 */}
        <div className="group">
          <p className="group-title">模型</p>
          <div className="field">
            <label>模型厂商</label>
            <select value={form.llm.provider} onChange={(e) => pickProvider(e.target.value)}>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            {providers.find((p) => p.id === form.llm.provider)?.note && (
              <p className="hint">{providers.find((p) => p.id === form.llm.provider)?.note}</p>
            )}
          </div>
          <div className="field">
            <label>Base URL</label>
            <input
              value={form.llm.base_url}
              onChange={(e) => setForm((f) => (f ? { ...f, llm: { ...f.llm, base_url: e.target.value } } : f))}
              placeholder="https://…/v1"
            />
          </div>
          <div className="field">
            <label>模型名</label>
            <input
              value={form.llm.model}
              onChange={(e) => setForm((f) => (f ? { ...f, llm: { ...f.llm, model: e.target.value } } : f))}
              placeholder="例如 deepseek-flash"
            />
          </div>
          <div className="field">
            <label>API Key</label>
            <div className="key-row">
              <input
                type={showKey ? "text" : "password"}
                value={form.llm.api_key}
                onChange={(e) => setForm((f) => (f ? { ...f, llm: { ...f.llm, api_key: e.target.value } } : f))}
                placeholder={form.llm.api_key ? "已配置（留空则不修改）" : "sk-…"}
              />
              <button className="btn sm" onClick={() => setShowKey((v) => !v)}>
                {showKey ? "隐藏" : "显示"}
              </button>
            </div>
          </div>
          <div className="field">
            <label>Temperature · {form.llm.temperature.toFixed(1)}</label>
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={form.llm.temperature}
              onChange={(e) =>
                setForm((f) => (f ? { ...f, llm: { ...f.llm, temperature: Number(e.target.value) } } : f))
              }
            />
            <p className="hint">低 = 更稳定听话，高 = 更有创意</p>
            <div className="llm-test-row">
              <button className="btn sm" onClick={() => void testConnection()} disabled={testing}>
                {testing ? "测试中…" : "测试连接"}
              </button>
              {testResult && (
                <span className={testResult.startsWith("连接成功") ? "llm-test-ok" : "llm-test-err"}>
                  {testResult}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* 权限与安全 */}
        <div className="group">
          <p className="group-title">权限与安全</p>
          <div className="field">
            <label>工具权限</label>
            <div className="mode-row">
              {MODES.map((m) => (
                <label key={m.v} className={`mode ${form.permission_mode === m.v ? "on" : ""}`}>
                  <input
                    type="radio"
                    name="permission_mode"
                    checked={form.permission_mode === m.v}
                    onChange={() => setForm((f) => (f ? { ...f, permission_mode: m.v } : f))}
                  />
                  <b>{m.label}</b>
                  <span>{m.hint}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="field">
            <label>局域网访问令牌（可选）</label>
            <input
              value={form.server?.token ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, server: { ...f.server, token: e.target.value } } : f))}
              placeholder="留空 = 仅本机可访问"
            />
            <p className="hint">只要此处非空，所有 /api 请求（含本机）都必须带 Authorization: Bearer &lt;令牌&gt;；忘记令牌可在设备上执行 python -m agentd.main --clear-token 清除。</p>
          </div>
          <div className="field">
            <label>审批超时（秒）</label>
            <input
              type="number"
              min={5}
              max={600}
              value={form.server?.approval_timeout ?? 120}
              onChange={(e) =>
                setForm((f) =>
                  f ? { ...f, server: { ...f.server, approval_timeout: Number(e.target.value) || 120 } } : f
                )
              }
            />
            <p className="hint">等待审批超过该时长自动拒绝（安全优先）</p>
          </div>
        </div>

        {/* MCP 服务器（可选接入） */}
        <div className="group">
          <p className="group-title">MCP 服务器（可选）</p>
          <div className="field">
            <label>接入列表（每行一个：名称 | 启动命令 | 参数）</label>
            <textarea
              rows={3}
              value={(form.server?.mcp ?? [])
                .map((m) => [m.name, m.command, ...(m.args || [])].join(" | "))
                .join("\n")}
              onChange={(e) => {
                const rows = e.target.value
                  .split("\n")
                  .map((l) => l.trim())
                  .filter(Boolean)
                  .map((l) => {
                    const [name = "", command = "", ...rest] = l.split("|").map((x) => x.trim());
                    // 参数段再按空白切开：占位示例写的是「-y pkg /sdcard」这类多参数，
                    // 整段原样透传会让 MCP 客户端把三个参数当成一个 argv，服务器直接起不来
                    return {
                      name: name || "mcp",
                      command,
                      args: rest.flatMap((r) => r.split(/\s+/).filter(Boolean)),
                    };
                  })
                  .filter((m) => m.command);
                setForm((f) => (f ? { ...f, server: { ...f.server, mcp: rows } } : f));
              }}
              placeholder={"本地demo | python3 | tests/mcp_demo_server.py\n文件系统 | npx | -y @modelcontextprotocol/server-filesystem /sdcard"}
            />
            <p className="hint">
              MCP 是通用工具协议（Linux 基金会托管）。每行一个：名称 | 命令 | 参数。保存重启后自动连接，其工具会出现在「手机能力」里（默认需审批）。
            </p>
          </div>
        </div>

        {/* MCP Client（PocketMind 调用外部 MCP 服务器） */}
        <div className="group">
          <p className="group-title">MCP Client（调用外部 MCP）</p>
          <div className="field">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.mcp_client?.enabled ?? false}
                onChange={(e) =>
                  setForm((f) =>
                    f ? { ...f, mcp_client: { ...(f.mcp_client || {}), enabled: e.target.checked } } : f
                  )
                }
              />
              启用 MCP Client（让 PocketMind 调用外部 MCP Server）
            </label>
            <p className="hint">
              服务器列表每行一个：名称 | 命令 | 参数。保存后需重启服务生效，连接成功后其工具会出现在「MCP 扩展」分组（默认需审批）。
            </p>
            <textarea
              rows={3}
              value={(form.mcp_client?.servers ?? [])
                .map((s) => [s.name, s.command, ...(s.args || [])].join(" | "))
                .join("\n")}
              onChange={(e) => {
                const rows = e.target.value
                  .split("\n")
                  .map((l) => l.trim())
                  .filter(Boolean)
                  .map((l) => {
                    const [name = "", command = "", ...rest] = l.split("|").map((x) => x.trim());
                    return {
                      name: name || "mcp",
                      command,
                      args: rest.flatMap((r) => r.split(/\s+/).filter(Boolean)),
                      auto_connect: true,
                    };
                  })
                  .filter((s) => s.command);
                setForm((f) => (f ? { ...f, mcp_client: { ...(f.mcp_client || {}), servers: rows } } : f));
              }}
              placeholder={"github | npx | -y @modelcontextprotocol/server-github"}
            />
            <p className="hint">需先安装对应 MCP Server 依赖（如 npx 包），且外部服务器需可从手机访问。</p>
          </div>
        </div>

        {/* 长期偏好（跨会话记忆） */}
        <div className="group">
          <p className="group-title">长期偏好</p>
          <div className="field">
            <label>我希望 Agent 记住的事</label>
            <textarea
              rows={4}
              value={form.user_prefs ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, user_prefs: e.target.value } : f))}
              placeholder={"例如：\n· 回复用简体中文，简洁直接\n· 我常用支付宝，涉及支付时提醒我\n· 我是学生，白天上课，晚上有空"}
            />
            <p className="hint">这段文字会写进每次对话的系统提示（存在本机，不会上传）。</p>
          </div>
        </div>

        {/* 工具能力（按分组展示） */}
        <div className="group">
          <div className="group-head">
            <p className="group-title">工具能力（{tools.length} 项）</p>
            <button className="btn mini" onClick={onExport} title="把所有会话与配置导出为 JSON 文件">
              导出数据备份
            </button>
          </div>
          <div className="field">
            {GROUP_NAMES.map((g) => {
              const items = tools.filter((t) => (t.group || "other") === g.id);
              if (items.length === 0) return null;
              return (
                <div key={g.id} className="tool-group">
                  <p className="tool-group-name">{g.label}（{items.length}）</p>
                  <ul className="tools-list">
                    {items.map((t) => (
                      <li key={t.name} className={`tool-item ${t.risk}`}>
                        <code>{t.name}</code>
                        <span className="tool-risk">{RISK_LABEL[t.risk] ?? t.risk}</span>
                        <p>{t.summary}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
            <p className="hint">「手机能力」依赖 termux-api：pkg install termux-api。「系统」为 shell 命令（危险）。文件操作（写/删）执行前自动备份，可在对话里一键撤销。</p>
          </div>
        </div>

        {/* 数据备份 */}
        <div className="group">
          <p className="group-title">数据备份</p>
          <div className="field">
            <p className="hint">
              一键把全部会话与设置导出为 JSON 文件（不含 API 密钥），用于本地备份留存；文件保存在你下载的位置。
            </p>
            <button className="btn" onClick={onExport}>导出全部数据</button>
          </div>
        </div>

        <div className="sheet-foot">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={() => void save()} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
        {msg && <p className="save-msg">{msg}</p>}
      </div>
    </div>
  );
}
