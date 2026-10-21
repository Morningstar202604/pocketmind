#!/usr/bin/env python3
"""P11 Termux 安装脚本：已装判定必须幂等（不能每次重装）。

真实问题（Termux 模拟验证发现）：
原 install.sh 用 `command -v <包名>` 判断是否已装，但
  - `build-essential` 是元包，没有同名可执行文件 → 恒判缺失 → 每次重装；
  - `rust` 的可执行文件叫 `rustc` → 同样恒判缺失；
在手机上重装一次要几分钟并消耗流量，用户每次跑 install.sh 都在白等。

本测试不依赖真实 Termux：构造一个最小假环境（假 PREFIX + 假 pkg +
预置"已装"的可执行文件），断言"全部已装 → 一次 pkg 都不调用"。
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "termux" / "install.sh"

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def write_exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def build_env(root: Path) -> tuple[dict[str, str], Path]:
    """构造假 Termux 环境：PREFIX/bin 下放 pkg 桩 + 预装命令。"""
    prefix = root / "usr"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    home = root / "home"
    home.mkdir(exist_ok=True)
    log = root / "pkg.log"
    log.write_text("", encoding="utf-8")

    # pkg 桩：只记录调用，不真装
    write_exe(bindir / "pkg", '#!/bin/sh\necho "$*" >> "$PKG_LOG"\nexit 0\n')
    env = {
        **os.environ,
        "HOME": str(home),
        "PREFIX": str(prefix),
        "PKG_LOG": str(log),
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    return env, log


def _bash() -> str:
    """定位 bash。

    Windows 上 PATH 里同时有 Git Bash 与 System32 的 WSL 转发器；直接写 "bash"
    可能命中后者，报 "execvpe(/bin/bash) failed"（实测踩到）。
    """
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    for cand in (
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
    ):
        if Path(cand).exists():
            return cand
    return found or "bash"


BASH = _bash()


def run_install(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(INSTALL_SH)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def main() -> int:
    print("== P11 Termux 安装脚本幂等性 ==")
    if not INSTALL_SH.exists():
        check("install.sh 存在", False)
        return 1

    # --- 场景 1：所有依赖都已安装 → 不得调用任何 pkg install ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, log = build_env(root)
        bindir = Path(env["PREFIX"]) / "bin"
        for b in ("python", "git", "rustc", "ld", "ar", "cc", "gcc", "termux-notification"):
            write_exe(bindir / b)
        r = run_install(env)
        calls = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("全已装时不调用 pkg install", calls == [], str(calls))
        check("脚本正常退出", r.returncode == 0, r.stderr[-200:])

    # --- 场景 2：rust 已装但只有 rustc（没有 rust 可执行文件）→ 不应重装 ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, log = build_env(root)
        bindir = Path(env["PREFIX"]) / "bin"
        for b in ("python", "git", "rustc", "ld", "ar", "cc", "gcc", "termux-notification"):
            write_exe(bindir / b)
        run_install(env)
        calls = log.read_text(encoding="utf-8")
        check("rust 用 rustc 判定（不因缺 rust 可执行文件而重装）", "rust" not in calls, calls)

    # --- 场景 3：build-essential 已装但无同名命令 → 不应重装 ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, log = build_env(root)
        bindir = Path(env["PREFIX"]) / "bin"
        for b in ("python", "git", "rustc", "ld", "ar", "cc", "gcc", "termux-notification"):
            write_exe(bindir / b)
        run_install(env)
        calls = log.read_text(encoding="utf-8")
        check("build-essential 用 cc 判定（不因缺同名命令而重装）",
              "build-essential" not in calls, calls)

    # --- 场景 4：真缺 rust → 必须尝试安装 ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, log = build_env(root)
        bindir = Path(env["PREFIX"]) / "bin"
        for b in ("python", "git", "ld", "ar", "cc", "gcc", "termux-notification"):
            write_exe(bindir / b)
        run_install(env)
        calls = log.read_text(encoding="utf-8")
        check("真缺 rust 时会尝试安装", "rust" in calls, calls)

    # --- 场景 5：termux-api 缺失 → 提示但不阻断（非致命） ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, log = build_env(root)
        bindir = Path(env["PREFIX"]) / "bin"
        for b in ("python", "git", "rustc", "ld", "ar", "cc", "gcc"):
            write_exe(bindir / b)
        r = run_install(env)
        check("缺 termux-api 不导致脚本失败", r.returncode == 0, r.stderr[-200:])
        check("缺 termux-api 时有提示信息",
              "termux-api" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])

    # --- 场景 6：boot.sh 找不到 python 时必须非零退出并说明原因 ---
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bindir = root / "bin"
        bindir.mkdir()
        # curl 桩：健康检查恒失败，确保走到失败分支
        write_exe(bindir / "curl", "#!/bin/sh\nexit 1\n")
        env = {
            **os.environ,
            "HOME": str(root),
            "PATH": f"{bindir}:/usr/bin:/bin",
        }
        boot = ROOT / "termux" / "boot.sh"
        r = subprocess.run([BASH, str(boot)], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        check("boot.sh 无 python 时非零退出", r.returncode != 0, f"exit={r.returncode}")
        check("boot.sh 无 python 时提示 pkg install python", "pkg install python" in out, out[-200:])

    # --- 场景 7：tzlocal 取不到时区时调度器不得崩溃（Termux 实测缺陷） ---
    # Android/Termux 没有系统 tzdata，APScheduler 构造期调用 tzlocal.get_localzone()
    # 抛 ZoneInfoNotFoundError('No time zone found with key GMT')，服务直接起不来。
    #
    # 测试要点：必须 (a) 真正构造 SchedulerService，(b) 在子进程里 monkeypatch
    # tzlocal 让它抛异常。只在 Windows 上跑是测不到的——本机有 tzdata，永远成功。
    # 第一版只调 _resolve_timezone()，把修复回退后测试仍然通过，等于没锁住。
    probe = """
import tzlocal, tzlocal.unix
def boom(*a, **k):
    raise Exception("No time zone found with key GMT")
tzlocal.unix._get_localzone = boom
tzlocal.get_localzone = boom
import tempfile, os
from agentd.scheduler import SchedulerService
d = tempfile.mkdtemp()
svc = SchedulerService(os.path.join(d, "jobs.db"))
print("SCHEDULER_OK", svc._sched.timezone)
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    check("tzlocal 失效时 SchedulerService 仍能构造（Android 实况）",
          r.returncode == 0 and "SCHEDULER_OK" in out, out[-400:])

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
