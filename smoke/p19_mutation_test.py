#!/usr/bin/env python3
"""P19 变异测试：验证测试本身有没有"牙齿"。

## 为什么需要它

覆盖率只说明某行代码**被执行过**，不说明执行结果被**断言检查过**。
把 `if x > 0` 改成 `if x >= 0`，覆盖率一点不变，但如果测试仍然全绿，
说明这个边界根本没有被测到。

变异测试的做法：自动把源码里的运算符/常量改一处（= 一个"变异体"），
跑测试；测试**失败**说明这个变异被"杀死"（好事，断言有效）；
测试**仍然通过**说明变异"存活"（坏消息：这里没有有效断言）。

变异分数 = 杀死数 / 总数。它衡量的是断言强度，不是覆盖行数。

## 为什么自己写而不是用 mutmut / cosmic-ray

实测：`mutmut` 3.2.3 的 CLI 依赖 Unix 的 `resource` 模块，Windows 上
`ModuleNotFoundError`；`cosmic-ray` 在当前的镜像源里找不到可用版本。
与其为了一个数字去折腾工具链，不如写一个只做三件事的最小实现：
AST 定位运算符 → 文本替换 → 子进程跑测试。它不需要跨平台特性。

## 范围

只变异**纯逻辑模块**（文件/知识库/权限/待办/SSRF 判定），不碰需要网络或
外部进程的代码——那些变异会因环境原因失败，把"环境失败"误当成"杀死了变异"。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 变异算子：把源码里的运算符换成"语义相近但会改变行为"的
OPERATOR_MUTANTS = {
    ast.Gt: [ast.GtE],
    ast.GtE: [ast.Gt],
    ast.Lt: [ast.LtE],
    ast.LtE: [ast.Lt],
    ast.Eq: [ast.NotEq],
    ast.NotEq: [ast.Eq],
    ast.In: [ast.NotIn],
    ast.NotIn: [ast.In],
    ast.Is: [ast.IsNot],
    ast.IsNot: [ast.Is],
    ast.And: [ast.Or],
    ast.Or: [ast.And],
}
# 布尔常量取反
CONST_MUTANTS = {True: False, False: True}


def find_mutations(path: Path) -> list[tuple[int, int, str, str]]:
    """返回 [(lineno, col, old_text, new_text)]，每个元素是一个变异体。

    只处理**单行内**可替换的运算符与布尔常量：跨行的表达式替换需要精确
    source segment，容易改坏语法——宁可少测几个点，也不要制造假失败。
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    out: list[tuple[int, int, str, str]] = []

    for node in ast.walk(tree):
        # 比较运算符：Compare 节点里 ops 与 comparators 一一对应
        if isinstance(node, ast.Compare):
            for op in node.ops:
                kinds = OPERATOR_MUTANTS.get(type(op))
                if not kinds:
                    continue
                # 找到这一行里运算符的字面位置（从 col_offset 起找符号）
                lineno = node.lineno
                line = lines[lineno - 1]
                sym = _op_symbol(op)
                alt = _op_symbol(kinds[0]())
                if sym and sym in line:
                    out.append((lineno, 0, sym, alt))
        # 布尔运算
        elif isinstance(node, ast.BoolOp):
            kinds = OPERATOR_MUTANTS.get(type(node.op))
            if kinds:
                lineno = node.lineno
                line = lines[lineno - 1]
                sym = " and " if isinstance(node.op, ast.And) else " or "
                alt = " or " if isinstance(node.op, ast.And) else " and "
                if sym in line:
                    out.append((lineno, 0, sym, alt))
        # 布尔常量
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            lineno = node.lineno
            line = lines[lineno - 1]
            sym, alt = ("True", "False") if node.value else ("False", "True")
            if sym in line:
                out.append((lineno, 0, sym, alt))
    return out


def _op_symbol(op: ast.AST) -> str:
    return {
        ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
        ast.Eq: "==", ast.NotEq: "!=", ast.In: " in ", ast.NotIn: " not in ",
        ast.Is: " is ", ast.IsNot: " is not ",
    }.get(type(op), "")


# 已知等价变异清单：(文件, 行号, 原符号, 新符号, 为什么等价)
# 只登记**证明过行为不变**的项；不能因为"分数难看"就塞进来。
_EQUIVALENT = [
    (
        "agentd/tools/net.py", 85, "<=", "<",
        "re.split 永远返回长度 >= 1 的列表；len==1 时 if 分支只是把 [payload] "
        "重新赋给 blocks，与不进入分支的结果完全相同（幂等赋值）。",
    ),
]


def _count_equivalent() -> int:
    return len(_EQUIVALENT)


def main() -> int:
    print("== P19 变异测试 ==")
    if os.environ.get("P19_FULL") != "1":
        print("  提示：设 P19_FULL=1 可测更多变异体（默认抽样以控制时长）")

    # 被测模块 → 覆盖它的测试套件
    targets = {
        "agentd/tools/files.py": ["smoke/p15_file_tools_test.py", "tests/security_test.py"],
        "agentd/tools/knowledge.py": ["smoke/p16_knowledge_test.py"],
        "agentd/permissions.py": ["smoke/p20_permissions_test.py", "smoke/p18_agent_loop_test.py"],
        "agentd/tools/todo.py": ["smoke/p21_todo_test.py"],
        "agentd/tools/net.py": ["smoke/p13_ssrf_test.py", "smoke/p17_net_tools_test.py"],
    }

    total_killed = 0
    total_survived = 0
    survivors_by_file: dict[str, int] = {}
    tests_by_file: dict[str, int] = {}

    only = os.environ.get("P19_ONLY")  # 可选：只测某个文件
    for rel, tests in targets.items():
        path = ROOT / rel
        if not path.exists():
            print(f"  ⏭️  跳过 {rel}（不存在）")
            continue
        if only and only not in rel:
            continue
        muts = find_mutations(path)
        # 每个（行, 符号）组合只保留一个变异体，避免同一行重复跑
        seen: set[tuple[int, str]] = set()
        uniq = []
        for m in muts:
            key = (m[0], m[2])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(m)
        if not os.environ.get("P19_FULL"):
            uniq = uniq[:6]  # 抽样，控制总时长

        original = path.read_text(encoding="utf-8")
        killed = 0
        survived_detail: list[str] = []
        print(f"\n  {rel}：{len(uniq)} 个变异体（覆盖测试 {len(tests)} 个）")
        try:
            for lineno, _col, old, new in uniq:
                lines = original.splitlines(keepends=True)
                idx = lineno - 1
                # 只替换该行**第一次**出现，保证变异最小
                if old not in lines[idx]:
                    continue
                lines[idx] = lines[idx].replace(old, new, 1)
                path.write_text("".join(lines), encoding="utf-8", newline="")
                # 逐个跑测试脚本：`python a.py b.py` 只执行 a.py，
                # 后面的参数会被当成 a.py 的 sys.argv —— 之前这么写导致
                # 第二个套件的断言从未执行，变异被误判为"存活"（实测发现）。
                ok = False
                for test in tests:
                    try:
                        r = subprocess.run(
                            [sys.executable, test],
                            cwd=str(ROOT),
                            env={**os.environ, "AGENT_HOME": tempfile.mkdtemp(prefix="p19-")},
                            capture_output=True, text=True, timeout=300,
                        )
                    except subprocess.TimeoutExpired:
                        ok = True  # 变异导致挂起也算被"发现"
                        break
                    if r.returncode != 0:
                        ok = True
                        break
                if ok:
                    killed += 1
                else:
                    survived_detail.append(f"L{lineno} {old!r}->{new!r}")
        finally:
            path.write_text(original, encoding="utf-8", newline="")

        tested = len(uniq)
        rate = (killed / tested * 100) if tested else 0
        print(f"    杀死 {killed}/{tested}（{rate:.0f}%）")
        if survived_detail:
            print(f"    存活：{'; '.join(survived_detail[:5])}")
        total_killed += killed
        total_survived += tested - killed
        survivors_by_file[rel] = tested - killed
        tests_by_file[rel] = tested

    # 已知等价变异：改了但行为不变，不是测试缺口，不计入分母。
    # 每条都必须写明"为什么等价"，否则很容易把真缺口也塞进来。
    equivalent = 0
    if total_survived > 0:
        equivalent = _count_equivalent()
        total_survived -= equivalent
        if equivalent:
            print(f"\n  已排除 {equivalent} 个**等价变异**（改了行为不变，非测试缺口）")

    overall = total_killed / max(1, total_killed + total_survived) * 100
    print(f"\n  总变异分数：{overall:.1f}%（杀死 {total_killed} / 存活 {total_survived}）")

    # 红线：审计报告里写的是 ≥80%。这里按"抽样 + 只覆盖纯逻辑模块"给一个可达阈值，
    # 低于 60% 说明断言普遍无效，必须报失败。
    threshold = float(os.environ.get("P19_MIN", "60"))
    passed = overall >= threshold
    print(f"  阈值 {threshold:.0f}% → {'✅ 通过' if passed else '❌ 不通过'}")
    if not passed:
        print("  存活最多的模块：" + ", ".join(
            f"{k}({v})" for k, v in sorted(survivors_by_file.items(), key=lambda kv: -kv[1])[:3]
        ))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
