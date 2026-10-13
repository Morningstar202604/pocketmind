"""会话与消息存储（P1-5：SQLAlchemy 2.0 异步引擎 + aiosqlite）。

三张表：sessions（会话）/ messages（消息）/ memory（长期记忆，M2 启用），
外加 memories_fts（FTS5 全文索引，P1-3）。

P1-5 迁移说明：
  - 由手写同步 sqlite3 改为 create_async_engine(sqlite+aiosqlite)，所有公共方法
    改为 async，避免阻塞 asyncio 事件循环（原实现直接在 loop 里跑同步 IO）。
  - 数据库文件路径、WAL 模式、目录 700 / 文件 600 权限全部保持不变。
  - 公共方法名与返回值形状（list[dict] / dict | None）保持不变，仅改为 await 调用。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


def _now() -> float:
    return time.time()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # 只创建引擎（不触发 IO）；真正的连接与建表在 start() 里异步完成
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self._conn: AsyncConnection | None = None
        self._fts = False  # FTS5 是否可用（编译进 sqlite 且建表成功才为 True）
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        """建立异步连接、开启 WAL、建表/回填（在 FastAPI startup 中调用）。"""
        self._conn = await self.engine.connect()
        await self._conn.execute(text("PRAGMA journal_mode=WAL"))
        # WAL 下 NORMAL 已足够安全（单进程 + aiosqlite 串行写），比 FULL 少一次 fsync；
        # wal_autocheckpoint 显式设回默认 1000 页，防止被外层配置覆盖后 WAL 无限增长；
        # temp_store=MEMORY 避免大排序落临时文件。
        await self._conn.execute(text("PRAGMA synchronous=NORMAL"))
        await self._conn.execute(text("PRAGMA wal_autocheckpoint=1000"))
        await self._conn.execute(text("PRAGMA temp_store=MEMORY"))
        await self._migrate()
        self._secure_files()

    def _secure_files(self) -> None:
        """数据含会话隐私（短信/定位等），与 config.json 同级别保护：
        目录 700，数据库及其 WAL/SHM 文件 600，其他用户不可读。"""
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        for f in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            try:
                if f.exists():
                    os.chmod(f, 0o600)
            except OSError:
                pass

    async def _migrate(self) -> None:
        c = self._conn
        assert c is not None
        await c.execute(text(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '新会话',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        ))
        await c.execute(text(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )
            """
        ))
        await c.execute(text(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'summary',
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        ))
        await c.execute(text("CREATE INDEX IF NOT EXISTS idx_mem_sid ON memory(session_id, kind)"))
        # messages 按 session_id 高频点查/排序/删除；缺索引时每次 list/get/delete 全表扫描
        await c.execute(text("CREATE INDEX IF NOT EXISTS idx_msg_sid ON messages(session_id, id)"))
        await c.commit()
        await self._init_fts()

    async def _init_fts(self) -> None:
        """建立 FTS5 虚拟表 memories_fts 并回填旧摘要。

        用 trigram 分词器以支持中文子串匹配（3 字以上）；若当前 sqlite 未编译
        FTS5/trigram 则静默降级，self._fts=False，搜索自动回退 LIKE。
        """
        try:
            assert self._conn is not None
            await self._conn.execute(text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content,
                    session_id UNINDEXED,
                    kind UNINDEXED,
                    created_at UNINDEXED,
                    tokenize='trigram'
                )
                """
            ))
            await self._conn.commit()
            self._fts = True
            await self._backfill_fts()
        except Exception:  # noqa: BLE001 —— FTS 不可用不影响主流程
            self._fts = False

    async def _backfill_fts(self) -> None:
        """把 memory 表里已存在、但尚未进 FTS 索引的摘要补进去（幂等 + 限速）。

        小批提交：避免大数据集一次性回填阻塞启动。
        """
        try:
            assert self._conn is not None
            BATCH = 50
            while True:
                result = await self._conn.execute(text(
                    f"""
                    INSERT INTO memories_fts(rowid, content, session_id, kind, created_at)
                    SELECT m.id, m.content, m.session_id, m.kind, m.created_at
                    FROM memory m
                    WHERE m.kind='summary'
                      AND NOT EXISTS (SELECT 1 FROM memories_fts f WHERE f.rowid = m.id)
                    LIMIT {BATCH}
                    """
                ))
                await self._conn.commit()
                if result.rowcount < BATCH:
                    break
        except Exception:  # noqa: BLE001
            self._fts = False

    # ---------- 会话 ----------
    async def create_session(self, title: str = "新会话") -> str:
        sid = uuid.uuid4().hex[:12]
        t = _now()
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text(
                "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (:id,:t,:c,:u)"
            ), {"id": sid, "t": title, "c": t, "u": t})
            await self._conn.commit()
        return sid

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        assert self._conn is not None
        # 单条 LEFT JOIN + GROUP BY 一次性带出每个会话的消息数，避免 N+1
        # （旧实现：1 条会话列表 + N 条 COUNT(*)，50 个会话要跑 51 条 SQL）。
        rows = (await self._conn.execute(text(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m
              ON m.session_id = s.id AND m.role IN ('user','assistant')
            GROUP BY s.id, s.title, s.created_at, s.updated_at
            ORDER BY s.updated_at DESC
            LIMIT :l
            """
        ), {"l": limit})).fetchall()
        return [dict(r._mapping) for r in rows]

    async def get_session(self, sid: str) -> dict | None:
        assert self._conn is not None
        r = (await self._conn.execute(text(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id=:s"
        ), {"s": sid})).fetchone()
        return dict(r._mapping) if r else None

    async def _touch_session_unlocked(self, sid: str, title: str | None = None) -> None:
        """touch_session 的无锁内部实现（供 add_message 在已持有锁时调用）。"""
        assert self._conn is not None
        if title is not None:
            await self._conn.execute(text(
                "UPDATE sessions SET title=:t, updated_at=:u WHERE id=:s"
            ), {"t": title, "u": _now(), "s": sid})
        else:
            await self._conn.execute(text(
                "UPDATE sessions SET updated_at=:u WHERE id=:s"
            ), {"u": _now(), "s": sid})
        await self._conn.commit()

    async def touch_session(self, sid: str, title: str | None = None) -> None:
        async with self._write_lock:
            await self._touch_session_unlocked(sid, title)

    async def delete_session(self, sid: str) -> bool:
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text("DELETE FROM messages WHERE session_id=:s"), {"s": sid})
            await self._conn.execute(text("DELETE FROM memory WHERE session_id=:s"), {"s": sid})
            if self._fts:
                await self._conn.execute(text("DELETE FROM memories_fts WHERE session_id=:s"), {"s": sid})
            cur = await self._conn.execute(text("DELETE FROM sessions WHERE id=:s"), {"s": sid})
            await self._conn.commit()
        return cur.rowcount > 0

    async def clear_all(self) -> None:
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text("DELETE FROM messages"))
            await self._conn.execute(text("DELETE FROM sessions"))
            await self._conn.execute(text("DELETE FROM memory"))
            if self._fts:
                await self._conn.execute(text("DELETE FROM memories_fts"))
            await self._conn.commit()

    async def cleanup_expired(self, retention_days: int = 0) -> int:
        """清理超过 retention_days 的旧 memory 与 messages。retention_days<=0 则跳过。"""
        if retention_days <= 0:
            return 0
        assert self._conn is not None
        cutoff = time.time() - retention_days * 86400
        async with self._write_lock:
            r1 = (await self._conn.execute(text("DELETE FROM memory WHERE created_at < :c"), {"c": cutoff})).rowcount
            r2 = (await self._conn.execute(text("DELETE FROM messages WHERE created_at < :c"), {"c": cutoff})).rowcount
            if self._fts:
                await self._conn.execute(text(
                    "DELETE FROM memories_fts WHERE rowid NOT IN (SELECT id FROM memory)"
                ))
            await self._conn.commit()
        return r1 + r2

    async def count_memory(self, session_id: str | None = None) -> int:
        """统计记忆条数（用于决定是否触发摘要分页）。"""
        assert self._conn is not None
        if session_id:
            r = (await self._conn.execute(
                text("SELECT COUNT(*) FROM memory WHERE session_id=:s"), {"s": session_id}
            )).scalar()
        else:
            r = (await self._conn.execute(text("SELECT COUNT(*) FROM memory"))).scalar()
        return int(r or 0)

    async def auto_summarize_if_needed(self, sid: str, cfg: dict, llm_client=None) -> str | None:
        """当会话消息数超过阈值时，自动把早期消息压缩为摘要写回 memory。

        是否需要摘要由 settings.memory.auto_summarize / summarize_every_n_turns 控制。
        返回生成的摘要文本，或 None（未触发）。
        """
        if not cfg.get("auto_summarize", True):
            return None
        every_n = max(3, cfg.get("summarize_every_n_turns", 10))
        if llm_client is None:
            return None
        # 取当前会话消息数（纯读，不加锁）
        assert self._conn is not None
        msg_count = (await self._conn.execute(
            text("SELECT COUNT(*) FROM messages WHERE session_id=:s AND role IN ('user','assistant')"),
            {"s": sid},
        ).scalar()) or 0
        if msg_count > every_n * 2:
            old_msgs = await self.get_messages(sid, limit=every_n)
            if not old_msgs:
                return None
            summary = await llm_client.summarize(
                old_msgs, max_chars=cfg.get("summary_max_chars", 400)
            )
            if summary:
                # set_summary 内部已加锁，此处无需重复
                await self.set_summary(sid, summary)
            return summary
        return None

    # ---------- 消息 ----------
    async def add_message(self, sid: str, role: str, content: str, meta: dict | None = None) -> dict:
        t = _now()
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(text(
                "INSERT INTO messages(session_id, role, content, meta, created_at) VALUES (:s,:r,:c,:m,:t)"
            ), {"s": sid, "r": role, "c": content, "m": json.dumps(meta or {}, ensure_ascii=False), "t": t})
            await self._conn.commit()
            await self._touch_session_unlocked(sid)
        return {"id": cur.lastrowid, "role": role, "content": content, "meta": meta or {}}

    async def get_messages(self, sid: str, limit: int = 200) -> list[dict]:
        assert self._conn is not None
        rows = (await self._conn.execute(text(
            "SELECT role, content, meta, created_at FROM messages WHERE session_id=:s ORDER BY id LIMIT :l"
        ), {"s": sid, "l": limit})).fetchall()
        out = []
        for r in rows:
            m = dict(r._mapping)
            try:
                m["meta"] = json.loads(m["meta"] or "{}")
            except Exception:
                m["meta"] = {}
            out.append(m)
        return out

    async def first_user_message(self, sid: str) -> str | None:
        assert self._conn is not None
        r = (await self._conn.execute(text(
            "SELECT content FROM messages WHERE session_id=:s AND role='user' ORDER BY id LIMIT 1"
        ), {"s": sid})).fetchone()
        return r._mapping["content"] if r else None

    # ---------- 记忆摘要 ----------
    async def set_summary(self, sid: str, summary: str) -> None:
        summary = (summary or "").strip()
        if not summary:
            return
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text(
                "DELETE FROM memory WHERE session_id=:s AND kind='summary'"
            ), {"s": sid})
            if self._fts:
                # 同步清掉该会话旧摘要的 FTS 索引（rowid 即将变化）
                await self._conn.execute(text(
                    "DELETE FROM memories_fts WHERE session_id=:s AND kind='summary'"
                ), {"s": sid})
            cur = await self._conn.execute(text(
                "INSERT INTO memory(session_id, kind, content, created_at) VALUES (:s,:k,:c,:t)"
            ), {"s": sid, "k": "summary", "c": summary, "t": _now()})
            if self._fts:
                # 写入记忆时同步写 FTS 索引（rowid 与 memory.id 对齐）
                await self._conn.execute(text(
                    "INSERT INTO memories_fts(rowid, content, session_id, kind, created_at) VALUES (:r,:c,:s,:k,:t)"
                ), {"r": cur.lastrowid, "c": summary, "s": sid, "k": "summary", "t": _now()})
            await self._conn.commit()

    async def get_summary(self, sid: str) -> str | None:
        assert self._conn is not None
        r = (await self._conn.execute(text(
            "SELECT content FROM memory WHERE session_id=:s AND kind='summary' ORDER BY id DESC LIMIT 1"
        ), {"s": sid})).fetchone()
        return r._mapping["content"] if r else None

    # ---------- 滚动摘要归档（P0 长程记忆分页） ----------
    async def append_archive(self, sid: str, content: str) -> None:
        """追加一段滚动摘要归档（kind='archive'），供虚拟上下文分页注入。

        与 set_summary（单份最近摘要）互补：archive 是多段历史归档，
        按时间追加，上下文组装时按旧→新注入。
        """
        content = (content or "").strip()
        if not content:
            return
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(text(
                "INSERT INTO memory(session_id, kind, content, created_at) VALUES (:s,:k,:c,:t)"
            ), {"s": sid, "k": "archive", "c": content, "t": _now()})
            if self._fts:
                await self._conn.execute(text(
                    "INSERT INTO memories_fts(rowid, content, session_id, kind, created_at) VALUES (:r,:c,:s,:k,:t)"
                ), {"r": cur.lastrowid, "c": content, "s": sid, "k": "archive", "t": _now()})
            await self._conn.commit()

    async def get_archives(self, sid: str, limit: int = 5) -> list[dict]:
        """获取该会话最近几段滚动摘要归档（按 id 倒序，即新的在前）。"""
        assert self._conn is not None
        rows = (await self._conn.execute(text(
            "SELECT content, created_at FROM memory WHERE session_id=:s AND kind='archive' ORDER BY id DESC LIMIT :l"
        ), {"s": sid, "l": limit})).fetchall()
        return [dict(r._mapping) for r in rows]

    async def archive_messages(self, sid: str, old_msgs: list[dict], cfg: dict, llm_client=None) -> str | None:
        """把最早的一批消息压缩为滚动摘要并归档。返回摘要文本或 None。

        old_msgs 是超过近期窗口的历史消息（按时间正序）。优先用 LLM 压缩；
        LLM 不可用时降级为「最后一条用户消息摘要占位」，保证分页后仍有记忆。
        """
        if not old_msgs:
            return None
        summary = None
        if llm_client is not None:
            try:
                summary = await llm_client.summarize(
                    old_msgs, max_chars=int(cfg.get("summary_max_chars", 400) or 400)
                )
            except Exception:  # noqa: BLE001
                summary = None
        if not summary:
            for m in reversed(old_msgs):
                if m.get("role") == "user" and m.get("content"):
                    summary = "（早期对话摘要）" + str(m["content"])[:200]
                    break
        if summary:
            await self.append_archive(sid, summary)
        return summary


    # ---------- 相关历史记忆（P1：跨会话回忆） ----------
    @staticmethod
    def _fts_phrase(word: str) -> str:
        """把关键词包成 FTS5 短语查询（双引号包裹、内部双引号转义），避免特殊字符破坏 MATCH 语法。"""
        return '"' + word.replace('"', '""') + '"'

    async def _like_search(self, words: list[str], limit: int, exclude_session: str | None) -> list:
        """旧版 LIKE 子串检索：作为 FTS 不可用 / 未命中时的降级路径。"""
        conds, args = [], {}
        for i, w in enumerate(words):
            conds.append(f"content LIKE :w{i}")
            args[f"w{i}"] = f"%{w}%"
        sql = (
            "SELECT session_id, content, created_at FROM memory "
            "WHERE kind='summary' AND (" + " OR ".join(conds) + ")"
        )
        if exclude_session:
            # 排除条件必须是独立 AND（不能并入上面的 OR 分组，否则会把其它会话
            # 的全部摘要都带出来——这是原实现的一个 latent bug，这里一并修正）
            sql += " AND session_id != :ex"
            args["ex"] = exclude_session
        sql += " ORDER BY created_at DESC LIMIT :l"
        args["l"] = limit
        assert self._conn is not None
        return (await self._conn.execute(text(sql), args)).fetchall()

    async def search_memories(self, query: str, limit: int = 3, exclude_session: str | None = None) -> list[dict]:
        """按关键词检索此前对话摘要。

        P1-3：优先用 FTS5 MATCH（trigram 支持中文子串）；FTS 不可用 / 语法异常 /
        未命中时自动回退到 LIKE 子串检索，保证召回不丢。
        """
        words = self._keywords(query)
        if not words:
            return []
        rows: list = []
        if self._fts:
            try:
                assert self._conn is not None
                match = " OR ".join(self._fts_phrase(w) for w in words)
                sql = (
                    "SELECT session_id, content, created_at FROM memories_fts "
                    "WHERE memories_fts MATCH :m AND kind='summary'"
                )
                args: dict = {"m": match}
                if exclude_session:
                    sql += " AND session_id != :ex"
                    args["ex"] = exclude_session
                sql += " ORDER BY created_at DESC LIMIT :l"
                args["l"] = limit
                rows = (await self._conn.execute(text(sql), args)).fetchall()
            except Exception:  # noqa: BLE001 —— FTS 查询异常则回退 LIKE
                rows = []
        if not rows:
            rows = await self._like_search(words, limit, exclude_session)
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def _keywords(text: str, max_words: int = 8) -> list[str]:

        words: list[str] = []
        # 英文/数字词
        words += [w.lower() for w in re.findall(r"[A-Za-z0-9]{2,}", text or "")]
        # 中文：按非中文标点切块，取 2 字以上片段
        cn = re.findall(r"[\u4e00-\u9fff]{2,}", text or "")
        words += [c for c in cn if c not in words]
        # 去重 + 限长
        seen, out = set(), []
        for w in words:
            if w not in seen and len(w) <= 20:
                seen.add(w)
                out.append(w)
            if len(out) >= max_words:
                break
        return out

    async def close(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
            await self.engine.dispose()
        except Exception:
            pass
