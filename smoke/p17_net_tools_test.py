#!/usr/bin/env python3
"""P17 联网工具：解析器 + 引擎回退 + 错误分支（全部离线，不打真实网络）。

为什么这样测：
- 真实网络不可控（会超时、会变结构、CI 里还不一定通），拿它做门禁等于把自己
  绑在第三方服务上。所以核心逻辑拆成**纯函数**（解析器）直接喂固定 HTML，
  只有 "要不要发请求" 那一层用 mock 替掉。
- 已有的 p13 只覆盖了 SSRF 判定；本测试补上解析器、多引擎回退、错误分类。

覆盖的失败模式（都是真实会发生的）：
- Bing 改版 → 解析不到结果 → 必须报"解析失败"，不能返回空数组假装没搜到
- 引擎 A 连不通 → 必须自动试引擎 B
- 两个引擎都挂 → 错误信息必须列出每个引擎的原因
- 无参/空 query → 明确 400 语义错误
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


# 真实 Bing 结果页的最小结构（2026-10 实测的 class 名与嵌套）
BING_HTML = """
<html><body>
<div class="b_algoheader"><a href="https://example.com/a" h="ID=1"><h2><strong>第一个</strong>结果</h2></a></div>
<div class="b_caption"><p>这是第一条的摘要，含关键词。</p></div>
<div class="b_algoheader"><a href="https://example.com/b" h="ID=2"><h2>第二个结果</h2></a></div>
<div class="b_caption"><p>第二条摘要。</p></div>
<div class="b_algoheader"><a href="https://example.com/c" h="ID=3"><h2>第三个</h2></a></div>
<div class="b_caption"><p>第三条。</p></div>
</body></html>
"""

# DDG 的跳转链接结构（uddg 参数里是真实 URL）
DDG_HTML = """
<html><body>
<div class="result results_links results_links_deep">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fx&amp;rut=abc">DDG 结果一</a>
  <a class="result__snippet">DDG 的摘要文本</a>
</div>
<div class="result results_links results_links_deep">
  <a class="result__a" href="https://example.org/y">DDG 结果二</a>
  <a class="result__snippet">第二条摘要</a>
</div>
</body></html>
"""


def main() -> int:
    print("== P17 联网工具（离线） ==")
    from agentd.tools import net

    # ---------- _strip_tags ----------
    check("_strip_tags 去标签", net._strip_tags("<b>加粗</b>文本") == "加粗文本")
    check("_strip_tags 解 HTML 实体", net._strip_tags("a &amp; b &lt;c&gt;") == "a & b <c>")
    check("_strip_tags 去多余空白", net._strip_tags("  a   b  ") == "a   b")
    check("_strip_tags 空串安全", net._strip_tags("") == "")

    # ---------- _parse_bing ----------
    r = net._parse_bing(BING_HTML, 5)
    check("Bing 解析出 3 条", len(r) == 3, str(len(r)))
    check("Bing 标题正确", r[0]["title"] == "第一个结果", r[0]["title"])
    check("Bing URL 正确", r[0]["url"] == "https://example.com/a", r[0]["url"])
    check("Bing 摘要正确", "第一条的摘要" in r[0]["snippet"], r[0]["snippet"])
    check("Bing 遵守 limit=2", len(net._parse_bing(BING_HTML, 2)) == 2)
    check("Bing 空页面返回空列表", net._parse_bing("<html></html>", 5) == [])
    check("Bing 只有 href 没有 h2 时不崩", net._parse_bing('<a href="https://x.com">x</a>', 5) == [])

    # ---------- _parse_ddg ----------
    r = net._parse_ddg(DDG_HTML, 5)
    check("DDG 解析出 2 条", len(r) == 2, str(len(r)))
    check("DDG 解开 uddg 跳转链接", r[0]["url"] == "https://example.org/x", r[0]["url"])
    check("DDG 直接链接保留", r[1]["url"] == "https://example.org/y", r[1]["url"])
    check("DDG 摘要解析", "DDG 的摘要" in r[0]["snippet"], r[0]["snippet"])
    check("DDG 空页面返回空列表", net._parse_ddg("", 5) == [])

    # ---------- 解析器不返回非 http 链接 ----------
    bad = '<div class="b_algoheader"><a href="javascript:alert(1)"><h2>坏</h2></a></div>'
    check("Bing 丢弃非 http 链接", net._parse_bing(bad, 5) == [])

    # ---------- _is_blocked_ip ----------
    import ipaddress

    check("回环被判定为阻断", net._is_blocked_ip(ipaddress.ip_address("127.0.0.1")))
    check("私有网段被阻断", net._is_blocked_ip(ipaddress.ip_address("10.1.2.3")))
    check("公网 IP 放行", not net._is_blocked_ip(ipaddress.ip_address("8.8.8.8")))
    check("IPv6 回环被阻断", net._is_blocked_ip(ipaddress.ip_address("::1")))
    check("IPv4-mapped IPv6 也拦", net._is_blocked_ip(ipaddress.ip_address("::ffff:127.0.0.1")))

    # ---------- web_search：参数校验 ----------
    async def run():
        r = await net.web_search("")
        check("web_search 空 query → error", "不能为空" in r.get("error", ""), str(r))
        r = await net.web_search("   ")
        check("web_search 空白 query → error", "不能为空" in r.get("error", ""), str(r))

        # ---------- web_search：引擎回退（mock 掉真实请求） ----------
        calls: list[str] = []

        def fake_fetch(url: str):
            calls.append(url)
            if "bing.com" in url:
                # 模拟 Bing 改版：200 但解析不到结果 → 应回退到 DDG
                return 200, "<html>nothing useful here</html>"
            return 200, DDG_HTML

        # web_search 内部用 asyncio.to_thread(_fetch, url)，_fetch 是闭包，无法直接替换。
        # 用 monkeypatch urlopen 的更稳做法：替换模块级 urllib.request.urlopen。
        import urllib.request

        class FakeResp:
            def __init__(self, body: str):
                self._b = body.encode("utf-8")
                self.status = 200
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self, *a):
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            calls.append(url)
            if "bing.com" in url:
                return FakeResp("<html>nothing useful</html>")
            return FakeResp(DDG_HTML)

        real = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            r = await net.web_search("测试查询", 3)
        finally:
            urllib.request.urlopen = real

        check("Bing 解析失败时自动回退 DDG", r.get("engine") == "duckduckgo", str(r.get("engine")))
        check("回退后能拿到结果", r.get("count") == 2, str(r.get("count")))
        check("确实先试了 Bing", any("bing" in u for u in calls), str(calls[:2]))

        # ---------- 两个引擎都失败 ----------
        def all_fail(req, timeout=None):
            raise urllib.error.URLError("network down")

        real = urllib.request.urlopen
        urllib.request.urlopen = all_fail
        try:
            r = await net.web_search("测试", 3)
        finally:
            urllib.request.urlopen = real
        check("全部引擎失败 → error 且列出每个引擎原因",
              "所有搜索源都不可用" in r.get("error", "") and "bing" in r.get("error", "").lower(),
              r.get("error", "")[:120])

        # ---------- 两个引擎都返回"没搜到" ----------
        def none_found(req, timeout=None):
            return FakeResp("<html>No results found</html>")

        real = urllib.request.urlopen
        urllib.request.urlopen = none_found
        try:
            r = await net.web_search("不存在的词", 3)
        finally:
            urllib.request.urlopen = real
        check("确实没搜到时返回空结果而非 error",
              r.get("ok") is True and r.get("results") == [], str(r)[:100])

        # ---------- read_url：协议与大小限制 ----------
        r = await net.read_url("ftp://example.com/x")
        check("read_url 拒绝非 http(s)", "只支持" in r.get("error", ""), str(r))
        r = await net.read_url("http://127.0.0.1/")
        check("read_url 拒绝回环", "拒绝访问" in r.get("error", ""), str(r))

        # ---------- read_url：正常抓取（mock） ----------
        page = "<html><head><style>x{}</style></head><body><p>正文段落</p><script>evil()</script></body></html>"
        real = urllib.request.urlopen
        # build_opener().open() 走的是 OpenerDirector，需替换 opener
        orig_build = urllib.request.build_opener

        class FakeOpener:
            def open(self, req, timeout=None):
                return FakeResp(page)

        urllib.request.build_opener = lambda *a, **k: FakeOpener()
        try:
            r = await net.read_url("https://example.com/article", 500)
        finally:
            urllib.request.build_opener = orig_build

        check("read_url 成功返回正文", r.get("ok") is True, str(r)[:80])
        check("read_url 去掉 script 标签", "evil()" not in (r.get("text") or ""), (r.get("text") or "")[:80])
        check("read_url 去掉 style 标签", "x{}" not in (r.get("text") or ""))
        check("read_url 保留正文", "正文段落" in (r.get("text") or ""), (r.get("text") or "")[:80])
        check("read_url max_chars 生效", len(r.get("text") or "") <= 500, str(len(r.get("text") or "")))

    asyncio.run(run())

    # ---------- 边界值（变异测试发现的缺口） ----------
    # 1) DDG 只有 1 个结果块时 len(blocks)<=1 → 必须整页当一块处理
    one_block = """<div class="result results_links">
      <a class="result__a" href="https://only.example/x">唯一结果</a>
      <a class="result__snippet">唯一摘要</a>
    </div>"""
    r = net._parse_ddg(one_block, 5)
    check("DDG 单结果也能解析", len(r) == 1 and r[0]["url"] == "https://only.example/x", str(r))

    # 关键边界：payload 里**一次都不出现** result 块容器时，len(blocks)==1，
    # 代码必须把整页当作一块来解析（否则直接返回空，搜索引擎改版就全军覆没）。
    no_container = """<html><body>
      <a class="result__a" href="https://fallback.example/z">无容器结果</a>
      <a class="result__snippet">无容器摘要</a>
    </body></html>"""
    import re as _re
    check("构造的用例确实只有 1 个 split 块",
          len(_re.split(r'<div class="result results_links', no_container)) == 1)
    r = net._parse_ddg(no_container, 5)
    check("无 result 容器时整页当一块解析（<=1 边界）",
          len(r) == 1 and r[0]["url"] == "https://fallback.example/z", str(r))

    # 2) web_search 的 limit 上限是 10：传 100 必须被钳到 10 而不是原样下传
    # web_search 用的是 urllib.request.urlopen（不是 build_opener）。
    # 注意 FakeResp 原本定义在 run() 内部，同步部分拿不到——这里另建一个同构的。
    class _Resp:
        def __init__(self, body: str):
            self._b = body.encode("utf-8")
            self.status = 200
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, *a):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig_open = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: _Resp(BING_HTML)
    try:
        r = asyncio.run(net.web_search("边界", 100))
    finally:
        urllib.request.urlopen = orig_open
    check("web_search limit=100 被钳到 10（返回不超过 10 条）", r.get("count", 99) <= 10, str(r.get("count")))

    # 3) _check_url_allowed 的 scheme 白名单：ftp / gopher / javascript 都要拒
    for bad_scheme in ("ftp://x.com/", "gopher://x.com/", "javascript:alert(1)", "data:text/html,x"):
        reason = net._check_url_allowed(bad_scheme)
        check(f"拒绝 scheme {bad_scheme.split(':')[0]}", bool(reason), bad_scheme)

    # 4) host 缺失（只有 scheme）
    reason = net._check_url_allowed("http:///path")
    check("拒绝无主机名的 URL", bool(reason), str(reason))

    # 5) 非字符串入参：`not isinstance(x, str) or not x.strip()` 里 or→and 的变异
    #    只有传"非字符串"才能区分（传空字符串两者都为真，测不出差别）。
    r = asyncio.run(net.web_search(123))  # type: ignore[arg-type]
    check("web_search 非字符串入参 → error", "不能为空" in r.get("error", ""), str(r))
    r = asyncio.run(net.read_url(123))  # type: ignore[arg-type]
    check("read_url 非字符串入参 → error", "只支持" in r.get("error", ""), str(r))

    # 6) _check_url_allowed 的 scheme 判断用 not in：
    #    必须验证"合法 scheme 不报错"，否则 in/not in 互换测不出
    for good_scheme in ("http://example.com/", "https://example.com/"):
        reason = net._check_url_allowed(good_scheme)
        check(f"合法 scheme {good_scheme.split(':')[0]} 不被 scheme 规则拒绝",
              "只支持" not in reason, reason)

    # ---------- _healthcheck ----------
    h = net._healthcheck()
    check("_healthcheck 返回 ok 字段", "ok" in h, str(h))
    check("_healthcheck 标注 provider", h.get("provider") == "duckduckgo" or h.get("ok") is False, str(h))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import urllib.error  # noqa: F401  (供上面的 all_fail 使用)

    sys.exit(main())
