#!/usr/bin/env python3
"""P9 无障碍：主题配色的文字对比度必须达到 WCAG AA（4.5:1）。

为什么用脚本锁：颜色是"改一行就静默退化"的典型——把 --text-3 从 #666d79
改回 #6b7280 肉眼看不出区别，但在浅色 panel-2 上会从 4.60 掉到 4.40，
截图巡检时才发现按钮文字发灰看不清。这类约束必须机器守。

只解析 styles.css 的变量表，不依赖浏览器。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "agentd" / "web" / "src" / "styles.css"

# 每套主题：文字变量 → 它可能落在哪些背景上
PAIRS = {
    "light": [("text", ["bg", "panel", "panel-2"]), ("text-2", ["bg", "panel", "panel-2"]),
              ("text-3", ["bg", "panel", "panel-2"]), ("warn", ["panel", "panel-2"]),
              ("err", ["panel", "panel-2"]), ("ok", ["panel", "panel-2"])],
    "dark": [("text", ["bg", "panel", "panel-2"]), ("text-2", ["bg", "panel", "panel-2"]),
             ("text-3", ["bg", "panel", "panel-2"]), ("warn", ["panel", "panel-2"]),
             ("err", ["panel", "panel-2"]), ("ok", ["panel", "panel-2"])],
}
MIN_RATIO = 4.5
passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def parse_theme(css: str, theme: str) -> dict[str, str]:
    """提取某个主题块里的 --变量: #hex。浅色块是 html[data-theme=light]"""
    if theme == "light":
        m = re.search(r'html\[data-theme="?light"?\]\s*\{(.*?)\}', css, re.S)
    else:
        m = re.search(r'html\[data-theme="?dark"?\]\s*\{(.*?)\}', css, re.S)
    if not m:
        return {}
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


def luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    print("== P9 主题文字对比度（WCAG AA 4.5:1）==")
    css = CSS.read_text(encoding="utf-8")
    for theme, pairs in PAIRS.items():
        vars_ = parse_theme(css, theme)
        check(f"{theme} 主题变量可解析", bool(vars_), str(list(vars_)[:5]))
        for fg_key, bg_keys in pairs:
            fg = vars_.get(fg_key)
            if not fg:
                check(f"{theme}.{fg_key} 存在", False)
                continue
            worst = None
            for bk in bg_keys:
                bg = vars_.get(bk)
                if not bg:
                    continue
                r = ratio(fg, bg)
                if worst is None or r < worst[1]:
                    worst = (bk, r)
            if worst:
                check(
                    f"{theme} / {fg_key} on {worst[0]} ≥ {MIN_RATIO}",
                    worst[1] >= MIN_RATIO,
                    f"{worst[1]:.2f}:1（{fg} on {vars_.get(worst[0])}）",
                )
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
