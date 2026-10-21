#!/usr/bin/env python3
"""依赖清单自检：requirements.txt 的版本约束必须真实可满足。

真实事故（2026-10-20 在干净的 Ubuntu 24.04 上装依赖时暴露）：
``opentelemetry-instrumentation-fastapi>=0.46`` 永远匹配不到任何版本。
instrumentation 系列走独立的 0.x beta 版本线，发布号形如 ``0.46b0`` / ``0.66b1``；
按 PEP 440，预发布版本只有在显式带后缀或说明符包含预发布时才参与匹配，且
``0.46b0 < 0.46``，所以裸 ``>=0.46`` 的下界高于所有已发布版本 —— 全新安装直接失败，
而已经装过旧版的环境完全无感（本机 Windows 就是从别处装好的 0.66b1）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQ = Path(__file__).resolve().parent.parent / "agentd" / "requirements.txt"

# 已知只发布 beta 版本线的包：下界必须带预发布后缀
PRERELEASE_ONLY = ("opentelemetry-instrumentation",)

# 必须锁主版本的包：这些一旦跨大版本就会改 API，而 requirements 里只写 >=
# 等于把"某天重装就挂"写进部署流程（本项目已因 mcp 1.x→2.x 做过一次兼容分支）。
MAJOR_CAPPED = {
    "pydantic",
    "pydantic-settings",
    "sqlalchemy",
    "fastapi",
    "starlette",
}

# 允许 extras 写法（如 sqlalchemy[asyncio]>=2.0）与环境标记
LINE_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*([<>=!~].*)?$")


def main() -> int:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except ModuleNotFoundError:
        print("跳过：未安装 packaging（pip 自带，正常环境都有）")
        return 0

    problems: list[str] = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        # 环境标记（; python_version<"3.11"）与行内注释都不影响版本约束判定
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = LINE_RE.match(line)
        if not m:
            problems.append(f"无法解析的行：{raw}")
            continue
        name, spec = m.group(1), (m.group(3) or "").strip()
        if not spec:
            continue
        try:
            specs = SpecifierSet(spec)
        except Exception as e:  # noqa: BLE001 —— 任何非法说明符都要报出来
            problems.append(f"{name}{spec}：说明符非法（{e}）")
            continue
        if name in MAJOR_CAPPED:
            has_upper = any(sp.operator in ("<", "<=", "~=", "==") for sp in specs)
            if not has_upper:
                problems.append(
                    f"{name}{spec}：缺主版本上限（建议加 ,<N）；否则下一次大版本发布会"
                    f"在不改一行代码的情况下破坏已部署环境"
                )

        if not name.startswith(PRERELEASE_ONLY):
            continue
        for s in specs:
            if s.operator not in (">=", ">", "=="):
                continue
            try:
                v = Version(s.version)
            except Exception:  # noqa: BLE001
                continue
            if not v.is_prerelease:
                problems.append(
                    f"{name}{spec}：下界 {s.version} 是正式号，但该包只发布 0.x beta"
                    f"（如 0.46b0）；PEP 440 下 0.46b0 < 0.46，该下界永远匹配不到任何版本"
                )

    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("  依赖约束可解析，无「永不匹配」的版本下界")
    return 0


if __name__ == "__main__":
    sys.exit(main())
