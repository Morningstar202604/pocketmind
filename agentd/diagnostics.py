"""环境自检：把「为什么它不工作」变成可执行的答案。

动机（真实痛点）：这个 Agent 要跑在极不确定的环境里——Termux 的 termux-api 没装、
存储空间满、数据目录只读、Python 版本太老、LLM 端点不可达。此前这些问题的表现
都是同一种：用户发一句话，界面停在「正在思考…」或弹一句泛泛的报错，用户不知道
下一步该做什么。

这里做的是**只读探测 + 可执行建议**：每项给出 status(ok/warn/fail)、事实描述、
以及一句用户能照做的修复动作。不做自动修改——自愈动作（删文件、改权限、装包）
必须由用户确认，静默改动比报错更危险。
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from pathlib import Path

MIN_PY = (3, 11)


def _probe_python() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PY
    return {
        "id": "python",
        "title": "Python 版本",
        "status": "ok" if ok else "fail",
        "detail": f"{platform.python_version()}（{platform.machine()}）",
        "fix": "" if ok else f"需要 Python ≥ {MIN_PY[0]}.{MIN_PY[1]}；Termux 上执行 pkg upgrade python",
    }


def _probe_disk(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(str(path))
        free_mb = usage.free // (1024 * 1024)
        pct = int(usage.used / usage.total * 100) if usage.total else 0
        if free_mb < 50:
            status, fix = "fail", "剩余空间不足 50MB：清理旧会话（设置→导出后删除）或卸载不用的应用"
        elif free_mb < 200:
            status, fix = "warn", "空间偏紧，建议清理；低于 50MB 时服务将无法写入"
        else:
            status, fix = "ok", ""
        return {
            "id": "disk",
            "title": "存储空间",
            "status": status,
            "detail": f"剩余 {free_mb} MB（已用 {pct}%）",
            "fix": fix,
        }
    except Exception as e:  # noqa: BLE001
        return {"id": "disk", "title": "存储空间", "status": "fail",
                "detail": f"无法读取：{type(e).__name__}", "fix": "检查数据目录是否存在且可访问"}


def _probe_writable(path: Path) -> dict:
    probe = path / ".write_probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"id": "writable", "title": "数据目录可写", "status": "ok", "detail": str(path), "fix": ""}
    except Exception as e:  # noqa: BLE001
        return {
            "id": "writable",
            "title": "数据目录可写",
            "status": "fail",
            "detail": f"{path} 不可写（{type(e).__name__}）",
            "fix": "在 Termux 执行 chmod 700 ~/.agent/termux-agent，或用 --home 指到可写目录",
        }


def _probe_termux() -> dict:
    """termux-api 是否可用：这决定了 25 个手机工具能不能用。"""
    if os.name != "posix" or "com.termux" not in os.environ.get("PREFIX", "") + os.environ.get("HOME", ""):
        # 非 Termux（PC / Docker）不算故障：手机工具本就用不了，属于预期降级
        has_bin = shutil.which("termux-battery-status") is not None
        return {
            "id": "termux",
            "title": "手机能力（termux-api）",
            "status": "ok" if has_bin else "warn",
            "detail": "检测到 termux-api 命令" if has_bin else "当前不是 Termux 环境，手机工具不可用（其余功能正常）",
            "fix": "" if has_bin else "如需手机能力，请在 Termux 中 pkg install termux-api 并安装 Termux:API 应用",
        }
    ok = shutil.which("termux-battery-status") is not None
    return {
        "id": "termux",
        "title": "手机能力（termux-api）",
        "status": "ok" if ok else "fail",
        "detail": "termux-api 可用" if ok else "Termux 环境但找不到 termux-api",
        "fix": "" if ok else "在 Termux 执行 pkg install termux-api，并安装 Termux:API 配套 App",
    }


def _probe_port(host: str, port: int) -> dict:
    """端口是否可绑定：被占用时启动会直接失败，提前告诉用户换端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return {"id": "port", "title": "监听端口", "status": "ok", "detail": f"{host}:{port} 可用", "fix": ""}
    except OSError as e:
        return {
            "id": "port",
            "title": "监听端口",
            "status": "fail",
            "detail": f"{host}:{port} 被占用（{e.strerror or e}）",
            "fix": f"换一个端口：python -m agentd.main --port {port + 1}",
        }
    finally:
        s.close()


def _probe_net(host: str = "www.bing.com", timeout: float = 3.0) -> dict:
    """外网可达性：联网搜索与云端 LLM 都依赖它。"""
    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return {"id": "net", "title": "外网连接", "status": "ok", "detail": f"{host}:443 可达", "fix": ""}
    except Exception as e:  # noqa: BLE001
        return {
            "id": "net",
            "title": "外网连接",
            "status": "warn",
            "detail": f"{host}:443 不可达（{type(e).__name__}）",
            "fix": "本地功能不受影响；联网搜索与云端模型需要外网，检查手机网络或代理设置",
        }


def _probe_llm(settings_mgr, mock: bool) -> dict:
    if mock:
        return {"id": "llm", "title": "模型配置", "status": "ok", "detail": "离线演示模式（mock）", "fix": ""}
    try:
        ready = bool(settings_mgr.llm_ready())
        missing = [] if ready else settings_mgr.llm_missing()
    except Exception as e:  # noqa: BLE001
        return {"id": "llm", "title": "模型配置", "status": "fail",
                "detail": f"读取配置失败：{type(e).__name__}", "fix": "检查 config.json 是否被改坏，或重置设置"}
    return {
        "id": "llm",
        "title": "模型配置",
        "status": "ok" if ready else "fail",
        "detail": "已配置" if ready else f"缺少{'、'.join(missing)}",
        "fix": "" if ready else "打开「设置 → 模型」填写 Base URL / 模型名 / API Key",
    }


def _probe_deps() -> dict:
    """可选依赖缺失不算错，但要让用户知道哪些能力被降级了。"""
    optional = {
        "mem0ai": "长期记忆（Mem0）",
        "sherpa_onnx": "离线语音",
        "telegram": "Telegram 远程控制",
    }
    missing = []
    for mod, label in optional.items():
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001 —— 任何导入失败都视为未安装
            missing.append(label)
    if not missing:
        return {"id": "deps", "title": "可选能力", "status": "ok", "detail": "全部可用", "fix": ""}
    return {
        "id": "deps",
        "title": "可选能力",
        "status": "warn",
        "detail": "未启用：" + "、".join(missing),
        "fix": "按需安装：pip install mem0ai / sherpa-onnx / python-telegram-bot；不装不影响主功能",
    }


def run_diagnostics(settings_mgr, mock: bool, host: str, port: int, data_dir: Path) -> dict:
    items = [
        _probe_python(),
        _probe_writable(data_dir),
        _probe_disk(data_dir),
        _probe_port(host, port),
        _probe_net(),
        _probe_termux(),
        _probe_llm(settings_mgr, mock),
        _probe_deps(),
    ]
    fails = [i for i in items if i["status"] == "fail"]
    warns = [i for i in items if i["status"] == "warn"]
    return {
        "ok": not fails,
        "summary": "一切正常" if not fails and not warns
        else (f"{len(fails)} 项需要处理" if fails else f"{len(warns)} 项提示"),
        "items": items,
    }
