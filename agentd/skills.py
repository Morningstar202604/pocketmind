"""Skills 机制：SKILL.md 目录扫描 + 渐进披露（对齐 Anthropic Agent Skills 格式）。

目录约定（与生态一致，可直接拷贝别人的 skill 目录进来）：

    $AGENT_HOME/skills/<skill-name>/SKILL.md
    ---
    name: battery-care
    description: 当用户询问电池保养 / 充电习惯时使用
    ---
    （正文：完整步骤、脚本、注意事项…）

渐进披露：只有 name + description 进 system 前缀（占用极小、恒定不变以保
提示缓存）；正文由模型按需调用 ``read_skill`` 工具读取。这样挂 50 个 skill
也不会撑爆上下文——这是官方 Skills 设计与「把全文塞进 prompt」的本质区别。

前向/向后兼容：目录不存在 = 空注册表；frontmatter 缺失时退化为「首个 # 标题
当 name、首段当 description」，保证手写 md 也能用。纯 stdlib 解析，无新依赖。
"""
from __future__ import annotations

from pathlib import Path

MAX_BODY_CHARS = 12000   # read_skill 单次返回上限
MAX_DESC_CHARS = 200     # 注入 system 的 description 截断


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter（只认 `key: value` 单行，够用且零依赖）。

    返回 (meta, body)。无 frontmatter 时 meta 为空 dict、body 为原文。
    """
    meta: dict[str, str] = {}
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return meta, text
    lines = text.splitlines()
    end = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            end = i
            break
    if end is None:
        return meta, text
    for ln in lines[1:end]:
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        key = k.strip().lower()
        val = v.strip().strip('"').strip("'")
        if key and val:
            meta[key] = val
    return meta, "\n".join(lines[end + 1:]).strip()


class SkillRegistry:
    """扫描 skills 目录并提供 list/read/system_block 三个操作。"""

    def __init__(self, skills_dir: Path):
        self.dir = Path(skills_dir)
        self._cache: list[dict] = []
        self._cache_key: tuple | None = None

    # ---------- 扫描（按目录 mtime 缓存：keep system 前缀字节稳定以利提示缓存） ----------

    def _scan(self) -> list[dict]:
        try:
            key = (str(self.dir), self.dir.stat().st_mtime_ns if self.dir.exists() else 0)
        except OSError:
            key = (str(self.dir), 0)
        if self._cache_key == key:
            return self._cache
        items: list[dict] = []
        if self.dir.is_dir():
            for child in sorted(self.dir.iterdir()):
                if not child.is_dir():
                    continue
                md = child / "SKILL.md"
                if not md.is_file():
                    continue
                try:
                    raw = md.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                meta, body = _parse_frontmatter(raw)
                name = meta.get("name") or child.name
                desc = meta.get("description") or ""
                if not desc:
                    # 退化：首个非标题的非空行当描述
                    for ln in body.splitlines():
                        s = ln.strip()
                        if s and not s.startswith("#"):
                            desc = s
                            break
                items.append({
                    "name": name,
                    "description": desc[:MAX_DESC_CHARS],
                    "dir": str(child),
                    "file": str(md),
                })
        self._cache, self._cache_key = items, key
        return items

    # ---------- 对外 ----------

    def list_skills(self) -> list[dict]:
        return [{"name": i["name"], "description": i["description"]} for i in self._scan()]

    def read_skill(self, name: str) -> str | None:
        """按名字读取正文（去掉 frontmatter），截断到上限；未知技能返回 None。"""
        target = str(name or "").strip().lower()
        for item in self._scan():
            if item["name"].lower() == target:
                try:
                    raw = Path(item["file"]).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return None
                _, body = _parse_frontmatter(raw)
                return body[:MAX_BODY_CHARS]
        return None

    def system_block(self) -> str:
        """注入 system 的技能索引；无技能时返回空串。内容稳定（按名字排序）。"""
        items = self._scan()
        if not items:
            return ""
        lines = [f"- {i['name']}：{i['description']}" for i in items]
        return (
            "可用技能（只有当任务与描述匹配时才调用 read_skill 读取完整步骤，不要凭空猜测技能内容）：\n"
            + "\n".join(lines)
        )

    def register_tool(self) -> None:
        """注册 read_skill 工具（延迟到 tools 包就绪后调用，避免循环导入）。"""
        from .tools import Tool, register

        registry = self

        async def read_skill(name: str) -> dict:
            body = registry.read_skill(name)
            if body is None:
                names = [i["name"] for i in registry.list_skills()]
                return {"error": f"没有名为 {name} 的技能", "available": names}
            return {"name": name, "content": body}

        register(Tool(
            name="read_skill",
            description="读取某个技能的完整说明（先看 system 里的技能索引，按需读取）。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名（见技能索引）"}},
                "required": ["name"],
            },
            risk="safe",
            handler=read_skill,
            summary="读取技能说明",
            group="skills",
        ))
