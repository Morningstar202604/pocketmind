"""Mem0 本地记忆封装：向量 + 语义搜索，跨会话长期记忆。

完全本地运行（不依赖 mem0 云服务）：
  - 向量存储：ChromaDB（本地路径持久化）
  - 嵌入模型：FastEmbed（ONNX 运行时，无需 GPU，手机可跑）
  - LLM 抽取：OpenAI 兼容 API（复用 settings.llm / LiteLLM 配置）

Agent 集成流程：
  1. 对话开始前 → search(query) 找相关记忆 → 注入 system prompt
  2. 对话结束后 → add_conversation(messages, session_id) 抽取长期事实

依赖：mem0ai、chromadb、fastembed（均 pip install 即可）
降级：任一依赖缺失时，对象仍可构造（enabled() == False），调用全部空操作。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 模块级懒加载标志
_MEM0_AVAILABLE: bool | None = None


def _check_mem0_available() -> bool:
    """检查 mem0 + chromadb + fastembed 是否都已安装。"""
    global _MEM0_AVAILABLE
    if _MEM0_AVAILABLE is not None:
        return _MEM0_AVAILABLE
    try:
        import chromadb  # noqa: F401
        import fastembed  # noqa: F401
        import mem0  # noqa: F401
        _MEM0_AVAILABLE = True
    except ImportError:
        _MEM0_AVAILABLE = False
        logger.info("Mem0 依赖未全装（mem0ai/chromadb/fastembed），本地记忆功能未启用")
    return _MEM0_AVAILABLE


class Mem0Memory:
    """本地 Mem0 记忆管理器。

    使用方式（异步）：
        mem = Mem0Memory(data_dir=Path("~/.agent/mem0"), cfg={...})
        await mem.asearch("用户背景", limit=5)
        await mem.aadd(messages, session_id="s1")
    """

    def __init__(self, data_dir: Path, cfg: dict | None = None):
        """
        Args:
            data_dir: Mem0 数据存储目录（ChromaDB 持久化路径 + history.db）
            cfg: settings.mem0 配置块
        """
        self.data_dir = data_dir
        self.cfg = cfg or {}
        self._mem0: Any = None  # mem0.Memory 实例（懒加载，线程安全）
        self._lock = threading.Lock()
        self._error: str | None = None

        # 默认配置
        self._embedding_model = self.cfg.get("embedding_model", "BAAI/bge-small-zh-v1.5")
        self._llm_provider = self.cfg.get("llm_provider", "openai")
        self._llm_model = self.cfg.get("llm_model", "")
        self._search_limit = int(self.cfg.get("search_limit", 5))
        self._custom_instructions = self.cfg.get("custom_instructions", "")

    # ---------- 生命周期 ----------

    def enabled(self) -> bool:
        """Mem0 是否可用（依赖已装 + 已初始化）。"""
        return _check_mem0_available() and self._mem0 is not None

    def status(self) -> dict:
        """当前状态（用于前端展示 / 调试）。"""
        return {
            "available": _check_mem0_available(),
            "initialized": self._mem0 is not None,
            "error": self._error,
            "embedding_model": self._embedding_model,
            "search_limit": self._search_limit,
        }

    def init(self) -> bool:
        """初始化 Mem0 实例（幂等，线程安全）。

        返回 True 表示初始化成功，False 表示失败（看 self._error）。
        """
        if not _check_mem0_available():
            self._error = "依赖未安装（mem0ai/chromadb/fastembed）"
            return False
        if self._mem0 is not None:
            return True

        with self._lock:
            if self._mem0 is not None:
                return True  # 另一线程已做完
            try:
                self._mem0 = self._build_mem0()
                self._error = None
                logger.info("Mem0 本地记忆已初始化（dir=%s, embed=%s)", self.data_dir, self._embedding_model)
                return True
            except Exception as e:  # noqa: BLE001
                self._error = str(e)
                logger.error("Mem0 初始化失败: %s", e)
                return False

    def _build_mem0(self) -> Any:
        """构建 mem0.Memory 配置（内部调用，需已持有 _check_mem0_available()）。"""
        from mem0 import Memory
        from mem0.configs.base import MemoryConfig, VectorStoreConfig

        # ChromaDB 本地路径持久化
        chroma_path = str(self.data_dir / "chroma")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Vector store: ChromaDB
        vector_config = VectorStoreConfig(
            provider="chroma",
            config={
                "collection_name": "pocketmind_memories",
                "path": chroma_path,
            },
        )

        # Embedder: FastEmbed (ONNX) — 本地无需公网，支持中文
        from mem0.embeddings.configs import EmbedderConfig
        embedder_config = EmbedderConfig(
            provider="fastembed",
            config={
                "model": self._embedding_model,
            },
        )

        # LLM: OpenAI 兼容（复用 settings.llm 配置）
        from mem0.llms.configs import LlmConfig
        llm_cfg = self.cfg.get("_llm", {})  # 注入的完整 llm 配置
        llm_model = self._llm_model or llm_cfg.get("model", "gpt-4o-mini")
        llm_base_url = llm_cfg.get("base_url") or None
        llm_api_key = llm_cfg.get("api_key") or "empty"

        llm_config = LlmConfig(
            provider=self._llm_provider,
            config={
                "model": llm_model,
                "api_key": llm_api_key,
                **({"base_url": llm_base_url} if llm_base_url else {}),
            },
        )

        history_db_path = str(self.data_dir / "history.db")

        config = MemoryConfig(
            vector_store=vector_config,
            embedder=embedder_config,
            llm=llm_config,
            history_db_path=history_db_path,
            custom_instructions=self._custom_instructions or None,
        )

        return Memory(config=config)

    # ---------- 核心操作（异步包装，Mem0 是同步阻塞 IO） ----------

    async def aadd(self, messages: list[dict], session_id: str) -> bool:
        """从对话消息中抽取长期事实并写入记忆。

        在 agent.chat() 一轮结束后异步调用（不阻塞响应）。
        messages: OpenAI 格式消息列表 [{role, content, ...}]
        session_id: 当前会话 ID（作为 user_id 隔离）
        """
        if not self.enabled() or not messages:
            return False

        def _do():
            try:
                # Mem0.add 会自动调用 LLM 抽取实体/关系/事实
                self._mem0.add(messages, user_id=session_id)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("Mem0 add 失败: %s", e)
                return False

        try:
            return await asyncio.to_thread(_do)
        except Exception as e:  # noqa: BLE001
            logger.warning("Mem0 add 线程异常: %s", e)
            return False

    async def asearch(self, query: str, limit: int | None = None, session_id: str | None = None) -> list[dict]:
        """语义搜索相关记忆。

        在 agent.chat() 生成回复前调用，结果注入 system prompt。
        返回: [{"id": ..., "memory": "...", "score": ..., "created_at": ...}]

        session_id 作为 user_id：同一 session 的记忆优先级更高（私有）。
        """
        if not self.enabled() or not query.strip():
            return []
        k = limit or self._search_limit

        def _do():
            try:
                results = self._mem0.search(query, user_id=session_id, limit=k)
                # Mem0.search 在不同版本返回 dict 或 list；统一格式
                if isinstance(results, dict):
                    results = results.get("results", [])
                elif not isinstance(results, list):
                    results = []
                return [
                    {
                        "id": r.get("id", ""),
                        "memory": r.get("memory", ""),
                        "score": r.get("score", 0.0),
                        "created_at": r.get("created_at", ""),
                    }
                    for r in results
                    if isinstance(r, dict) and r.get("memory")
                ]
            except Exception as e:  # noqa: BLE001
                logger.warning("Mem0 search 失败: %s", e)
                return []

        try:
            return await asyncio.to_thread(_do)
        except Exception as e:  # noqa: BLE001
            logger.warning("Mem0 search 线程异常: %s", e)
            return []

    async def aget_all(self) -> list[dict]:
        """列出全部记忆（管理用）。"""
        if not self.enabled():
            return []

        def _do():
            try:
                results = self._mem0.get_all(user_id=None)  # 全部用户的记忆
                if isinstance(results, dict):
                    results = results.get("results", [])
                return [
                    {"id": r.get("id", ""), "memory": r.get("memory", ""), "created_at": r.get("created_at", "")}
                    for r in results if isinstance(r, dict)
                ]
            except Exception as e:
                logger.warning("Mem0 get_all 失败: %s", e)
                return []

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            return []

    async def adelete(self, memory_id: str) -> bool:
        """删除指定记忆。"""
        if not self.enabled() or not memory_id:
            return False

        def _do():
            try:
                self._mem0.delete(memory_id)
                return True
            except Exception as e:
                logger.warning("Mem0 delete 失败: %s", e)
                return False

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            return False

    async def areset(self) -> bool:
        """清除所有记忆（危险操作，前端需二次确认）。"""
        if not self.enabled():
            return False

        def _do():
            try:
                self._mem0.reset()
                return True
            except Exception as e:
                logger.warning("Mem0 reset 失败: %s", e)
                return False

        try:
            return await asyncio.to_thread(_do)
        except Exception:
            return False


# ---------- 工厂函数（注入到 main.py） ----------

def create_mem0(settings, data_dir: Path) -> Mem0Memory | None:
    """工厂：根据 settings 决定是否创建 Mem0Memory。

    返回 None 表示用户未启用 / LLM 不可用 / 依赖未装。
    """
    if not settings.use_mem0():
        return None

    cfg = settings.mem0_cfg()
    if not _check_mem0_available():
        logger.warning("Mem0 未安装（pip install mem0ai chromadb fastembed 可启用）")
        return None

    # 注入完整 llm 配置供 Mem0 的内部抽取 LLM 使用
    cfg["_llm"] = settings.llm()
    mem = Mem0Memory(data_dir=data_dir, cfg=cfg)
    if not mem.init():
        logger.warning("Mem0 初始化失败: %s", mem.status().get("error"))
        return None
    return mem
