#!/usr/bin/env python3
"""P20 审批规则矩阵：risk × mode 的 9 种组合逐条断言。

由来：变异测试发现 permissions.should_ask 里 `mode=="auto" → False` 这个分支
没有任何测试断言——把它改成 True（auto 模式也弹审批）所有测试仍然通过。
这属于"覆盖率看着够、断言其实是空的"。

审批规则是整个项目的安全底线，9 种组合必须逐条锁死。
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
    print("== P20 审批规则矩阵 ==")
    from agentd.permissions import DANGER, SAFE, VALID_MODES, WRITE, should_ask

    # 期望矩阵：True = 需要用户确认
    EXPECT = {
        # (risk, mode): should_ask
        (SAFE, "auto"): False,
        (WRITE, "auto"): False,
        (DANGER, "auto"): False,
        (SAFE, "approve"): False,   # 只读永不打扰
        (WRITE, "approve"): True,
        (DANGER, "approve"): True,
        (SAFE, "chat"): False,      # chat 模式走不到，但保持一致
        (WRITE, "chat"): False,
        (DANGER, "chat"): False,
    }
    for (risk, mode), expect in EXPECT.items():
        got = should_ask(risk, mode)
        check(f"{mode:8s} × {risk:6s} → {'需确认' if expect else '放行'}",
              got is expect, f"实际 {got}")

    # 变更检测：把 auto 模式改成会询问，上面的断言必须失败
    check("auto 模式下 danger 也不询问（不打扰调试）", should_ask(DANGER, "auto") is False)

    # approve 模式：safe 不应被拦（否则每读一次目录都弹卡片）
    check("approve 模式下 safe 不询问", should_ask(SAFE, "approve") is False)

    # 未知 risk 在 approve 下应放行（只有明确的 write/danger 才拦）
    check("approve 下未知 risk 不询问", should_ask("unknown-risk", "approve") is False)

    # 未知 mode 应放行（不阻塞；与 chat 一致）
    check("未知 mode 不询问", should_ask(WRITE, "bogus-mode") is False)

    # 常量契约
    check("VALID_MODES 含三种模式", set(VALID_MODES) == {"auto", "approve", "chat"}, str(VALID_MODES))
    check("SAFE/WRITE/DANGER 常量为小写", (SAFE, WRITE, DANGER) == ("safe", "write", "danger"))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
