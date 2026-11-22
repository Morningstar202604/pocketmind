"""Shared path helpers for agentd."""

from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    """数据目录（配置 + 数据库）。"""
    h = os.environ.get("AGENT_HOME")
    return Path(h).expanduser() if h else Path.home() / ".agent" / "termux-agent"


def workspace_dir() -> Path:
    """开发工作区目录（代码/项目文件）。

    与 home_dir 分开：数据目录存放配置、数据库、凭据，绝不能作为代码工作区，
    否则 Agent 在编辑项目时可能误写 config.json / token。默认放在主目录下的
    pocketmind-workspace；可用 AGENT_WORKSPACE 指向已有项目目录。
    """
    w = os.environ.get("AGENT_WORKSPACE")
    return Path(w).expanduser() if w else Path.home() / "pocketmind-workspace"
