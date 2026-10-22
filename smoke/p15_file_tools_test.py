#!/usr/bin/env python3
"""P15 文件工具：4 个工具的正常路径 + 全部错误分支。

为什么单独测：files 是核心能力（agent 读写用户文件），但覆盖率只有 39%。
已有的 security_test 只测了 `_resolve` 的路径穿越，没测 list/read/write/delete
的实际行为、大小限制、备份与错误返回。

注意：本测试会临时改写 HOME，避免污染真实主目录。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def main() -> int:
    print("== P15 文件工具 ==")

    # 用临时目录当 HOME：_roots() 取 Path.home()，改 HOME 后需重新 import
    tmp_home = Path(tempfile.mkdtemp(prefix="p15-home-"))
    os.environ["HOME"] = str(tmp_home)
    os.environ["USERPROFILE"] = str(tmp_home)  # Windows 上 Path.home() 看这个
    for mod in list(sys.modules):
        if mod.startswith("agentd.tools"):
            del sys.modules[mod]

    # 确认 home 已切换（否则测试会写到真实主目录，不可接受）
    check("测试已隔离到临时 HOME", Path.home() == tmp_home, f"{Path.home()} vs {tmp_home}")

    import agentd.tools.files as F

    # ---------- list_dir ----------
    (tmp_home / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_home / "docs" / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_home / "docs" / "sub").mkdir(exist_ok=True)

    r = await F.list_dir("docs")
    check("list_dir 正常返回条目", len(r.get("entries", [])) == 2, str(r))
    names = {e["name"] for e in r.get("entries", [])}
    check("list_dir 列出文件与目录", names == {"a.txt", "sub"}, str(names))
    types = {e["name"]: e["type"] for e in r.get("entries", [])}
    check("list_dir 正确区分 file/dir", types.get("a.txt") == "file" and types.get("sub") == "dir", str(types))
    check("list_dir 给出文件大小", any(e.get("size") == 5 for e in r.get("entries", [])), str(r))

    r = await F.list_dir("no-such-dir")
    check("list_dir 不存在 → error", "error" in r and "不是目录" in r["error"], str(r))

    r = await F.list_dir("../../..")
    check("list_dir 越界 → error", "error" in r, str(r))

    r = await F.list_dir("docs/a.txt")
    check("list_dir 传文件路径 → 不是目录", "不是目录" in r.get("error", ""), str(r))

    # ---------- read_file ----------
    r = await F.read_file("docs/a.txt")
    check("read_file 返回内容", r.get("content") == "hello", str(r))
    check("read_file 返回路径", r.get("path", "").endswith("a.txt"))

    r = await F.read_file("docs/none.txt")
    check("read_file 不存在 → 不是文件", "不是文件" in r.get("error", ""), str(r))

    big = tmp_home / "docs" / "big.txt"
    big.write_text("x" * (F.MAX_TEXT + 10), encoding="utf-8")
    r = await F.read_file("docs/big.txt")
    check("read_file 超大文件被拒", "文件过大" in r.get("error", ""), r.get("error", "")[:60])

    r = await F.read_file("docs")
    check("read_file 传目录 → 不是文件", "不是文件" in r.get("error", ""), str(r))

    # 二进制内容：用 errors=replace 解码，不应崩
    binf = tmp_home / "docs" / "bin.dat"
    binf.write_bytes(b"\xff\xfe\x00\x01hello")
    r = await F.read_file("docs/bin.dat")
    check("read_file 二进制文件不崩溃", "content" in r or "error" in r, str(r)[:80])

    # ---------- write_file ----------
    r = await F.write_file("docs/new.txt", "内容")
    check("write_file 新建成功", r.get("ok") is True, str(r))
    check("write_file 落盘正确", (tmp_home / "docs" / "new.txt").read_text(encoding="utf-8") == "内容")
    check("write_file 返回字节数", r.get("bytes") == 2, str(r.get("bytes")))

    r = await F.write_file("docs/new.txt", "覆盖后")
    check("write_file 覆盖成功", r.get("ok") is True, str(r))
    check("write_file 覆盖时产生 _ckpt（可撤销）", "_ckpt" in r, str(r))

    r = await F.write_file("docs/deep/nested/x.txt", "a/b")
    check("write_file 自动建父目录", r.get("ok") is True and (tmp_home / "docs/deep/nested/x.txt").exists(), str(r))

    huge = "y" * 1_000_001
    r = await F.write_file("docs/huge.txt", huge)
    check("write_file 超 1MB 被拒", "内容过大" in r.get("error", ""), r.get("error", "")[:50])
    check("超限时未落盘", not (tmp_home / "docs/huge.txt").exists())

    r = await F.write_file("../escape.txt", "x")
    check("write_file 越界被拒", "error" in r, str(r))

    r = await F.write_file("docs/empty.txt", "")
    check("write_file 允许空内容", r.get("ok") is True, str(r))

    # ---------- delete_file ----------
    r = await F.delete_file("docs/new.txt")
    check("delete_file 成功", r.get("ok") is True, str(r))
    check("delete_file 真的删掉", not (tmp_home / "docs" / "new.txt").exists())
    check("delete_file 产生 _ckpt（可撤销）", "_ckpt" in r, str(r))

    r = await F.delete_file("docs/new.txt")
    check("delete_file 不存在 → 明确报错", "不存在" in r.get("error", ""), str(r))

    # 先清空再删目录：nested 里还有 x.txt，非空时 rmdir 必然失败（下一项验证）
    (tmp_home / "docs/deep/nested/x.txt").unlink()
    r = await F.delete_file("docs/deep/nested")
    check("delete_file 空目录成功", r.get("ok") is True, str(r))

    r = await F.delete_file("docs")
    check("delete_file 非空目录 → 报错而非级联删除",
          "error" in r, f"{r}")  # rmdir 对非空目录抛 OSError

    r = await F.delete_file("../outside.txt")
    check("delete_file 越界被拒", "error" in r, str(r))

    # ---------- 工具注册 ----------
    from agentd.tools import get_tool

    for name, expect_risk in [("list_dir", "safe"), ("read_file", "safe"),
                              ("write_file", "write"), ("delete_file", "danger")]:
        t = get_tool(name)
        check(f"{name} 已注册且 risk={expect_risk}", t is not None and t.risk == expect_risk,
              f"{getattr(t, 'risk', None)}")

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
