#!/usr/bin/env python3
"""P13 SSRF 防护：read_url 不得访问内网 / 回环 / 云元数据 / 任意端口。

真实缺陷（审查发现，已复现）：
read_url 原本只校验 URL 以 http(s) 开头，于是模型（或被注入的模型）可以
  read_url("http://127.0.0.1:8787/api/export")
直接读出本机所有会话数据，随下一次 LLM 请求离开设备。
还能读 169.254.169.254 的云元数据（云主机上等于拿到临时凭据）。

本测试只测**判定逻辑**（不发真实请求），所以不需要网络。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P13 SSRF 防护 ==")
    from agentd.tools.net import _check_url_allowed

    must_block = [
        ("回环 IPv4", "http://127.0.0.1/"),
        ("回环 localhost", "http://localhost/"),
        ("私有 10.x", "http://10.0.0.1/"),
        ("私有 192.168", "http://192.168.1.1/"),
        ("私有 172.16", "http://172.16.0.1/"),
        ("云元数据", "http://169.254.169.254/latest/meta-data/"),
        ("链路本地", "http://169.254.1.1/"),
        ("IPv6 回环", "http://[::1]/"),
        ("0.0.0.0", "http://0.0.0.0/"),
        ("C 类保留 192.0.2", "http://192.0.2.1/"),
        ("组播", "http://224.0.0.1/"),
        ("非白名单端口 8787", "http://example.com:8787/"),
        ("非白名单端口 22", "http://example.com:22/"),
        ("非 http 协议 file", "file:///etc/passwd"),
        ("非 http 协议 gopher", "gopher://127.0.0.1:6379/_INFO"),
        ("无主机", "http:///path"),
    ]
    for name, url in must_block:
        reason = _check_url_allowed(url)
        check(f"拦截 {name}", bool(reason), f"{url} 未被拦截")

    must_allow = [
        ("普通 http 80", "http://example.com/"),
        ("普通 https 443", "https://example.com/page"),
        ("允许 8080", "http://example.com:8080/"),
        ("允许 8443", "https://example.com:8443/"),
    ]
    for name, url in must_allow:
        # example.com 可能解析失败（离线环境）也算"非拦截"——只要求不是"内网拦截"
        reason = _check_url_allowed(url)
        check(f"放行 {name}", "内网" not in reason and "端口" not in reason, f"{url} -> {reason}")

    # DNS 解析型绕过：域名解析到内网必须被拦
    # （127.0.0.1.nip.io 是公开的通配 DNS，无需联网即可解析到 127.0.0.1；
    #  离线时解析失败也返回拒绝原因，同样算通过）
    reason = _check_url_allowed("http://127.0.0.1.nip.io/")
    check("拦截 DNS 解析到回环的域名", bool(reason), "未被拦截")

    # 重定向处理器存在（逐跳校验）
    import agentd.tools.net as net

    check("存在重定向逐跳校验处理器", hasattr(net, "_SafeRedirectHandler"))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
