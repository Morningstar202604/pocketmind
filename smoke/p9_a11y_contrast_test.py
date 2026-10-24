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

# 白字压深色填充（气泡/主按钮/发送键）：这两组是渐变的两个端点，
# 白字在两端都要过 AA，否则渐变中间段必然有一段不可读。
FILL_PAIRS = {
    "light": ["brand-fill", "brand-fill-2"],
    "dark": ["brand-fill", "brand-fill-2"],
}

# 每套主题：文字变量 → 它可能落在哪些背景上
PAIRS = {
    # brand-text 是「作为正文颜色」的品牌色（置顶徽章、链接、状态文字都用它）。
    # 不能拿 --brand 直接写正文：#0d9d6e 在浅色 panel-2 上只有 3.06:1。
    "light": [("text", ["bg", "panel", "panel-2"]), ("text-2", ["bg", "panel", "panel-2"]),
              ("text-3", ["bg", "panel", "panel-2"]), ("warn", ["panel", "panel-2"]),
              ("err", ["panel", "panel-2"]), ("ok", ["panel", "panel-2"]),
              ("brand-text", ["bg", "panel", "panel-2"])],
    "dark": [("text", ["bg", "panel", "panel-2"]), ("text-2", ["bg", "panel", "panel-2"]),
             ("text-3", ["bg", "panel", "panel-2"]), ("warn", ["panel", "panel-2"]),
             ("err", ["panel", "panel-2"]), ("ok", ["panel", "panel-2"]),
             ("brand-text", ["bg", "panel", "panel-2"])],
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

    # 白字压填充色：--on-brand 是 #fff；气泡 / 主按钮 / 发送键都是「白字 + 品牌渐变」，
    # 渐变两端都必须过 AA，否则中间必然有一段读不清（旧值 #10b981 只有 2.54:1）。
    for theme in ("light", "dark"):
        vars_ = parse_theme(css, theme)
        for name in FILL_PAIRS[theme]:
            hexv = vars_.get(name)
            if not hexv:
                check(f"{theme} / 缺少 {name} 变量", False)
                continue
            r = ratio("#ffffff", hexv)
            check(f"{theme} / 白字 on {name} ≥ {MIN_RATIO}", r >= MIN_RATIO, f"{r:.2f}:1（{hexv}）")

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
