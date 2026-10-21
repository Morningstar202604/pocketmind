#!/usr/bin/env python3
"""P12 能力矩阵：设备能做什么、不能做什么、不能做时怎么办。

这条测试守的不是"功能"，而是**诚实**：如果某台设备其实给不了某项能力，
程序必须在用户使用之前就说清楚，而不是等用户发一句话、工具永远返回空。

三类断言：
1. 结构完整——每个不可用项都必须带 reason 与 workaround（不能只写"不支持"）。
2. 环境识别——Windows / 容器 / Termux 三种形态要认对，Termux 里还要区分模拟器。
3. 关键边界——非 Termux 上所有手机硬件能力必须标 unavailable；模拟器上即使
   装了 termux-api 也必须标 unavailable（没有真实 SIM/GPS/传感器）。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def fresh_module(env_patch: dict[str, str]):
    """在修改过的环境变量下重新加载 diagnostics，模拟不同设备形态。"""
    old = {k: os.environ.get(k) for k in env_patch}
    try:
        for k, v in env_patch.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import agentd.diagnostics as diag

        return importlib.reload(diag)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(importlib.import_module("agentd.diagnostics"))


def main() -> int:
    print("== P12 能力矩阵 ==")
    import agentd.diagnostics as diag

    m = diag.capability_matrix()

    # 1. 结构
    check("返回 platform/available/unavailable/degraded", all(
        k in m for k in ("platform", "available", "unavailable", "degraded")))
    check("platform 带 kind 与 abi", "kind" in m["platform"] and "abi" in m["platform"])
    check("summary 非空", bool(m.get("summary")))

    # 2. 每个不可用项都必须给出原因与替代方案
    missing_reason = [c["id"] for c in m["unavailable"] if not c.get("reason")]
    missing_fix = [c["id"] for c in m["unavailable"] if not c.get("workaround")]
    check("不可用项都有 reason", missing_reason == [], str(missing_reason))
    check("不可用项都有 workaround（不能只说'不支持'）", missing_fix == [], str(missing_fix))

    # 3. degraded 项同样要有解释
    bad_degraded = [c["id"] for c in m["degraded"] if not c.get("reason") and not c.get("workaround")]
    check("有条件可用项都有说明", bad_degraded == [], str(bad_degraded))

    # 4. 核心能力必须在所有平台都可用
    avail_ids = {c["id"] for c in m["available"]}
    for cid in ("chat", "files", "memory", "todo", "scheduler"):
        check(f"核心能力 {cid} 可用", cid in avail_ids)

    # 5. 非 Termux 环境：手机硬件能力必须全部 unavailable
    if m["platform"]["kind"] != "termux":
        phone_ids = {"sms", "phone", "location", "sensors", "battery", "clipboard", "notification", "camera"}
        unavail_ids = {c["id"] for c in m["unavailable"]}
        check("非 Termux 上手机能力全部标为不可用", phone_ids <= unavail_ids,
              str(sorted(phone_ids - unavail_ids)))
        sms = next((c for c in m["unavailable"] if c["id"] == "sms"), {})
        check("手机能力不可用时说明是环境原因", "Termux" in sms.get("reason", ""), sms.get("reason", ""))

    # 6. 模拟器判定：即使有 termux-api，硬件能力也必须不可用
    fake = diag.capability_matrix.__globals__
    orig_detect = fake["_detect_platform"]
    orig_which = fake["shutil"].which
    try:
        fake["_detect_platform"] = lambda: {
            "kind": "termux", "abi": "x86_64", "is_termux": True,
            "is_android": True, "is_emulator": True,
        }
        fake["shutil"].which = lambda name: "/fake/bin/" + name  # 假装 termux-api 都在
        m2 = diag.capability_matrix()
        sim_unavail = {c["id"] for c in m2["unavailable"]}
        check("模拟器上即使有 termux-api 也标硬件不可用",
              {"sms", "phone", "location", "sensors"} <= sim_unavail,
              str(sorted(sim_unavail)))
        sms2 = next((c for c in m2["unavailable"] if c["id"] == "sms"), {})
        check("模拟器不可用的原因写明是模拟器", "模拟器" in sms2.get("reason", ""), sms2.get("reason", ""))

        # 6b. 模拟器且**没有** termux-api：原因必须归到模拟器，
        #     不能报"未安装 termux-api"——那会让用户去装一个装了也没用的包。
        fake["shutil"].which = lambda name: None
        m2b = diag.capability_matrix()
        sms2b = next((c for c in m2b["unavailable"] if c["id"] == "sms"), {})
        check("模拟器未装 termux-api 时仍归因为模拟器",
              "模拟器" in sms2b.get("reason", ""), sms2b.get("reason", ""))

        fake["shutil"].which = lambda name: "/fake/bin/" + name
        # 7. 真机（非模拟器）上：termux-api 存在 → 手机能力应可用
        fake["_detect_platform"] = lambda: {
            "kind": "termux", "abi": "aarch64", "is_termux": True,
            "is_android": True, "is_emulator": False,
        }
        m3 = diag.capability_matrix()
        real_avail = {c["id"] for c in m3["available"]}
        check("真机 + termux-api 时手机能力可用",
              {"sms", "phone", "location", "battery"} <= real_avail,
              str(sorted(real_avail)))
    finally:
        fake["_detect_platform"] = orig_detect
        fake["shutil"].which = orig_which

    # 8. 诊断报告必须带上能力矩阵
    from agentd.config import Settings

    rep = diag.run_diagnostics(Settings(Path("/tmp/p12")), True, "127.0.0.1", 8799, Path("/tmp/p12/d"))
    check("诊断报告包含 capabilities", "capabilities" in rep)
    check("报告里的能力矩阵与直接调用一致",
          rep["capabilities"]["summary"] == diag.capability_matrix()["summary"])

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
