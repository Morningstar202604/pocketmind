"""结构化长期记忆：用户画像 / 长期事实 / 事件日志（对标 Cherry Studio 的 agent 文件记忆）。

三层文件（都在 $AGENT_HOME/memory/ 下，权限 700/600，纯本机明文，用户可直接编辑）：

  USER.md       用户画像（覆盖写）：称呼、所在地、习惯、偏好
  FACT.md       长期事实（追加写）：一条一行，前缀 "- "
  JOURNAL.jsonl 追加式事件日志（每行一个 JSON：{ts, kind, text}），行数上限内自动截断

为什么用文件而不是数据库：这三类内容的价值在于「用户可见、可编辑、可迁移」，
黑盒向量抽取（mem0）做不到；会话摘要（memory 表）是自动生成的过程数据，
也不适合当用户档案。文件形态同时天然兼容「备份即拷贝目录」。

注入策略：system_block() 产出稳定字符串（内容不变则字节不变），排在固定 system
前缀之后，不破坏厂商提示缓存。写入后立即生效，无需重启。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_FILE_CHARS = 8000      # 单个文件（USER/FACT）保留上限，超出保留头部
MAX_JOURNAL_LINES = 2000   # 事件日志行数上限，超出丢弃最旧
MAX_BLOCK_CHARS = 6000     # 注入 system 的总上限


class ProfileMemory:
    """$AGENT_HOME/memory 下的画像/事实/日志三件套读写。"""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.user_md = self.dir / "USER.md"
        self.fact_md = self.dir / "FACT.md"
        self.journal = self.dir / "JOURNAL.jsonl"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    # ---------- 读 ----------

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)

    def load(self) -> dict:
        """返回 {user, facts}（原始文本，未截断前）。"""
        return {"user": self._read(self.user_md), "facts": self._read(self.fact_md)}

    def system_block(self) -> str:
        """产出注入 system 的稳定文本块；无内容时返回空串（调用方不追加消息）。"""
        user = self._read(self.user_md).strip()
        facts = self._read(self.fact_md).strip()
        parts = []
        if user:
            parts.append(f"用户画像：\n{user[:MAX_FILE_CHARS]}")
        if facts:
            parts.append(f"长期事实：\n{facts[:MAX_FILE_CHARS]}")
        if not parts:
            return ""
        return ("以下是你自己维护的长期记忆（由用户或你的记忆工具写入，可直接采信）：\n"
                + "\n".join(parts))[:MAX_BLOCK_CHARS]

    # ---------- 写 ----------

    def update_user(self, content: str) -> None:
        """覆盖写用户画像。"""
        self._write(self.user_md, str(content or "").strip()[:MAX_FILE_CHARS])
        self.append_journal("user_profile", str(content or "")[:200])

    def append_fact(self, fact: str) -> None:
        """追加一条长期事实（去重：完全相同的行不重复追加）。"""
        text = " ".join(str(fact or "").split())
        if not text:
            return
        line = f"- {text[:500]}"
        cur = self._read(self.fact_md)
        if line in cur.splitlines():
            return
        new = (cur.rstrip("\n") + "\n" + line + "\n") if cur.strip() else (line + "\n")
        # 超出上限时保留头部（早期事实通常更重要）
        if len(new) > MAX_FILE_CHARS:
            new = new[:MAX_FILE_CHARS] + "\n…（已达上限，后续事实请先整理 FACT.md）\n"
        self._write(self.fact_md, new)
        self.append_journal("fact", text[:200])

    def append_journal(self, kind: str, text: str) -> None:
        """追加一条事件日志（JSONL，只追加；超行数上限截断最旧）。"""
        try:
            rec = json.dumps({"ts": round(time.time(), 3), "kind": str(kind), "text": str(text)},
                             ensure_ascii=False)
            lines = self._read(self.journal).splitlines()
            lines.append(rec)
            if len(lines) > MAX_JOURNAL_LINES:
                lines = lines[-MAX_JOURNAL_LINES:]
            self._write(self.journal, "\n".join(lines) + "\n")
        # aqg: top-level boundary —— 日志失败不影响记忆主功能
        except Exception:  # noqa: BLE001
            pass

    # ---------- 工具注册 ----------

    def register_tools(self) -> None:
        """注册 remember_fact 工具（风险 write：写入会长期影响后续回答，值得用户把关）。"""
        from .tools import Tool, register

        pm = self

        async def remember_fact(fact: str) -> dict:
            text = " ".join(str(fact or "").split())
            if not text:
                return {"error": "fact 不能为空"}
            pm.append_fact(text)
            return {"ok": True, "fact": text[:200], "file": str(pm.fact_md)}

        register(Tool(
            name="remember_fact",
            description=(
                "把一条需要长期记住的事实写入用户档案（FACT.md）。"
                "适用于用户明确要求记住、或反复用到的稳定信息（地点、习惯、账号规则等）；"
                "不要记录一次性的临时内容。"
            ),
            parameters={
                "type": "object",
                "properties": {"fact": {"type": "string", "description": "一句话事实（祈使/陈述均可）"}},
                "required": ["fact"],
            },
            risk="write",
            handler=remember_fact,
            summary="记入长期事实",
            group="memory",
        ))
