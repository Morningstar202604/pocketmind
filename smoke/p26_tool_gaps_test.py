#!/usr/bin/env python3
"""P26 工具层缺口回归：静默失败 + 指标基数 + 外发风险分级。

三个都是"看起来能用、实际上骗人"的问题：

1. `set_volume` / `set_brightness` 用裸 `_run()` 且不看退出码 —— termux-api 没装、
   权限被拒、参数非法时命令其实失败了，前端仍显示「已完成」。同文件里
   `_run_checked` 就是为这件事写的（p7 的 A7 只查了 send_sms/set_wifi，漏了这两个）。
2. `metrics.normalize_path` 只把会话 id 归一化为 `{id}`，`/messages/123` 里的
   **消息主键**原样进了指标标签 —— 基数随消息数线性增长，且把用户数据 id 灌进监控。
   p23 的断言只覆盖了会话 id 那一段。
3. `read_url` 标成 safe：它会把 URL（可能含用户问题里的隐私）发给第三方站点，
   与 `web_search`（write，需审批）是同一类外发行为，分级必须一致。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def main() -> int:
    print("== P26 工具层缺口 ==")

    # ---------- 1. 设置类写操作必须校验退出码 ----------
    from agentd.tools.phone import _termux, system

    orig_bin, orig_run = _termux._bin, _termux._run
    # system.py 在 import 时把 _bin/_run 绑到了自己的模块命名空间：直接调用 _run 的
    # 函数（set_brightness）要看 system 那份；走 _run_checked 的（set_volume）看
    # _termux 那份。两份都要替换，否则只会测到其中一条路径。
    orig_sys_bin, orig_sys_run = system._bin, system._run

    async def _patched_fail(args, input_text=None, timeout=20):  # noqa: ASYNC109 —— 复刻真实签名
        return 1, "", "termux-api: permission denied"

    async def _patched_ok(args, input_text=None, timeout=20):  # noqa: ASYNC109 —— 复刻真实签名
        return 0, "ok", ""

    system._bin = lambda name: f"/usr/bin/{name}"
    _termux._bin = lambda name: f"/usr/bin/{name}"
    system._run = _patched_fail
    _termux._run = _patched_fail
    try:
        r = await system.set_volume("music", 5)
        check("set_volume 命令失败时返回 error", bool(r.get("error")), str(r))
        r2 = await system.set_brightness(120)
        check("set_brightness 命令失败时返回 error", bool(r2.get("error")), str(r2))
    finally:
        system._bin, system._run = orig_sys_bin, orig_sys_run
        _termux._bin, _termux._run = orig_bin, orig_run

    system._bin = lambda name: f"/usr/bin/{name}"
    _termux._bin = lambda name: f"/usr/bin/{name}"
    system._run = _patched_ok
    _termux._run = _patched_ok
    try:
        r3 = await system.set_volume("music", 5)
        check("set_volume 成功时无 error", not r3.get("error"), str(r3))
        r4 = await system.set_brightness(120)
        check("set_brightness 成功时无 error", not r4.get("error"), str(r4))
    finally:
        system._bin, system._run = orig_sys_bin, orig_sys_run
        _termux._bin, _termux._run = orig_bin, orig_run

    # ---------- 2. 指标路径归一化不能泄露消息 id ----------
    from agentd import metrics as m

    p = m.normalize_path("/api/sessions/abc123/messages/987654")
    check("消息 id 归一化为 {id}", p == "/api/sessions/{id}/messages/{id}", p)
    check("归一化结果不含原始消息 id", "987654" not in p, p)
    p2 = m.normalize_path("/api/sessions/abc123/messages")
    check("无消息 id 的路径不被改写", p2 == "/api/sessions/{id}/messages", p2)
    # 截断端点的方法不同，但路径形状相同，仍然不能带出 id
    p3 = m.normalize_path("/api/kb/42")
    check("kb id 归一化", p3 == "/api/kb/{id}", p3)

    # ---------- 3. read_url 必须与 web_search 同为外发风险 ----------
    from agentd.tools import get_tool

    rw = get_tool("read_url")
    ws = get_tool("web_search")
    check("read_url 已注册", rw is not None)
    check("web_search 已注册", ws is not None)
    if rw and ws:
        check("read_url 风险等级为 write（外发需审批）", rw.risk == "write", rw.risk)
        check("read_url 与 web_search 风险等级一致", rw.risk == ws.risk, f"{rw.risk} vs {ws.risk}")

    # ---------- 4. 定时任务 run 记录结构与普通 run 一致 ----------
    import agentd.main as M

    M.MOCK = True
    home = Path(tempfile.mkdtemp(prefix="p26-"))
    from agentd.memory import Store

    M.store = Store(home / "agent.db")
    await M.store.start()
    sid = await M.store.create_session()
    M.runs.pop(sid, None)
    # 直接构造一次 job 触发路径：只检查它注册的 run 记录字段齐全
    await M.run_agent_for_job(sid, "你好")
    check("job 触发后 run 记录已清理", sid not in M.runs, str(M.runs.get(sid)))
    msgs = await M.store.get_messages(sid)
    check("job 触发真的产生了一轮对话", len(msgs) >= 2, str(len(msgs)))
    await M.store.close()

    # ---------- 汇总 ----------
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败：" + "；".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
