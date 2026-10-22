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
import logging
import os
import re
import time
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

logger = logging.getLogger("agentd.memory")


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
        # FTS 回填批量（由 settings.memory.fts_page_size 注入；影响启动速度）
        self.fts_page_size = 50

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
        # busy_timeout：多入口/多进程（用户误起两个实例、崩溃残留连接）争用同一库时，
        # aiosqlite 默认只等 5 秒就抛 "database is locked"，而全仓没有 OperationalError
        # 捕获 → 直接 500。放宽到 30 秒，让正常写入排队完成而不是失败。
        await self._conn.execute(text("PRAGMA busy_timeout=30000"))
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
        # 归档水位：记录该会话「已归档到哪条消息 id」。没有它就无从判断哪些消息
        # 已进过摘要，于是每轮都把同一批最旧消息重新摘要一遍（白烧 token，
        # 而且多份近似重复的归档会顶掉真正有用的记忆）。
        await self._ensure_column(c, "sessions", "archived_upto", "INTEGER NOT NULL DEFAULT 0")
        # messages 按 session_id 高频点查/排序/删除；缺索引时每次 list/get/delete 全表扫描
        await c.execute(text("CREATE INDEX IF NOT EXISTS idx_msg_sid ON messages(session_id, id)"))
        # 任务清单（todo_write 按会话整表覆盖；position 保序）
        await c.execute(text(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at REAL NOT NULL
            )
            """
        ))
        await c.execute(text("CREATE INDEX IF NOT EXISTS idx_todo_sid ON todos(session_id, position)"))
        # 知识库（用户策展资料；只经 kb_* 工具显式检索，不自动注入上下文）
        await c.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        ))
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
            # 知识库全文索引：与 memories_fts 同一分词器（trigram，中文子串可命中）
            await self._conn.execute(text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    content,
                    title UNINDEXED,
                    tokenize='trigram'
                )
                """
            ))
            await self._conn.commit()
            self._fts = True
            await self._backfill_fts()
        except Exception:  # noqa: BLE001 —— FTS 不可用不影响主流程
            self._fts = False

    async def _ensure_column(self, conn: AsyncConnection, table: str, column: str, ddl: str) -> None:
        """幂等补列（老库升级用）：SQLite 没有 ADD COLUMN IF NOT EXISTS，先查表结构。"""
        try:
            rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            existing = {str(dict(r._mapping).get("name", "")) for r in rows}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                await conn.commit()
        # aqg: top-level boundary —— 补列失败不阻塞启动（旧库仍可用，仅归档水位退化为 0）
        except Exception as e:  # noqa: BLE001
            # 静默 pass 会把「老库缺列」藏成后续导出/归档的 500，必须留痕
            logger.warning("补列 %s.%s 失败，相关能力退化为默认值: %s", table, column, e)

    async def _backfill_fts(self) -> None:
        """把 memory 表里已存在、但尚未进 FTS 索引的摘要补进去（幂等 + 限速）。

        小批提交：避免大数据集一次性回填阻塞启动。
        """
        try:
            assert self._conn is not None
            # 批量大小来自 settings.memory.fts_page_size（小屏/慢存储可调小减少卡顿）
            try:
                BATCH = max(1, min(1000, int(self.fts_page_size)))
            except (TypeError, ValueError):
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
                   COALESCE(s.archived_upto, 0) AS archived_upto,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m
              ON m.session_id = s.id AND m.role IN ('user','assistant')
            GROUP BY s.id, s.title, s.created_at, s.updated_at, s.archived_upto
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
            # 失败必须回滚：这批 DELETE 共用一条连接，若中途抛错（锁冲突/IO 错误）
            # 而直接退出，前几条未提交的 DELETE 会悬在连接上，被**下一次任意 commit**
            # 连带提交 —— 用户看到删除失败，但消息/记忆已被悄悄清空（跨会话数据损坏）。
            try:
                await self._conn.execute(text("DELETE FROM messages WHERE session_id=:s"), {"s": sid})
                await self._conn.execute(text("DELETE FROM memory WHERE session_id=:s"), {"s": sid})
                await self._conn.execute(text("DELETE FROM todos WHERE session_id=:s"), {"s": sid})
                if self._fts:
                    await self._conn.execute(text("DELETE FROM memories_fts WHERE session_id=:s"), {"s": sid})
                cur = await self._conn.execute(text("DELETE FROM sessions WHERE id=:s"), {"s": sid})
                await self._conn.commit()
            # aqg: top-level boundary —— 事务边界：回滚后原样抛出，不吞异常、不降级。
            # 取消（CancelledError）时 await rollback 会立即再次传播取消，
            # 用 best-effort 包一层，至少把回滚的发起动作做完。
            except BaseException:
                # aqg: top-level boundary —— 回滚本身失败（连接已坏）只记录不压制原异常
                try:
                    await self._conn.rollback()
                except BaseException:
                    pass
                raise
        return cur.rowcount > 0

    async def delete_messages_from(self, sid: str, message_id: int) -> int:
        """删除某条消息及其之后的所有消息（用于「编辑并重新发送」与「从这条重新生成」）。

        为什么按 id 截断而不是删单条：对话历史必须保持 user → assistant → tool 的
        配对完整。删掉中间一条会留下孤立 tool 响应或没有提问的回答，下一轮组装上下文
        时模型看到的就是错乱的对话。截断到分叉点是唯一语义正确的做法——
        这也是 Claude / ChatGPT 编辑消息时的行为。
        """
        assert self._conn is not None
        async with self._write_lock:
            try:
                cur = await self._conn.execute(text(
                    "DELETE FROM messages WHERE session_id=:s AND id >= :i"
                ), {"s": sid, "i": message_id})
                await self._conn.commit()
            except BaseException:
                try:
                    await self._conn.rollback()
                except BaseException:
                    pass
                raise
            return cur.rowcount or 0

    async def export_bundle(self, max_sessions: int = 500, max_messages: int = 1000) -> dict:
        """一次性取回导出所需的全部数据（会话 / 消息 / 归档 / 清单）。

        为什么单独一个方法：原来的 /api/export 对每个会话要查 3 次
        （messages / archives / todos）。500 个会话 = 1500 次往返，
        实测 100 会话就要 0.5 秒，且随会话数线性增长。

        现在改成 4 条聚合查询（IN 子句按会话批量取），把 N+1 压成常数次。
        返回 {sid: {"messages": [...], "archives": [...], "todos": [...]}}。

        注意：这里不做分页。导出是"全量备份"语义，分页会静默丢数据；
        限制由 max_sessions / max_messages 控制并在调用方标注。
        """
        assert self._conn is not None
        sessions = await self.list_sessions(limit=max_sessions)
        if not sessions:
            return {"sessions": [], "by_session": {}}

        sids = [s["id"] for s in sessions]
        # SQLAlchemy 的绑定参数不能直接展开列表，用命名参数拼占位符
        placeholders = ",".join(f":s{i}" for i in range(len(sids)))
        params = {f"s{i}": sid for i, sid in enumerate(sids)}

        def _rows_to_map(rows, key: str) -> dict[str, list]:
            out: dict[str, list] = {sid: [] for sid in sids}
            for r in rows:
                d = dict(r._mapping)
                out.setdefault(d[key], []).append(d)
            return out

        msg_rows = (await self._conn.execute(text(
            f"SELECT session_id, role, content, meta, created_at FROM messages "
            f"WHERE session_id IN ({placeholders}) ORDER BY id"
        ), params)).fetchall()
        # 每个会话最多 max_messages 条：在 Python 侧截断最近 N 条（保持时间正序）
        by_msgs: dict[str, list] = {sid: [] for sid in sids}
        raw = _rows_to_map(msg_rows, "session_id")
        for sid, items in raw.items():
            tail = items[-max_messages:] if len(items) > max_messages else items
            for d in tail:
                try:
                    d["meta"] = json.loads(d.get("meta") or "{}")
                except Exception:  # noqa: BLE001
                    d["meta"] = {}
            by_msgs[sid] = tail

        # 归档存在 memory 表（kind='archive'），不是独立表
        arch_rows = (await self._conn.execute(text(
            f"SELECT session_id, content, created_at FROM memory "
            f"WHERE session_id IN ({placeholders}) AND kind='archive' ORDER BY id"
        ), params)).fetchall()
        by_arch = _rows_to_map(arch_rows, "session_id")

        todo_rows = (await self._conn.execute(text(
            f"SELECT session_id, content, status FROM todos "
            f"WHERE session_id IN ({placeholders}) ORDER BY position"
        ), params)).fetchall()
        by_todo = _rows_to_map(todo_rows, "session_id")

        # 每会话最新一条摘要（kind='summary'）：用窗口函数一次取回，
        # 避免"每个会话再查一次"（原来就是这样，500 会话 = 500 次额外查询）
        sum_rows = (await self._conn.execute(text(
            f"SELECT session_id, content FROM memory WHERE session_id IN ({placeholders}) "
            f"AND kind='summary' AND id IN ("
            f"  SELECT MAX(id) FROM memory WHERE session_id IN ({placeholders}) "
            f"  AND kind='summary' GROUP BY session_id"
            f")"
        ), params)).fetchall()
        by_summary = {d["session_id"]: d["content"] for d in (dict(r._mapping) for r in sum_rows)}

        by_session = {
            sid: {
                "messages": by_msgs.get(sid, []),
                "archives": by_arch.get(sid, []),
                "todos": by_todo.get(sid, []),
                "summary": by_summary.get(sid),
            }
            for sid in sids
        }
        return {"sessions": sessions, "by_session": by_session}

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
        # 注意括号：`await conn.execute(...).scalar()` 会先对 coroutine 取属性，
        # 永远抛 AttributeError（摘要因此从未生效），必须先 await execute 再取 scalar。
        count_row = await self._conn.execute(
            text("SELECT COUNT(*) FROM messages WHERE session_id=:s AND role IN ('user','assistant')"),
            {"s": sid},
        )
        msg_count = count_row.scalar() or 0
        if msg_count > every_n * 2:
            # 摘要对象是「早期未归档的历史」，但 get_messages 的语义已反转为取**最近** N
            # 条（修失忆时改的），直接用它会把正在进行的对话摘要掉。
            # 这里显式按水位取最旧的 every_n 条：已归档的（id <= archived_upto）跳过，
            # 与 archive_messages 共享同一水位，不重复摘要同一段。
            old_rows = (await self._conn.execute(text(
                "SELECT role, content FROM messages WHERE session_id=:s "
                "AND role IN ('user','assistant') AND id > :w "
                "ORDER BY id ASC LIMIT :l"
            ), {"s": sid, "w": await self.get_archived_upto(sid), "l": every_n})).fetchall()
            old_msgs = [
                {"role": dict(r._mapping)["role"], "content": dict(r._mapping)["content"]}
                for r in old_rows
            ]
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
        """取该会话最近 limit 条消息（时间正序返回）。

        必须按 id **倒序**取再翻转：旧的 `ORDER BY id LIMIT :l` 取的是**最旧** N 条，
        会话超过 limit 后最新的消息根本进不了上下文 —— 模型表现为「突然失忆、答非所问」，
        而 `_trim` 只会在已截断的集合里再裁，无法补救。
        """
        assert self._conn is not None
        # 带上 id：归档水位要靠它判断「这条是否已归档过」
        rows = (await self._conn.execute(text(
            "SELECT id, role, content, meta, created_at FROM messages WHERE session_id=:s "
            "ORDER BY id DESC LIMIT :l"
        ), {"s": sid, "l": limit})).fetchall()
        rows = list(reversed(rows))
        out = []
        for r in rows:
            m = dict(r._mapping)
            try:
                m["meta"] = json.loads(m["meta"] or "{}")
            # aqg: top-level boundary —— meta 是 JSON 文本，坏值降级为 {}，不影响消息本体
            except Exception:  # noqa: BLE001
                m["meta"] = {}
            out.append(m)
        # 归位 tool 响应：中断恢复的占位消息落在会话末尾，需在组装时纠正顺序
        return self._reorder_tool_pairs(out)

    @staticmethod
    def _reorder_tool_pairs(items: list[dict]) -> list[dict]:
        """把 role="tool" 的响应紧贴到它对应的 assistant(tool_calls) 之后。

        为什么需要：中断恢复补的占位响应**插入在会话末尾**（写入时无从知道该插哪里），
        而 OpenAI 兼容 API 要求 tool 消息必须紧跟发起它的那条 assistant 消息 ——
        否则直接 400「tool_calls must be followed by tool messages」，会话从此卡死。
        这里在组装上下文时按 tool_call_id 归位，让任何写入顺序都得到合法历史。
        """
        by_id: dict[str, dict] = {}
        for m in items:
            if m.get("role") == "tool":
                cid = str((m.get("meta") or {}).get("tool_call_id") or "")
                if cid and cid not in by_id:
                    by_id[cid] = m
        if not by_id:
            return items
        placed: set[str] = set()
        out: list[dict] = []
        for m in items:
            role = m.get("role")
            if role == "assistant":
                out.append(m)
                for c in (m.get("meta") or {}).get("tool_calls") or []:
                    cid = str(c.get("id") or "")
                    if cid in by_id and cid not in placed:
                        out.append(by_id[cid])
                        placed.add(cid)
                continue
            if role == "tool":
                cid = str((m.get("meta") or {}).get("tool_call_id") or "")
                if cid in placed:
                    continue  # 已归位，跳过原位置
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

        水位控制：只归档「id 大于 archived_upto」的消息，并在成功后推进水位。
        否则每轮都把同一批最旧消息重新摘要（一次多烧一次 LLM 调用，
        且 5 条近似重复的归档会把真正有用的记忆挤出注入窗口）。
        """
        if not old_msgs:
            return None
        upto = await self.get_archived_upto(sid)
        pending = [m for m in old_msgs if int(m.get("id") or 0) > upto]
        if not pending:
            return None  # 这批已经归档过了，不重复调用 LLM
        summary = None
        if llm_client is not None:
            try:
                summary = await llm_client.summarize(
                    pending, max_chars=int(cfg.get("summary_max_chars", 400) or 400)
                )
            # aqg: top-level boundary —— LLM 摘要失败降级为「取最后一条用户消息」占位，
            # 不推进水位（下轮重试），绝不丢历史
            except Exception:  # noqa: BLE001
                summary = None
        if not summary:
            for m in reversed(pending):
                if m.get("role") == "user" and m.get("content"):
                    summary = "（早期对话摘要）" + str(m["content"])[:200]
                    break
        if summary:
            await self.append_archive(sid, summary)
            # 只有在摘要真正落库后才推进水位；失败则下轮重试，不丢历史
            await self.set_archived_upto(sid, max(int(m.get("id") or 0) for m in pending))
        return summary

    async def get_archived_upto(self, sid: str) -> int:
        """该会话已归档到的消息 id（0 = 尚未归档）。"""
        assert self._conn is not None
        try:
            row = (await self._conn.execute(
                text("SELECT archived_upto FROM sessions WHERE id=:s"), {"s": sid}
            )).fetchone()
        # aqg: top-level boundary —— 老库补列失败（缺列）时降级为 0，不能让调用方 500
        except Exception as e:  # noqa: BLE001
            logger.warning("读取归档水位失败（缺列?）: %s", e)
            return 0
        if not row:
            return 0
        try:
            return int(dict(row._mapping).get("archived_upto") or 0)
        except (TypeError, ValueError):
            return 0

    async def set_archived_upto(self, sid: str, upto: int) -> None:
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(
                text("UPDATE sessions SET archived_upto=:u WHERE id=:s AND archived_upto<:u"),
                {"u": int(upto), "s": sid},
            )
            await self._conn.commit()


    # ---------- 中断恢复：孤立 tool_calls 回填 ----------
    async def recover_orphan_tool_calls(self, sid: str) -> int:
        """为「有 tool_calls 但缺 tool 响应」的历史补占位响应，返回补的条数。

        成因：assistant(tool_calls) 消息在工具执行前就已落库，若进程在工具结果落库
        前被杀/被取消，历史里就留下没有响应的工具调用。OpenAI 兼容 API 对这种历史
        直接 400（tool_calls must be followed by tool messages），会话从此无法继续。
        这里按序扫描会话，对每个未被响应的 tool_call_id 补一条失败占位
        （meta.recovered=True），使历史自洽——模型看到「未执行完成」会自行决定重试。
        """
        assert self._conn is not None
        # 只扫最近 RECOVER_SCAN 条：孤儿工具调用只可能出现在**最近一轮**——
        # 进程在工具结果落库前被杀才会产生，更早的历史早已被本轮或上一轮修复过。
        # 原来无 LIMIT 会拉全会话（1000 条消息的会话每次 chat 都要全量扫描 + 解析 JSON）。
        rows = (await self._conn.execute(text(
            "SELECT id, role, meta FROM messages WHERE session_id=:s ORDER BY id DESC LIMIT :l"
        ), {"s": sid, "l": 200})).fetchall()
        rows = list(reversed(rows))
        pending: list[str] = []
        for r in rows:
            m = dict(r._mapping)
            try:
                meta = json.loads(m["meta"] or "{}")
            # aqg: top-level boundary —— 失败仅降级本能力
            except Exception:  # noqa: BLE001
                meta = {}
            if m["role"] == "assistant" and meta.get("tool_calls"):
                for c in meta["tool_calls"]:
                    cid = str(c.get("id") or "")
                    if cid:
                        pending.append(cid)
            elif m["role"] == "tool" and meta.get("tool_call_id"):
                cid = str(meta["tool_call_id"])
                if cid in pending:
                    pending.remove(cid)
        if not pending:
            return 0
        content = json.dumps(
            {"error": "该工具调用在服务中断时未执行完成，结果未知；如需请重新发起。"},
            ensure_ascii=False,
        )
        async with self._write_lock:
            # 批量 INSERT：这条路径在**每次 chat 之前**都会跑（见 agent.chat），
            # 原来是每个孤儿调用一次往返。孤儿通常 1-2 个，但批量后是常数。
            now = _now()
            await self._conn.execute(
                text(
                    "INSERT INTO messages(session_id, role, content, meta, created_at) "
                    "VALUES (:s,'tool',:c,:m,:t)"
                ),
                [
                    {
                        "s": sid, "c": content, "t": now,
                        "m": json.dumps({"tool_call_id": cid, "name": "", "status": "failed",
                                         "recovered": True}, ensure_ascii=False),
                    }
                    for cid in pending
                ],
            )
            await self._conn.commit()
        return len(pending)

    # ---------- 任务清单（todo_write） ----------
    async def set_todos(self, sid: str, todos: list[dict]) -> list[dict]:
        """整表覆盖某会话的任务清单（先删后插，position 保序）。"""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text("DELETE FROM todos WHERE session_id=:s"), {"s": sid})
            # 批量 INSERT：原来每条一次往返，todo 上限 50 条 = 50 次。
            # execute_many 一次提交完成（SQLAlchemy 会走 executemany）。
            if todos:
                now = _now()
                await self._conn.execute(
                    text(
                        "INSERT INTO todos(session_id, position, content, status, updated_at) "
                        "VALUES (:s,:p,:c,:st,:u)"
                    ),
                    [
                        {"s": sid, "p": i, "c": str(t.get("content", ""))[:200],
                         "st": str(t.get("status", "pending")), "u": now}
                        for i, t in enumerate(todos)
                    ],
                )
            await self._conn.commit()
        return todos

    async def get_todos(self, sid: str) -> list[dict]:
        assert self._conn is not None
        rows = (await self._conn.execute(text(
            "SELECT content, status FROM todos WHERE session_id=:s ORDER BY position"
        ), {"s": sid})).fetchall()
        return [dict(r._mapping) for r in rows]

    async def clear_todos(self, sid: str) -> None:
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(text("DELETE FROM todos WHERE session_id=:s"), {"s": sid})
            await self._conn.commit()

    # ---------- 知识库（显式检索，不自动注入） ----------
    async def kb_add(self, title: str, content: str) -> int:
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(text(
                "INSERT INTO knowledge(title, content, created_at) VALUES (:t,:c,:n)"
            ), {"t": str(title or "")[:120], "c": str(content or ""), "n": _now()})
            if self._fts:
                await self._conn.execute(text(
                    "INSERT INTO knowledge_fts(rowid, content, title) VALUES (:r,:c,:t)"
                ), {"r": cur.lastrowid, "c": str(content or ""), "t": str(title or "")[:120]})
            await self._conn.commit()
        return int(cur.lastrowid or 0)

    async def kb_search(self, query: str, limit: int = 5) -> list[dict]:
        """关键词检索知识库：FTS5 MATCH 优先，未命中/不可用时回退 LIKE。"""
        words = self._keywords(query)
        if not words:
            return []
        rows: list = []
        if self._fts:
            try:
                assert self._conn is not None
                match = " OR ".join(self._fts_phrase(w) for w in words)
                rows = (await self._conn.execute(text(
                    "SELECT k.id, k.title, k.content, k.created_at FROM knowledge_fts f "
                    "JOIN knowledge k ON k.id = f.rowid WHERE knowledge_fts MATCH :m LIMIT :l"
                ), {"m": match, "l": max(1, int(limit))})).fetchall()
            # aqg: top-level boundary —— 失败仅降级本能力
            except Exception:  # noqa: BLE001 —— FTS 异常回退 LIKE
                rows = []
        if not rows:
            assert self._conn is not None
            conds, args = [], {}
            for i, w in enumerate(words):
                conds.append(f"(content LIKE :w{i} OR title LIKE :w{i})")
                args[f"w{i}"] = f"%{w}%"
            args["l"] = max(1, int(limit))
            rows = (await self._conn.execute(text(
                "SELECT id, title, content, created_at FROM knowledge WHERE "
                + " OR ".join(conds) + " ORDER BY id DESC LIMIT :l"
            ), args)).fetchall()
        return [dict(r._mapping) for r in rows]

    async def kb_list(self) -> list[dict]:
        assert self._conn is not None
        rows = (await self._conn.execute(text(
            "SELECT id, title, content, created_at FROM knowledge ORDER BY id DESC"
        ))).fetchall()
        return [dict(r._mapping) for r in rows]

    async def kb_delete(self, kid: int) -> bool:
        assert self._conn is not None
        async with self._write_lock:
            if self._fts:
                await self._conn.execute(text("DELETE FROM knowledge_fts WHERE rowid=:r"), {"r": int(kid)})
            cur = await self._conn.execute(text("DELETE FROM knowledge WHERE id=:r"), {"r": int(kid)})
            await self._conn.commit()
        return cur.rowcount > 0

    async def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """跨会话全文搜索消息内容（会话抽屉的「搜索会话」升级为搜正文）。

        为什么要它：原来前端只按**标题**过滤，而标题是自动取的前 14 个字，
        用户记得的是"当时聊到的某个结论"，标题里根本没有 —— 几十个会话之后
        等于搜不到。这里直接搜正文。

        实现：优先 FTS5（messages_fts 若可用），否则 LIKE 降级；
        两种路径都只返回摘要片段而非全文，避免把长消息整段塞给前端。
        """
        words = [w for w in query.strip().split() if w][:6]
        if not words:
            return []
        assert self._conn is not None
        # 用 SQLite 的 instr 截一个窗口作为摘要（比 Python 侧切片少搬数据）
        rows = (await self._conn.execute(text(
            "SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.title "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.role IN ('user','assistant') AND ("
            + " OR ".join(f"m.content LIKE :w{i}" for i in range(len(words)))
            + ") ORDER BY m.id DESC LIMIT :l"
        ), {**{f"w{i}": f"%{w}%" for i, w in enumerate(words)}, "l": max(1, min(limit, 50))})).fetchall()

        out = []
        for r in rows:
            d = dict(r._mapping)
            content = d.pop("content") or ""
            low = content.lower()
            pos = min((low.find(w.lower()) for w in words if low.find(w.lower()) >= 0), default=0)
            start = max(0, pos - 30)
            snippet = content[start:start + 120].replace("\n", " ")
            out.append({**d, "snippet": ("…" if start else "") + snippet + ("…" if start + 120 < len(content) else "")})
        return out

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
            # aqg: top-level boundary —— FTS 查询异常回退 LIKE，检索能力降级但不失败
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
