#!/usr/bin/env python3
"""P3 健壮性回归（纯单元，无需起服务）。

覆盖本次专项检测发现并修复的问题：
  BUG-1 非法 cron 表达式：scheduler._make_trigger 抛 ValueError（路由层转 400）
  BUG-2 非法 permission_mode 不应让 pydantic 整体回退而丢弃其它字段 clamp
  BUG-3 date 触发字符串必须解析成 datetime，否则 DateTrigger 抛 Invalid date string
  边界：ApprovalCenter allow_always 持久化 / checkpoint undo 端到端 / 损坏 config 恢复
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  OK " if cond else "  XX ") + name + (f" — {detail}" if detail and not cond else ""))


def main():
    print("== BUG-1：非法 cron 抛 ValueError（路由层据此转 400）==")
    from agentd.scheduler import SchedulerService

    bad_crons = ["not a cron !!!", "* *", "99 99 99 99 99", "a b c d e"]
    raised = 0
    for expr in bad_crons:
        try:
            SchedulerService._make_trigger({"trigger_type": "cron", "expr": expr})
        except ValueError:
            raised += 1
    check("全部非法 cron 抛 ValueError", raised == len(bad_crons), f"{raised}/{len(bad_crons)}")
    # 合法 cron 不抛
    try:
        SchedulerService._make_trigger({"trigger_type": "cron", "expr": "0 9 * * *"})
        check("合法 cron 正常构造", True)
    except Exception as e:  # noqa: BLE001
        check("合法 cron 正常构造", False, str(e))

    print("== BUG-3：date 字符串解析为 datetime ==")
    from datetime import datetime

    t = SchedulerService._make_trigger({"trigger_type": "date", "expr": "2026-12-31 23:59"})
    check("date trigger 构造成功", isinstance(t.run_date, datetime), str(type(t.run_date)))
    check(
        "date 时间正确",
        t.run_date.strftime("%Y-%m-%d %H:%M") == "2026-12-31 23:59",
        str(t.run_date),
    )

    print("== BUG-2：非法 permission_mode 不连累其它字段 clamp ==")
    from agentd.config import Settings

    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.json"
        # permission_mode 非法 + approval_timeout=-5（应被 clamp 回 120）
        cfg.write_text(
            json.dumps({"permission_mode": "bogus", "server": {"approval_timeout": -5}}),
            encoding="utf-8",
        )
        s = Settings(cfg)
        check(
            "非法 permission_mode 兜底 approve",
            s.permission_mode() == "approve",
            s.permission_mode(),
        )
        check(
            "approval_timeout=-5 仍被 clamp 到 120",
            s.get()["server"]["approval_timeout"] == 120,
            str(s.get()["server"]["approval_timeout"]),
        )
        # 损坏 config
        cfg.write_text("{not json", encoding="utf-8")
        s2 = Settings(cfg)
        check(
            "损坏 config 回退默认且能启动", s2.permission_mode() == "approve", s2.permission_mode()
        )
        check("损坏 config 后 timeout 为默认 120", s2.get()["server"]["approval_timeout"] == 120)

    print("== ApprovalCenter：allow_always 持久化 ==")
    from agentd.main import ApprovalCenter

    async def ac_simple():
        ac = ApprovalCenter()
        task = asyncio.create_task(ac.ask("s1", "t1", "write_file"))
        await asyncio.sleep(0.05)
        ac.decide("s1", "t1", "allow_always")
        r1 = await task
        # 下次同工具同会话：已在白名单，ask 立即返回 allow（不挂 gate）
        r2 = await ac.ask("s1", "t2", "write_file")
        return r1, ac._always.get("s1", set()), r2

    r, always_set, r2 = asyncio.run(ac_simple())
    check("allow_always 返回 allow", r == "allow", r)
    check("allow_always 把工具记入会话白名单", "write_file" in always_set, str(always_set))
    check("再次 ask 直接 allow（不挂 gate）", r2 == "allow", r2)

    print("== checkpoint undo 端到端 ==")
    from agentd.checkpoints import CheckpointStore, backup_file, undo

    async def ckpt_test():
        with tempfile.TemporaryDirectory() as td:
            os.environ["AGENT_HOME"] = td
            f = Path(td) / "target.txt"
            f.write_text("v1", encoding="utf-8")
            bak = backup_file(f)
            cs = CheckpointStore(Path(td) / "checkpoints.db")
            await cs.start()
            await cs.record("sess", "call1", "write_file", str(f), bak, "overwrite")
            f.write_text("v2", encoding="utf-8")  # 覆盖
            res1 = await undo(cs, "sess", "call1")
            after = f.read_text(encoding="utf-8")
            res2 = await undo(cs, "sess", "call1")  # 二次 undo
            await cs.close()
            return res1, after, res2

    res1, after, res2 = asyncio.run(ckpt_test())
    check("undo 成功恢复文件", res1.get("ok") is True and after == "v1", f"{res1} after={after}")
    check("二次 undo 报已撤销", "error" in res2, str(res2))

    print()
    print(f"结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
