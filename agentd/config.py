"""配置管理：LLM、权限模式、服务选项。

配置文件位于 $AGENT_HOME/config.json（默认 ~/.agent/termux-agent/config.json）。
API Key 只存在本地文件里，任何 API 回传时都会打码。

P1-2：字段类型强制 / 范围收敛改由 pydantic 模型完成（替代 PUT /api/settings 里
手写的一堆 if/else clamp）。对外仍保持 Settings.get()/save(patch)/llm()/permission_mode()
的字典式接口，main.py / agent.py 调用处零改动。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import home_dir as _home_dir

logger = logging.getLogger(__name__)

# 国产 LLM 厂商预设（OpenAI 兼容协议）。base_url 与 model 均可按需修改。
PRESETS = [
    {
        "id": "doubao",
        "label": "豆包 · 火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-250615",
        "note": "火山方舟控制台创建推理接入点后，用接入点 ID 作为 model",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "note": "官方当前默认模型；legacy 名 deepseek-chat 已弃用，另支持 deepseek-v4-pro（更强）",
    },
    {
        "id": "qwen",
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "阿里云百炼开通后使用；高级用法可换业务空间专属域名 {WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    },
    {
        "id": "kimi",
        "label": "Kimi · 月之暗面",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",
        "note": "官方当前默认 kimi-k3（另有 kimi-k2.6）；旧 moonshot-v1 系列仍兼容",
    },
    {
        "id": "glm",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "glm-4-flash 有免费额度；最新为 glm-5.3",
    },
    {
        "id": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "note": "",
    },
    {
        "id": "custom",
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "model": "",
        "note": "填你自己的 base_url 与模型名",
    },
]

DEFAULTS = {
    "llm": {
        "provider": "custom",
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    # auto = 全放行（不推荐） | approve = 写操作与危险操作需确认（推荐） | chat = 纯聊天（不调用工具）
    "permission_mode": "approve",
    # plan = 先输出计划文本并停止，用户确认后再执行 | act = 直接执行工具（默认）
    "agent_mode": "act",
    # LiteLLM 统一网关（可选）：一份代码调用 100+ LLM，自动故障转移，成本追踪
    "litellm": {},
    # Mem0 本地记忆（可选）：向量 + 图混合记忆，跨会话语义搜索
    # 开启前需 pip install mem0ai chromadb fastembed
    # 完全本地运行（ChromaDB + FastEmbed ONNX + LiteLLM/OpenAI 抽取 LLM），不依赖 mem0 云服务
    # {
    #   "mem0": {
    #     "enabled": true,
    #     "embedding_model": "BAAI/bge-small-zh-v1.5",  # 中文优先；英文用 BAAI/bge-small-en-v1.5
    #     "llm_provider": "openai",                      # 抽取 LLM（与 settings.llm 同配置）
    #     "llm_model": "",                               # 留空则复用 settings.llm.model
    #     "search_limit": 5,                             # 对话时最多注入几条记忆
    #     "custom_instructions": "关注用户偏好、固定习惯、长期项目、姓名/联系信息"
    #   }
    # }
    "mem0": {},
    # mcp：可选接入的 MCP 服务器 [{name, command, args}]（零依赖 stdio 客户端）
    # tg_token / tg_chat_id：可选的 Telegram 远程控制（token 属敏感信息，API 回传打码）
    "server": {
        "allow_lan": False,
        "token": "",
        "approval_timeout": 120,
        "mcp": [],
        "tg_token": "",
        "tg_chat_id": "",
        "use_litellm": False,
    },
    # 用户长期偏好：跨会话注入 system（存在本机，零依赖）
    "user_prefs": "",
    # 【可配置】memory：自动摘要分页 + 检索参数
    "memory": {
        "auto_summarize": True,        # 每 N 轮自动把早期消息压缩为长期记忆
        "summarize_every_n_turns": 10,  # 触发摘要的轮数
        "summary_max_chars": 400,       # 摘要最大字符数
        "search_limit": 5,              # 对话时最多注入几条检索结果
        "fts_page_size": 50,            # FTS 回填分页大小（影响启动速度）
        "retention_days": 90,           # 记忆保留天数（0 = 永久）
    },
    # 【可配置】agent：工具执行 / 递归控制
    "agent": {
        "tool_timeout": 60,             # 工具执行超时（秒，传给 subprocess）
        "max_tool_retries": 2,          # 工具失败后最大重试次数
        "max_tool_chain_depth": 10,     # 单次请求最大工具调用链长度（防死循环）
    },
    # 【可配置】context：LLM 上下文 Token 预算控制
    "context": {
        "budget_tokens": 0,             # 上下文窗口 Token 预算（0 = 用模型默认值即 max_tokens）
        "auto_compress": True,          # 接近 budget 时自动压缩早期消息为摘要
        "compress_threshold": 0.8,      # 触发自动压缩的 budget 占用比例
    },
    # 【可配置】mcp_client：让 PocketMind 作为 MCP Client 接入外部 MCP Server
    "mcp_client": {
        "enabled": False,               # 是否启用 MCP 客户端角色
        "servers": [],                  # [{name, command, args, auto_connect}]
        "timeout": 15,                  # MCP 调用超时（秒）
    },
    # 【可配置】eval：promptfoo 评测偏好
    "eval": {
        "provider": "litellm",           # 评测使用的 provider（litellm / custom）
        "auto_run_on_startup": False,   # 启动时自动运行评测（需配置真实 API key）
    },
}

# ---------- pydantic 配置子模型 ----------


class MemorySettings(BaseModel):
    """记忆管理配置"""
    auto_summarize: bool = True
    summarize_every_n_turns: int = 10
    summary_max_chars: int = 400
    search_limit: int = 5
    fts_page_size: int = 50
    retention_days: int = 90

    @field_validator("summarize_every_n_turns", mode="before")
    @classmethod
    def _clamp_turns(cls, v):
        try:
            return max(3, min(50, int(v)))
        except (TypeError, ValueError):
            return 10

    @field_validator("summary_max_chars", mode="before")
    @classmethod
    def _clamp_chars(cls, v):
        try:
            return max(100, min(2000, int(v)))
        except (TypeError, ValueError):
            return 400

    @field_validator("retention_days", mode="before")
    @classmethod
    def _clamp_retention(cls, v):
        try:
            return max(0, min(3650, int(v)))
        except (TypeError, ValueError):
            return 90


class AgentSettings(BaseModel):
    """Agent 执行配置"""
    tool_timeout: int = 60
    max_tool_retries: int = 2
    max_tool_chain_depth: int = 10

    @field_validator("tool_timeout", mode="before")
    @classmethod
    def _clamp_timeout(cls, v):
        try:
            return max(5, min(300, int(v)))
        except (TypeError, ValueError):
            return 60

    @field_validator("max_tool_retries", mode="before")
    @classmethod
    def _clamp_retries(cls, v):
        try:
            return max(0, min(5, int(v)))
        except (TypeError, ValueError):
            return 2

    @field_validator("max_tool_chain_depth", mode="before")
    @classmethod
    def _clamp_depth(cls, v):
        try:
            return max(3, min(50, int(v)))
        except (TypeError, ValueError):
            return 10


class ContextSettings(BaseModel):
    """上下文 Token 预算控制"""
    budget_tokens: int = 0
    auto_compress: bool = True
    compress_threshold: float = 0.8

    @field_validator("budget_tokens", mode="before")
    @classmethod
    def _clamp_budget(cls, v):
        try:
            return max(0, min(200000, int(v)))
        except (TypeError, ValueError):
            return 0

    @field_validator("compress_threshold", mode="before")
    @classmethod
    def _clamp_threshold(cls, v):
        try:
            return max(0.5, min(0.95, float(v)))
        except (TypeError, ValueError):
            return 0.8


class MCPClientSettings(BaseModel):
    """MCP 客户端配置（让 PocketMind 接入外部 MCP Server）"""
    enabled: bool = False
    servers: list = []
    timeout: int = 15

    @field_validator("timeout", mode="before")
    @classmethod
    def _clamp_timeout(cls, v):
        try:
            return max(3, min(120, int(v)))
        except (TypeError, ValueError):
            return 15


class EvalSettings(BaseModel):
    """PromptFoo 评测偏好"""
    provider: str = "litellm"
    auto_run_on_startup: bool = False


# ---------- pydantic 配置模型（P1-2：类型强制 + 范围收敛） ----------
class LLMSettings(BaseModel):
    """LLM 连接参数。temperature / max_tokens 用 validator 做「收敛」而非「报错」，
    与原手写 clamp（max/min）行为一致：越界值被夹到边界，而不是 422 拒绝。"""

    provider: str = "custom"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096

    @field_validator("temperature", mode="before")
    @classmethod
    def _clamp_temperature(cls, v):
        # 原手写：max(0.0, min(2.0, float(v)))
        try:
            return max(0.0, min(2.0, float(v)))
        except (TypeError, ValueError):
            return 0.3

    @field_validator("max_tokens", mode="before")
    @classmethod
    def _clamp_max_tokens(cls, v):
        # 原手写：max(256, min(65536, int(v)))
        try:
            return max(256, min(65536, int(v)))
        except (TypeError, ValueError):
            return 4096


class MCPServerCfg(BaseModel):
    """单个 MCP 服务器配置。"""

    name: str = "mcp"
    command: str = ""
    args: list[str] = []


class ServerSettings(BaseModel):
    """服务端选项。approval_timeout 原手写：>0 且 min(.,600)。"""

    allow_lan: bool = False
    token: str = ""
    approval_timeout: int = 120
    mcp: list[MCPServerCfg] = []
    # Telegram 远程控制：bot token 与允许访问的 chat_id 白名单（均留空即不启用）
    tg_token: str = ""
    tg_chat_id: str = ""
    # LiteLLM 网关：开启后所有 LLM 调用走 litellm.acompletion()
    use_litellm: bool = False

    @field_validator("approval_timeout", mode="before")
    @classmethod
    def _clamp_timeout(cls, v):
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return 120
        if iv <= 0:
            return 120
        return min(iv, 600)


class AppSettings(BaseSettings):
    """整份配置的校验模型。继承 pydantic-settings 的 BaseSettings 以满足统一模型定义，
    但数据来自本地 config.json（env_file=None 不读环境变量），多余字段忽略。"""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    llm: LLMSettings = LLMSettings()
    # 注意：以下两个字段故意用 str + before 校验器做「兜底」而非 Literal：
    # 若用 Literal，一旦配置里出现非法值，整个 model_validate 会抛 ValidationError，
    # 导致 _coerce 整体回退到未收敛的原始 dict——连 approval_timeout/temperature 等
    # 其它字段的 clamp 也被一并跳过（BUG-2：非法 permission_mode 会让 -5 的超时漏过）。
    permission_mode: str = "approve"
    # plan = 先输出计划文本并停止，用户确认后再执行（参考 Cline Plan/Act）
    # act  = 现状，直接执行工具
    agent_mode: str = "act"
    server: ServerSettings = ServerSettings()
    user_prefs: str = ""

    @field_validator("permission_mode", mode="before")
    @classmethod
    def _sanitize_permission_mode(cls, v):
        return v if v in ("auto", "approve", "chat") else "approve"

    @field_validator("agent_mode", mode="before")
    @classmethod
    def _sanitize_agent_mode(cls, v):
        return v if v in ("plan", "act") else "act"


home_dir = _home_dir  # 复用 paths.home_dir 单一实现


class Settings:
    """配置读写：保留 JSON 文件存储 + 深度合并 patch；落盘前用 pydantic 模型校验。"""

    def __init__(self, path: Path | None = None):
        self.path = path or (home_dir() / "config.json")
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = self._merge(dict(DEFAULTS), loaded)
                else:
                    self.data = dict(DEFAULTS)
            else:
                self.data = dict(DEFAULTS)
        except Exception:
            self.data = dict(DEFAULTS)
        # 无论是否已有配置文件，都过一遍 pydantic 模型：补齐新字段默认值（如 agent_mode）、
        # 收敛类型。否则首次启动（无 config.json）时新增字段会缺失。
        self.data = self._coerce(self.data)

    @staticmethod
    def _merge(base: dict, patch: dict) -> dict:
        out = dict(base)
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = Settings._merge(out[k], v)
            else:
                out[k] = v
        return out

    @staticmethod
    def _coerce(data: dict) -> dict:
        """用 AppSettings 模型做类型强制 / 范围收敛（替代手写字段 clamp）。

        校验失败时回退到合并后的原始 dict，保证坏配置不会让服务起不来；
        具体字段的合法性在调用处（如 permission_mode()）仍有兜底。
        """
        try:
            validated = AppSettings.model_validate(data).model_dump()
            # 合并而非整体替换：AppSettings 只覆盖核心字段，
            # 扩展配置块（memory/agent/context/mcp_client/litellm/mem0/eval）
            # 不在模型内，必须原样保留，否则用户配置会被默认值覆盖。
            for k, v in validated.items():
                data[k] = v
            return data
        except ValidationError as e:
            logger.warning("Config validation failed, using raw fallback: %s", e)
            # 安全兜底：即使整体校验失败，收敛关键数值字段，防止极端值破坏服务
            llm = data.get("llm") if isinstance(data, dict) else {}
            if isinstance(llm, dict):
                try:
                    llm["temperature"] = max(0.0, min(2.0, float(llm.get("temperature", 0.3))))
                except (TypeError, ValueError):
                    llm["temperature"] = 0.3
            server = data.get("server") if isinstance(data, dict) else {}
            if isinstance(server, dict):
                try:
                    server["approval_timeout"] = max(0, min(3600, int(server.get("approval_timeout", 120))))
                except (TypeError, ValueError):
                    server["approval_timeout"] = 120
            return data

    def get(self) -> dict:
        return self.data

    def save(self, patch: dict) -> dict:
        self.data = self._coerce(self._merge(self.data, patch))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)  # 数据目录 700（含数据库）
        except OSError:
            pass
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.data

    def llm(self) -> dict:
        return self.data["llm"]

    def litellm_cfg(self) -> dict:
        """LiteLLM 专用配置。返回 {} 表示未启用。"""
        return self.data.get("litellm", {})

    def use_litellm(self) -> bool:
        """是否走 LiteLLM 统一网关。"""
        return bool(
            self.data.get("server", {}).get("use_litellm", False)
            and self.data.get("litellm", {})
        )

    # ---------- Mem0 本地记忆 ----------
    def mem0_cfg(self) -> dict:
        """Mem0 配置块。返回 {} 表示未启用。"""
        return self.data.get("mem0", {})

    def use_mem0(self) -> bool:
        """是否启用 Mem0 本地记忆。需同时配置了 enabled=true 与可用的 LLM。"""
        m = self.data.get("mem0", {})
        if not isinstance(m, dict):
            return False
        if not m.get("enabled", False):
            return False
        # 抽取 LLM 必须可用：要么用户配置了 llm_model，要么 settings.llm 有 api_key
        if m.get("llm_model"):
            return True
        llm = self.data.get("llm", {})
        return bool(llm.get("api_key") and llm.get("model") and llm.get("base_url"))

    def permission_mode(self) -> str:
        m = self.data.get("permission_mode", "approve")
        return m if m in ("auto", "approve", "chat") else "approve"

    def agent_mode(self) -> str:
        """plan / act；非法值回退到 act（直接执行，保持现状不阻塞）。"""
        m = self.data.get("agent_mode", "act")
        return m if m in ("plan", "act") else "act"

    # ---------- 新增可配置 section 访问器 ----------

    def memory_cfg(self) -> dict:
        """记忆管理配置（auto_summarize / summarize_every_n_turns / fts_page_size / retention_days）。"""
        base = DEFAULTS.get("memory", {}).copy()
        base.update(self.data.get("memory", {}))
        return MemorySettings(**base).model_dump()

    def agent_cfg(self) -> dict:
        """Agent 执行配置（tool_timeout / max_tool_retries / max_tool_chain_depth）。"""
        base = DEFAULTS.get("agent", {}).copy()
        base.update(self.data.get("agent", {}))
        return AgentSettings(**base).model_dump()

    def context_cfg(self) -> dict:
        """上下文 Token 预算控制（budget_tokens / auto_compress / compress_threshold）。"""
        base = DEFAULTS.get("context", {}).copy()
        base.update(self.data.get("context", {}))
        return ContextSettings(**base).model_dump()

    def mcp_client_cfg(self) -> dict:
        """MCP 客户端配置。dict 始终返回 enabled / servers / timeout。"""
        base = DEFAULTS.get("mcp_client", {}).copy()
        base.update(self.data.get("mcp_client", {}))
        return MCPClientSettings(**base).model_dump()

    def eval_cfg(self) -> dict:
        """评测偏好（provider / auto_run）。"""
        base = DEFAULTS.get("eval", {}).copy()
        base.update(self.data.get("eval", {}))
        return EvalSettings(**base).model_dump()


def mask_key(key: str) -> str:
    """打码 API Key：短 key（≤8）全遮；中等长度保留首尾 2 位；长 key 保留首尾 4 位。"""
    if not key:
        return ""
    n = len(key)
    if n <= 8:
        return "*" * n
    head = tail = 4 if n > 16 else 2
    return key[:head] + "*" * (n - head - tail) + key[-tail:]
