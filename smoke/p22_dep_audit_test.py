#!/usr/bin/env python3
"""P22 依赖漏洞扫描：直接查 OSV.dev，不依赖 pip-audit 的 PyPI 通路。

## 为什么不用 pip-audit

实测本机：`pip-audit -r requirements.txt` 返回空且 exit 0。加 `-v` 才看出它
卡在解析依赖（要访问 pypi.org），而本机 `pypi.org` 完全不通 —— 它把
"网络失败"静默当成了"没有漏洞"。这是最危险的一类假绿。

改用 OSV.dev 的 REST API（实测可达）：按「包名 + 版本」逐条查，
返回 `{"vulns": [...]}` 或 `{}`。OSV 是 Google 维护的开源漏洞库，
覆盖 PyPI / npm 生态，数据源包含 GitHub Advisory。

## 覆盖范围

- Python：从 `agentd/requirements.txt` 解析出**实际安装版本**（用
  importlib.metadata 查已装的，查不到就跳过并报告）
- 前端：读 `agentd/web/package-lock.json` 的 dependencies 段

## 失败语义

- 有 Critical/High 漏洞 → 失败（退出 1）
- 有 Medium/Low → 只警告
- 查询失败（网络）→ **报告为"未扫描"，不算通过**，并在汇总里明确写出

这条很关键：不能因为查不到就报绿。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OSV = "https://api.osv.dev/v1/query"
TIMEOUT = 12

passed, failed, skipped = [], [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def installed_version(pkg: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def parse_requirements() -> list[tuple[str, str]]:
    """返回 [(包名, 已装版本)]。只查已安装的：未安装的依赖在部署时才装，
    拿不到确切版本就无法判断（而不是拿 requirements 的下界去猜）。"""
    out: list[tuple[str, str]] = []
    for raw in (ROOT / "agentd" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?", line)
        if not m:
            continue
        name = m.group(1)
        ver = installed_version(name)
        if ver:
            out.append((name, ver))
    return out


def osv_query(name: str, version: str, ecosystem: str = "PyPI") -> list[dict] | None:
    """返回漏洞列表；网络失败返回 None（区别于"没有漏洞"的空列表）。"""
    payload = json.dumps({
        "package": {"name": name, "ecosystem": ecosystem},
        "version": version,
    }).encode()
    req = urllib.request.Request(
        OSV, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data.get("vulns", []) or []
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def severity_of(vuln: dict) -> str:
    """从 OSV 记录里提取最高严重级别。

    OSV 的 severity 字段是 CVSS 向量，不一定带等级字符串；database_specific
    里常有 GHSA 的 severity（CRITICAL/HIGH/MODERATE/LOW）。两级都看。
    """
    for sev in vuln.get("severity", []) or []:
        score = str(sev.get("score", ""))
        # CVSS:3.1/AV:N/... 不含分数；有的记录直接给 "9.8"
        try:
            num = float(score)
        except ValueError:
            num = None
        if num is not None:
            if num >= 9.0:
                return "CRITICAL"
            if num >= 7.0:
                return "HIGH"
            if num >= 4.0:
                return "MEDIUM"
            return "LOW"
    ds = vuln.get("database_specific", {}) or {}
    lvl = str(ds.get("severity", "")).upper()
    if lvl in ("CRITICAL", "HIGH", "MODERATE", "LOW"):
        return "MEDIUM" if lvl == "MODERATE" else lvl
    return "UNKNOWN"


def scan_python() -> tuple[list[dict], int]:
    """返回 (问题列表, 未扫描数)。"""
    pkgs = parse_requirements()
    print(f"  已装 Python 依赖：{len(pkgs)} 个")
    problems: list[dict] = []
    unscanned = 0
    for name, ver in pkgs:
        vulns = osv_query(name, ver)
        if vulns is None:
            unscanned += 1
            continue
        for v in vulns:
            problems.append({
                "pkg": f"{name}=={ver}",
                "id": v.get("id", "?"),
                "severity": severity_of(v),
                "summary": (v.get("summary") or "")[:100],
            })
    return problems, unscanned


def scan_frontend() -> tuple[list[dict], int]:
    lock = ROOT / "agentd" / "web" / "package-lock.json"
    if not lock.exists():
        return [], 0
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], 1
    pkgs = data.get("packages", {}) or {}
    # npm lockfile v3：packages["node_modules/xxx"] = {version}
    items = [
        (path.split("node_modules/")[-1], meta.get("version"))
        for path, meta in pkgs.items()
        if path.startswith("node_modules/") and meta.get("version")
    ]
    print(f"  已锁前端依赖：{len(items)} 个")
    problems: list[dict] = []
    unscanned = 0
    for name, ver in items:
        vulns = osv_query(name, ver, ecosystem="npm")
        if vulns is None:
            unscanned += 1
            continue
        for v in vulns:
            problems.append({
                "pkg": f"{name}@{ver}",
                "id": v.get("id", "?"),
                "severity": severity_of(v),
                "summary": (v.get("summary") or "")[:100],
            })
    return problems, unscanned


def main() -> int:
    print("== P22 依赖漏洞扫描（OSV.dev） ==")
    if not osv_query("fastapi", "0.143.3"):
        # 连基本查询都返回 None 或空，先确认服务可达
        probe = osv_query("fastapi", "0.0.1-nonexistent")
        if probe is None:
            print("  ❌ OSV API 不可达，无法扫描（这不算通过）")
            return 1

    print("\n[Python]")
    py_problems, py_unscanned = scan_python()
    print("\n[Frontend]")
    fe_problems, fe_unscanned = scan_frontend()

    all_problems = py_problems + fe_problems
    critical = [p for p in all_problems if p["severity"] in ("CRITICAL", "HIGH")]
    medium = [p for p in all_problems if p["severity"] in ("MEDIUM", "LOW", "UNKNOWN")]

    check("无 Critical/High 漏洞", not critical,
          "; ".join(f"{p['pkg']} {p['id']}({p['severity']})" for p in critical[:5]))
    if medium:
        print(f"  ⚠️  {len(medium)} 个中低危（不阻断）：")
        for p in medium[:5]:
            print(f"     {p['pkg']} {p['id']} {p['severity']} — {p['summary']}")

    unscanned = py_unscanned + fe_unscanned
    if unscanned:
        # 不能把"没查到"当"没问题"
        check(f"全部依赖已完成扫描（{unscanned} 个查询失败）", False,
              "网络不稳导致部分包未扫描，结论不完整")
    else:
        check("全部依赖已完成扫描", True)

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
