"""知识库工具：用户资料的显式检索入口（对标 Cherry Studio 的知识库设计）。

设计要点（照官方思路抄）：
- **不作为上下文自动注入**。知识库是「用户策展的长文档」，自动塞进 system 会
  污染上下文、顶掉会话记忆；检索必须是模型显式发起的工具调用。
- 存储与检索完全复用本机 SQLite + FTS5（trigram，中文子串可命中），
  与会话记忆同一套降级策略（FTS 不可用自动回退 LIKE），不引任何向量依赖。
- kb_add 需要审批（write）：内容会长期影响后续回答，属于要用户把关的写入。
"""
from __future__ import annotations

from . import Tool, ctx, register

ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "条目标题（如「长江保修卡」「家庭地址」）"},
        "content": {"type": "string", "description": "要保存的正文（可多行，≤20000 字）"},
    },
    "required": ["title", "content"],
}

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索词（关键词即可，支持中文子串）"},
        "limit": {"type": "integer", "description": "最多返回几条，默认 5"},
    },
    "required": ["query"],
}


def _store():
    return ctx.current_store.get()


async def kb_add(title: str, content: str) -> dict:
    store = _store()
    if store is None:
        return {"error": "当前环境没有知识库（未在会话上下文中）"}
    text = str(content or "").strip()
    if not text:
        return {"error": "content 不能为空"}
    if len(text) > 20000:
        return {"error": "单条内容过长（>20000 字），请拆分后再存"}
    # 标题回退只算一次：此前"存库用未命名、返回用原始空串"，
    # 导致 kb_list 里显示空标题（实测发现的不一致）。
    safe_title = str(title or "").strip()[:120] or "未命名"
    kid = await store.kb_add(safe_title, text)
    return {"ok": True, "id": kid, "title": safe_title, "chars": len(text)}


async def kb_search(query: str, limit: int = 5) -> dict:
    store = _store()
    if store is None:
        return {"error": "当前环境没有知识库（未在会话上下文中）"}
    q = str(query or "").strip()
    if not q:
        return {"error": "query 不能为空"}
    n = max(1, min(int(limit or 5), 20))
    hits = await store.kb_search(q, limit=n)
    return {"ok": True, "count": len(hits),
            "results": [{"id": h["id"], "title": h["title"], "content": h["content"][:2000]}
                        for h in hits]}


async def kb_list() -> dict:
    store = _store()
    if store is None:
        return {"error": "当前环境没有知识库（未在会话上下文中）"}
    items = await store.kb_list()
    return {"ok": True, "count": len(items),
            "items": [{"id": i["id"], "title": i["title"], "chars": len(i["content"])}
                      for i in items]}


register(Tool(
    name="kb_add",
    description="把一段用户资料存入本机知识库（长期保存，之后可用 kb_search 检索）。",
    parameters=ADD_SCHEMA,
    risk="write",
    handler=kb_add,
    summary="写入知识库",
    group="knowledge",
))

register(Tool(
    name="kb_search",
    description="在本机知识库中检索用户资料（保修卡、地址、合同要点等）。需要用户资料时先检索再回答。",
    parameters=SEARCH_SCHEMA,
    risk="safe",
    handler=kb_search,
    summary="检索知识库",
    group="knowledge",
))

register(Tool(
    name="kb_list",
    description="列出知识库的全部条目（只看标题与长度，用于确认存了什么）。",
    parameters={"type": "object", "properties": {}},
    risk="safe",
    handler=kb_list,
    summary="列出知识库条目",
    group="knowledge",
))
