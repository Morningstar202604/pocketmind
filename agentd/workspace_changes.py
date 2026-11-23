"""工作区变更记录：人改的文件、删的文件、新建的文件，以及一键撤销。

为什么单独一份，而不直接复用 Agent 的 checkpoint：
- Agent 的 checkpoint 以 (session_id, tool_call_id) 为键，工作区页面没有这两个 id；
- 工作区的写操作是用户直接动作，需要一条「谁在什么时候改了哪个文件」的清单；
- 撤销要防「改完又改」：记录写入后的 mtime/size，目标不一致就拒绝，避免把用户的
  第二次修改一并覆盖掉。

存储是 `$AGENT_HOME/workspace_changes.json`（0600），最多保留 200 条。它不是审计
日志的替代品——真正的安全审计仍在 SQLite；这里只服务工作区的「变更 / 撤销」面板。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .paths import home_dir

_MAX_RECORDS = 200
_LOCK = threading.RLock()
_STORE: ChangeStore | None = None


class ChangeStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (home_dir() / "workspace_changes.json")

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-_MAX_RECORDS:], ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def record(
        self,
        *,
        path: str,
        kind: str,
        backup: str | None = None,
        after_mtime: float | None = None,
        after_size: int | None = None,
        bytes_changed: int = 0,
        source: str = "ui",
    ) -> dict:
        if kind not in {"overwrite", "create", "delete"}:
            raise ValueError(f"未知变更类型：{kind}")
        with _LOCK:
            items = self._read()
            item = {
                "id": uuid.uuid4().hex[:12],
                "path": path,
                "kind": kind,
                "backup": backup,
                "after_mtime": after_mtime,
                "after_size": after_size,
                "bytes": bytes_changed,
                "source": source,
                "created_at": time.time(),
                "undone": False,
            }
            items.append(item)
            self._write(items)
            return item

    def list(self, limit: int = 50, include_undone: bool = False) -> list[dict]:
        with _LOCK:
            items = self._read()
        if not include_undone:
            items = [x for x in items if not x.get("undone")]
        items.sort(key=lambda x: float(x.get("created_at") or 0), reverse=True)
        return items[: max(1, min(int(limit or 50), 200))]

    def get(self, change_id: str) -> dict | None:
        with _LOCK:
            for item in self._read():
                if item.get("id") == change_id:
                    return dict(item)
        return None

    def mark_undone(self, change_id: str) -> None:
        with _LOCK:
            items = self._read()
            for item in items:
                if item.get("id") == change_id:
                    item["undone"] = True
                    item["undone_at"] = time.time()
                    break
            self._write(items)

    def undo(self, change_id: str) -> dict:
        item = self.get(change_id)
        if not item:
            return {"error": "变更记录不存在"}
        if item.get("undone"):
            return {"error": "该变更已撤销过"}
        target = Path(str(item.get("path") or ""))
        kind = str(item.get("kind") or "")
        backup = item.get("backup")
        try:
            if kind == "create":
                if not target.exists():
                    return {"error": "文件已不存在，无需撤销"}
                if not self._matches_after(target, item):
                    return {"error": "文件在新建后又被修改，已拒绝撤销；请先确认内容"}
                target.unlink()
            elif kind in {"overwrite", "delete"}:
                if not backup or not Path(str(backup)).exists():
                    return {"error": "备份文件已丢失，无法撤销"}
                if kind == "overwrite" and target.exists() and not self._matches_after(target, item):
                    return {"error": "文件在保存后又被修改，已拒绝撤销；请先确认内容"}
                if kind == "delete" and target.exists():
                    return {"error": "该路径已被重新创建，已拒绝撤销；请先确认内容"}
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(backup), target)
            else:
                return {"error": f"未知变更类型：{kind}"}
        except OSError as exc:
            return {"error": f"撤销失败：{exc}"}
        self.mark_undone(change_id)
        return {"ok": True, "restored": str(target), "kind": kind}

    def undo_all(self) -> dict:
        """撤销当前全部未撤销记录（变更集）；单条失败不中断，逐条返回。"""
        items = self.list(limit=_MAX_RECORDS, include_undone=False)
        results = []
        ok = failed = 0
        for item in items:
            result = self.undo(str(item.get("id") or ""))
            entry = {
                "id": item.get("id"),
                "path": item.get("path"),
                "ok": bool(result.get("ok")),
            }
            if not result.get("ok"):
                entry["error"] = result.get("error")
                failed += 1
            else:
                ok += 1
            results.append(entry)
        return {"ok": failed == 0, "restored": ok, "failed": failed, "results": results}

    @staticmethod
    def _matches_after(target: Path, item: dict) -> bool:
        """目标是否仍是记录写入后的那一版；不一致就不覆盖用户的后续修改。"""
        try:
            st = target.stat()
        except OSError:
            return False
        mtime = item.get("after_mtime")
        size = item.get("after_size")
        if mtime is not None and abs(st.st_mtime - float(mtime)) > 0.001:
            return False
        if size is not None and int(st.st_size) != int(size):
            return False
        return True


def get_store() -> ChangeStore:
    global _STORE
    if _STORE is None:
        _STORE = ChangeStore()
    return _STORE
