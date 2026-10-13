"""Shared path helpers for agentd."""

from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    """数据目录（配置 + 数据库）。"""
    h = os.environ.get("AGENT_HOME")
    return Path(h).expanduser() if h else Path.home() / ".agent" / "termux-agent"
