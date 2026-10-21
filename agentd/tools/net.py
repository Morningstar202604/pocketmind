"""联网搜索工具：让 Agent 能查当前信息（对标 ChatGPT / Perplexity 的联网开关）。

设计取舍（为什么不是"直接爬网页"）：
- **默认关闭**。这是一个「数据不出本机」的产品，联网搜索会把用户的问题原文发给
  第三方搜索引擎——这必须是用户显式打开的开关，不能默认替用户决定。
- **只用标准库 urllib**，不引入 requests/httpx 之外的依赖（本仓库的依赖清单已经
  因为一个版本约束在本轮修复过一次，能不加就不加）。手机端 Termux 的 Python 自带
  ssl，够用。
- **结果只取标题 + 摘要 + 链接**，不抓正文：正文抓取要处理编码、反爬、超大页面，
  收益低而脆弱性高；摘要足够模型判断要不要继续追链接（后续可用 read_url 扩展）。
- **必须审批（write）**：查询词会离开设备，属于要用户点头的外发行为。

搜索后端按可达性排序尝试：Bing（国内多数网络可达，实测最稳）→ DuckDuckGo
HTML 端点（无需 API Key，但国内常被墙）。多引擎是刚需不是洁癖：本机实测
DuckDuckGo / 百度 HTML 均连不通，只挂一家等于功能在部分网络下直接失效。
解析失败要给出可执行的错误，而不是空数组假装成功。
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from . import Tool, register

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索关键词，用自然语言或几个词都可以"},
        "limit": {"type": "integer", "description": "最多返回几条，默认 5，上限 10"},
    },
    "required": ["query"],
}

_UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
)
_TIMEOUT = 12


def _strip_tags(s: str) -> str:
    """去标签 + 反转义实体。搜索结果里的摘要常带 <b> 高亮，直接塞给模型是噪音。"""
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _parse_bing(payload: str, limit: int) -> list[dict]:
    """从 Bing 结果页提取 [{title, url, snippet}]。

    实测结构（2026-10，Bing 中文站）：
        <div class="b_algoheader"><a href="URL"><h2>标题</h2></a></div>
        <div class="b_caption"><p>摘要</p></div>
    href 在 h2 的**外层** a 上，所以不能写成 `<h2>...<a href>`；
    按 b_algoheader 定位再取其后最近的一段 <p> 作摘要。
    """
    out: list[dict] = []
    for m in re.finditer(
        r'<div class="b_algoheader">\s*<a[^>]*href="([^"]+)"[^>]*>\s*(.*?)\s*</a>\s*</div>',
        payload, re.S,
    ):
        url = html.unescape(m.group(1))
        title = _strip_tags(m.group(2))
        # 摘要在这段之后的第一个 <p>（不跨越下一个结果块）
        tail = payload[m.end():m.end() + 3000]
        nxt = tail.find('class="b_algoheader"')
        if nxt > 0:
            tail = tail[:nxt]
        sn = re.search(r"<p[^>]*>(.*?)</p>", tail, re.S)
        snippet = _strip_tags(sn.group(1)) if sn else ""
        if title and url.startswith("http"):
            out.append({"title": title, "url": url, "snippet": snippet[:400]})
        if len(out) >= limit:
            break
    return out


def _parse_ddg(payload: str, limit: int) -> list[dict]:
    """从 DuckDuckGo HTML 结果页提取 [{title, url, snippet}]（备用引擎）。"""
    out: list[dict] = []
    blocks = re.split(r'<div class="result results_links', payload)
    if len(blocks) <= 1:
        blocks = [payload]
    for blk in blocks[1:] if len(blocks) > 1 else blocks:
        m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        url, title = m.group(1), _strip_tags(m.group(2))
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', blk, re.S)
        snippet = _strip_tags(sn.group(1)) if sn else ""
        if "uddg=" in url:
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                url = q.get("uddg", [url])[0]
            except Exception:  # noqa: BLE001 —— 链接解包失败就保留原样
                pass
        if url.startswith("//"):
            url = "https:" + url
        if title and url.startswith("http"):
            out.append({"title": title, "url": url, "snippet": snippet[:400]})
        if len(out) >= limit:
            break
    return out


async def web_search(query: str, limit: int = 5) -> dict:
    import asyncio

    if not isinstance(query, str) or not query.strip():
        return {"error": "搜索词不能为空"}
    limit = max(1, min(int(limit or 5), 10))
    q = query.strip()[:300]

    # 引擎按可达性排序：Bing 在多数网络（含国内）可达；DDG 作为备用。
    # 本机实测 DDG / 百度 HTML 均连不通，只挂一家会让功能在部分网络下直接失效。
    engines = [
        ("bing", "https://www.bing.com/search?setlang=zh-CN&q=" + urllib.parse.quote(q), _parse_bing),
        ("duckduckgo", "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q), _parse_ddg),
    ]
    errors: list[str] = []

    def _fetch(url: str) -> tuple[int, str]:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    for name, url, parser in engines:
        try:
            status, payload = await asyncio.to_thread(_fetch, url)
        except Exception as e:  # noqa: BLE001 —— 单个引擎失败要能继续试下一个
            reason = getattr(e, "reason", None) or type(e).__name__
            errors.append(f"{name}: {reason}")
            continue
        if status != 200:
            errors.append(f"{name}: HTTP {status}")
            continue
        results = parser(payload, limit)
        if results:
            return {"ok": True, "query": q, "engine": name, "count": len(results), "results": results}
        # 明确区分「没搜到」与「解析失败」
        lowered = payload.lower()
        if "no results" in lowered or "没有找到" in payload or "找不到" in payload:
            return {"ok": True, "query": q, "engine": name, "results": [], "note": "没有找到相关结果"}
        errors.append(f"{name}: 页面解析不到结果（结构可能已变更）")

    return {
        "error": "所有搜索源都不可用：" + "；".join(errors) + "。"
                 "请检查手机网络（含代理设置），或改用 read_url 直接读用户给的链接。",
        "query": q,
    }


register(Tool(
    name="web_search",
    description=(
        "联网搜索当前互联网信息（新闻、价格、文档、实时数据）。"
        "仅当用户明确要查网上信息、或问题涉及你知识截止之后的事实/本地无法得知的信息时调用。"
        "查询词会发送到外部搜索引擎，属于外发操作。返回标题、摘要与链接；"
        "需要正文时再用 read_url 打开具体链接。"
    ),
    parameters=SEARCH_SCHEMA,
    risk="write",
    handler=web_search,
    summary="联网搜索",
    group="net",
    timeout=20,
))


READ_URL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "要读取的网页地址（http/https）"},
        "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 4000，上限 20000"},
    },
    "required": ["url"],
}


async def read_url(url: str, max_chars: int = 4000) -> dict:
    """抓取网页正文的纯文本视图（去脚本/样式/标签）。

    不做 JS 渲染（手机上跑无头浏览器代价过高）；对静态页与多数文档站够用，
    对 SPA 会返回空——此时明确告知，而不是返回一堆脚本。
    """
    import asyncio

    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return {"error": "只支持 http/https 链接"}
    max_chars = max(200, min(int(max_chars or 4000), 20000))

    def _fetch() -> tuple[str, str]:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(2_000_000)  # 上限 2MB：手机内存与上下文都不允许无限抓
            enc = "utf-8"
            m = re.search(r"charset=([\w-]+)", ctype, re.I)
            if m:
                enc = m.group(1)
            return ctype, raw.decode(enc, errors="replace")

    try:
        ctype, body = await asyncio.to_thread(_fetch)
    except TimeoutError:
        return {"error": "读取超时"}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"无法连接：{e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读取失败：{type(e).__name__}: {e}"}

    if "json" in ctype:
        return {"ok": True, "url": url, "content_type": ctype, "text": body[:max_chars]}
    # 去 script/style，再压空白；保留段落换行便于模型阅读
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", body)
    text = _strip_tags(body)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return {
            "error": "页面没有可读正文（可能是纯前端渲染的 SPA，或需要登录）",
            "url": url,
        }
    return {"ok": True, "url": url, "content_type": ctype, "chars": len(text), "text": text[:max_chars]}


register(Tool(
    name="read_url",
    description=(
        "读取一个网页的正文文本（去脚本与标签）。用于在 web_search 得到链接后看详情，"
        "或用户直接给了一个网址要你总结。纯前端渲染的页面可能读不到内容。"
    ),
    parameters=READ_URL_SCHEMA,
    risk="safe",
    handler=read_url,
    summary="读取网页正文",
    group="net",
    timeout=25,
))


def _healthcheck() -> dict:
    """供 /api/health 展示联网能力是否可用（不发起真实请求，只看环境）。"""
    import ssl

    try:
        ssl.create_default_context()
        return {"ok": True, "provider": "duckduckgo", "note": "需要手机能访问外网"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
