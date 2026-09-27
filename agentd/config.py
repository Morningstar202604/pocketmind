"""配置管理：LLM、权限模式、服务选项。

配置文件位于 $AGENT_HOME/config.json（默认 ~/.agent/termux-agent/config.json）。
API Key 只存在本地文件里，任何 API 回传时都会打码。

P1-2：字段类型强制 / 范围收敛改由 pydantic 模型完成（替代 PUT /api/settings 里
手写的一堆 if/else clamp）。对外仍保持 Settings.get()/save(patch)/llm()/permission_mode()
的字典式接口，main.py / agent.py 调用处零改动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    # mcp：可选接入的 MCP 服务器 [{name, command, args}]（零依赖 stdio 客户端）
    # tg_token / tg_chat_id：可选的 Telegram 远程控制（token 属敏感信息，API 回传打码）
    "server": {
        "allow_lan": False,
        "token": "",
        "approval_timeout": 120,
        "mcp": [],
        "tg_token": "",
        "tg_chat_id": "",
    },
    # 用户长期偏好：跨会话注入 system（存在本机，零依赖）
    "user_prefs": "",
}


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
    permission_mode: Literal["auto", "approve", "chat"] = "approve"
    # plan = 先输出计划文本并停止，用户确认后再执行（参考 Cline Plan/Act）
    # act  = 现状，直接执行工具
    agent_mode: Literal["plan", "act"] = "act"
    server: ServerSettings = ServerSettings()
    user_prefs: str = ""


def home_dir() -> Path:
    """数据目录（配置 + 数据库）。"""
    h = os.environ.get("AGENT_HOME")
    return Path(h).expanduser() if h else Path.home() / ".agent" / "termux-agent"


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
            return AppSettings.model_validate(data).model_dump()
        except ValidationError:
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
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.data

    def llm(self) -> dict:
        return self.data["llm"]

    def permission_mode(self) -> str:
        m = self.data.get("permission_mode", "approve")
        return m if m in ("auto", "approve", "chat") else "approve"

    def agent_mode(self) -> str:
        """plan / act；非法值回退到 act（直接执行，保持现状不阻塞）。"""
        m = self.data.get("agent_mode", "act")
        return m if m in ("plan", "act") else "act"


def mask_key(key: str) -> str:
    """打码 API Key：短 key（≤8）全遮；中等长度保留首尾 2 位；长 key 保留首尾 4 位。"""
    if not key:
        return ""
    n = len(key)
    if n <= 8:
        return "*" * n
    head = tail = 4 if n > 16 else 2
    return key[:head] + "*" * (n - head - tail) + key[-tail:]
