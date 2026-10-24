#!/usr/bin/env bash
# 发布一个版本：门禁 → 打 tag → 推 5 个远程 → 在 5 个平台建 Release。
#
# 用法：
#   bash scripts/release.sh 1.2.0              # 从当前 HEAD 发布
#   bash scripts/release.sh 1.2.0 --dry-run    # 只打印计划，不发任何网络请求
#
# 前置：
#   - 门禁全绿（脚本默认先跑 `bash tests/run_all.sh --allow-dirty-dist`；SKIP_GATE=1 跳过）
#   - 凭据来自本机库：`eval "$(python ~/.zcode/secrets/accounts_cli.py env)"`
#     需要 GH_X33834_TOKEN / GH_MORNINGSTAR_TOKEN / GH_BADHOPE_TOKEN / GITEE_TOKEN / GITCODE_TOKEN
#
# 为什么不用 gh CLI 建 Release：gh 只能以「当前登录的那一个账号」身份调用 API，
# 而本项目要往三个不同 GitHub 账号的仓库发 Release（实测 gh 会对非当前账号返回 404）。
# 这里按仓库显式选 token，走 REST API。
set -uo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
if [ -z "$VERSION" ]; then
  echo "用法：bash scripts/release.sh <version> [--dry-run]" >&2
  exit 2
fi
if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "版本号必须是 X.Y.Z（当前：$VERSION）" >&2
  exit 2
fi
TAG="v$VERSION"

if [ "$DRY" = 0 ] && [ "${SKIP_GATE:-0}" != 1 ]; then
  echo "== 门禁 =="
  bash tests/run_all.sh --allow-dirty-dist || { echo "门禁未通过，终止发布" >&2; exit 1; }
fi

echo ""
echo "== 校验工作区 =="
if [ -n "$(git status --porcelain | grep -v '^?? ' || true)" ]; then
  echo "存在未提交改动，先提交再发布：" >&2
  git status --short >&2
  exit 1
fi

echo ""
echo "== 打 tag $TAG =="
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "  $TAG 已存在，跳过创建（要移动 tag 请手工 git tag -f 后重跑）"
else
  if [ "$DRY" = 1 ]; then
    echo "  [dry-run] git tag -a $TAG -m \"PocketMind $TAG\""
  else
    git tag -a "$TAG" -m "PocketMind $TAG" || exit 1
  fi
fi

echo ""
echo "== 推送 main + tags 到 5 个远程 =="
if [ "$DRY" = 1 ]; then
  bash scripts/sync-remotes.sh --tags --dry-run
else
  bash scripts/sync-remotes.sh --tags || echo "推送有失败项，继续尝试建 Release"
fi

if [ "$DRY" = 0 ]; then
  for v in GH_X33834_TOKEN GH_MORNINGSTAR_TOKEN GH_BADHOPE_TOKEN GITEE_TOKEN GITCODE_TOKEN; do
    if [ -z "$(eval "printf '%s' \"\${$v:-}\"")" ]; then
      echo "缺少环境变量 $v —— 先执行：eval \"\$(python ~/.zcode/secrets/accounts_cli.py env)\"" >&2
      exit 2
    fi
  done
fi

echo ""
echo "== 创建 Release =="
BODY_FILE="$(mktemp)"
{
  echo "## PocketMind $TAG — 口袋智灵"
  echo ""
  echo "**一台手机上的常驻 Agent：41 个工具、服务端强制审批、数据不出设备。**"
  echo ""
  echo "- 演示：[docs/demo/pocketmind-demo.mp4](docs/demo/pocketmind-demo.mp4) · 官网：https://badhope.github.io/pocketmind/"
  echo "- 安装：\`bash termux/install.sh && bash termux/start.sh\`"
  echo "- 门禁：\`bash tests/run_all.sh\`"
  echo ""
  echo "---"
  echo ""
  # 取 CHANGELOG 里本版本的段落（不含下一个版本标题）
  python - "$VERSION" > "$BODY_FILE.changelog" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path("CHANGELOG.md").read_text(encoding="utf-8")
start = text.find(f"## [{version}]")
if start < 0:
    sys.exit(0)
rest = text[start:]
nxt = re.search(r"\n## \[", rest[1:])
print(rest if not nxt else rest[: nxt.start() + 1])
PY
  cat "$BODY_FILE.changelog" 2>/dev/null || true
} > "$BODY_FILE"

if [ "$DRY" = 1 ]; then
  echo "  [dry-run] 将用 REST API 在以下平台创建 Release："
  echo "    GitHub  X33834/pocketmind / Morningstar202604/pocketmind / badhope/pocketmind"
  echo "    Gitee   badhope/pocketmind"
  echo "    GitCode badhope/pocketmind"
  echo "  [dry-run] Release 正文预览（前 12 行）："
  sed -n '1,12p' "$BODY_FILE" | sed 's/^/    /'
  rm -f "$BODY_FILE" "$BODY_FILE.changelog"
  exit 0
fi

python - "$VERSION" "$BODY_FILE" <<'PY'
"""按仓库显式选 token 创建 Release（GitHub ×3 + Gitee + GitCode）。"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

version = sys.argv[1]
notes = Path(sys.argv[2]).read_text(encoding="utf-8")
tag = f"v{version}"
title = f"PocketMind {tag} — 口袋智灵"


def http(method: str, url: str, token: str, payload: dict | None = None, auth: str = "bearer"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "pocketmind-release", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}" if auth == "bearer" else ""
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        body = resp.read().decode()
        return resp.status, json.loads(body) if body.strip().startswith(("{", "[")) else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


ok = True
for repo, env_name in (
    ("X33834/pocketmind", "GH_X33834_TOKEN"),
    ("Morningstar202604/pocketmind", "GH_MORNINGSTAR_TOKEN"),
    ("badhope/pocketmind", "GH_BADHOPE_TOKEN"),
):
    token = os.environ.get(env_name, "")
    status, body = http(
        "POST",
        f"https://api.github.com/repos/{repo}/releases",
        token,
        {
            "tag_name": tag,
            "name": title,
            "body": notes,
            "draft": False,
            "prerelease": False,
            "make_latest": "true",
        },
    )
    if status == 201:
        print(f"  GitHub {repo}: 已创建 {body.get('html_url', '')}")
    elif status == 422:
        print(f"  GitHub {repo}: 已存在，跳过")
    else:
        print(f"  GitHub {repo}: 失败 {status} {body}")
        ok = False

# Gitee：access_token 走查询参数
gitee_token = os.environ.get("GITEE_TOKEN", "")
status, body = http(
    "POST",
    f"https://gitee.com/api/v5/repos/badhope/pocketmind/releases?access_token={gitee_token}",
    "",
    {"tag_name": tag, "name": title, "body": notes, "prerelease": False, "target_commitish": "main"},
)
if status == 201:
    print("  Gitee badhope/pocketmind: 已创建")
elif status in (400, 409):
    print(f"  Gitee badhope/pocketmind: 已存在或拒绝重复（{status}），跳过")
else:
    print(f"  Gitee badhope/pocketmind: 失败 {status} {body}")
    ok = False

# GitCode：v5 API，Bearer
gitcode_token = os.environ.get("GITCODE_TOKEN", "")
status, body = http(
    "POST",
    "https://gitcode.com/api/v5/repos/badhope/pocketmind/releases",
    gitcode_token,
    {"tag_name": tag, "name": title, "body": notes, "prerelease": False, "target_commitish": "main"},
)
if status in (200, 201):
    print("  GitCode badhope/pocketmind: 已创建")
elif status in (400, 409):
    print(f"  GitCode badhope/pocketmind: 已存在或拒绝重复（{status}），跳过")
else:
    print(f"  GitCode badhope/pocketmind: 失败 {status} {body}")
    ok = False

sys.exit(0 if ok else 1)
PY
RELEASE_RC=$?
rm -f "$BODY_FILE" "$BODY_FILE.changelog"
echo ""
if [ "$RELEASE_RC" = 0 ]; then
  echo "完成。$TAG"
else
  echo "Release 创建有失败项（见上）。推送与 tag 已完成。" >&2
fi
exit "$RELEASE_RC"
