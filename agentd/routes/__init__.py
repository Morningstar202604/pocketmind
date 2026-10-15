"""routes/validators — 无状态纯函数，从 main.py 拆出以便独立单测。"""
from __future__ import annotations

from fastapi import HTTPException

from ..config import DEFAULTS, mask_key
from ..scheduler import TRIGGER_TYPES, parse_condition


def validate_job_payload(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()[:60]
    tt = str(payload.get("trigger_type", "")).strip()
    expr = str(payload.get("expr", "")).strip()
    message = str(payload.get("message", "")).strip()
    if not (name and tt in TRIGGER_TYPES and expr and message):
        raise HTTPException(400, "缺少必要字段：name / trigger_type(cron|interval|date) / expr / message")
    if tt == "interval":
        try:
            val = float(expr)
            # inf/nan 会让 int() 抛 OverflowError（不是 ValueError），
            # 旧实现只捕 ValueError，于是 "inf"/"1e999" 直接 500 而不是 400
            if val != val or val in (float("inf"), float("-inf")):
                raise ValueError("非有限数")
            if val <= 0 or val > 365 * 86400:
                raise ValueError("超出范围")
        except (ValueError, OverflowError):
            raise HTTPException(400, "interval 表达式需为 1~31536000 之间的秒数（如 3600）") from None
    if tt == "date":
        try:
            __import__("datetime").datetime.strptime(expr, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(400, "date 表达式格式：YYYY-MM-DD HH:MM") from None
    cond = str(payload.get("condition", "")).strip()
    if cond and parse_condition(cond) is None:
        raise HTTPException(400, "条件格式：battery < 20（仅支持电池电量阈值）")
    return {
        "name": name, "trigger_type": tt, "expr": expr, "message": message,
        "session_id": str(payload.get("session_id", "")).strip(),
        "condition": cond, "enabled": bool(payload.get("enabled", True)),
    }

def masked_settings(s: dict) -> dict:
    """对外回传的配置副本：api_key / server.token / server.tg_token 一律打码。

    缺失字段由 DEFAULTS 补全（兼容旧版配置文件没有新增 memory/agent/context/mcp_client/eval 字段的情况）。
    """
    out: dict = {}
    # 先铺 DEFAULTS，再用实际值覆盖（确保新增字段可见）
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            merged = dict(v)
            merged.update(s.get(k, {}) or {})
            out[k] = merged
        else:
            out[k] = s.get(k, v)
    # 丢掉 DEFAULTS 没有的旧字段（防止幽灵字段）
    # 打码敏感字段
    llm = out.get("llm", {})
    llm["api_key"] = mask_key(llm.get("api_key", ""))
    server = out.get("server", {})
    if server.get("token"):
        server["token"] = mask_key(server["token"])
    if server.get("tg_token"):
        server["tg_token"] = mask_key(server["tg_token"])
    out["llm"] = llm
    out["server"] = server

    # litellm.api_key：/api/stats 已专门剔除它，这里同样必须打码
    litellm = out.get("litellm")
    if isinstance(litellm, dict) and litellm.get("api_key"):
        litellm = dict(litellm)
        litellm["api_key"] = mask_key(litellm["api_key"])
        out["litellm"] = litellm

    # MCP 客户端每个服务器的 env 常放第三方令牌（GITHUB_TOKEN 之类）——
    # 这是「配一次忘一次」的凭据，明文回传等于把外部账号交出去，必须整体打码
    mcpc = out.get("mcp_client")
    if isinstance(mcpc, dict) and isinstance(mcpc.get("servers"), list):
        masked_servers = []
        for srv in mcpc["servers"]:
            if not isinstance(srv, dict):
                continue
            srv2 = dict(srv)
            env = srv2.get("env")
            if isinstance(env, dict) and env:
                srv2["env"] = {k: mask_key(str(v)) for k, v in env.items()}
            masked_servers.append(srv2)
        mcpc = dict(mcpc)
        mcpc["servers"] = masked_servers
        out["mcp_client"] = mcpc
    return out
