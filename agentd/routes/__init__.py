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
            int(float(expr))
        except ValueError:
            raise HTTPException(400, "interval 表达式需为秒数（如 3600）") from None
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
    return out
