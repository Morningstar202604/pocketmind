#!/usr/bin/env bash
# 发布一个版本：本地打 tag → 推全部远程 → 在各仓库建 Release。
#
# 用法：
#   bash scripts/release.sh 1.2.0              # 从当前 HEAD 发布
#   bash scripts/release.sh 1.2.0 --dry-run    # 只打印计划
#
# 前置：
#   - gh CLI 已登录（gh auth status）；未登录时会跳过 Release 创建但仍完成推送
#   - 本机凭据库已注入；GitHub 远程用 X33834 账号（gh 默认身份）
#   - 门禁全绿：脚本会先跑 `bash tests/run_all.sh --allow-dirty-dist`（可用 SKIP_GATE=1 跳过）
#
# 说明：GitHub 镜像的历史 hash 与主仓不同，若某镜像不是同源克隆，
# 直接 `git push` 会被拒绝；此时脚本会提示用 `--force-with-lease`。不要盲目强推。
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
  echo "  $TAG 已存在，跳过创建"
else
  if [ "$DRY" = 1 ]; then
    echo "  [dry-run] git tag -a $TAG -m \"$TAG\""
  else
    git tag -a "$TAG" -m "$TAG" || exit 1
  fi
fi

echo ""
echo "== 推送 =="
bash scripts/sync-remotes.sh --tags || echo "推送有失败项，继续尝试建 Release" 

echo ""
echo "== 创建 Release =="
if ! command -v gh >/dev/null 2>&1; then
  echo "  未安装 gh，跳过 Release 创建（可在网页手工创建）"
  exit 0
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "  gh 未登录，跳过 Release 创建（gh auth login 后重跑本步）"
  exit 0
fi
NOTES="$(git tag -l --format='%(contents:subject)' "$TAG" 2>/dev/null || true)"
BODY_FILE="$(mktemp)"
{
  echo "PocketMind $TAG"
  echo ""
  echo "完整变更见 CHANGELOG.md。"
  echo ""
  echo "- 安装：\`bash termux/install.sh && bash termux/start.sh\`"
  echo "- 门禁：\`bash tests/run_all.sh\`"
  echo "- 演示：\`docs/demo/pocketmind-demo.mp4\`"
} > "$BODY_FILE"
for repo in X33834/pocketmind Morningstar202604/pocketmind badhope/pocketmind; do
  echo "  $repo"
  if [ "$DRY" = 1 ]; then
    echo "    [dry-run] gh release create $TAG --repo $repo --title \"PocketMind $TAG\" --notes-file ..."
    continue
  fi
  if gh release view "$TAG" --repo "$repo" >/dev/null 2>&1; then
    echo "    已存在，跳过"
  else
    gh release create "$TAG" --repo "$repo" --title "PocketMind $TAG" --notes-file "$BODY_FILE" \
      || echo "    创建失败（可能该镜像没有此 tag）"
  fi
done
rm -f "$BODY_FILE"
echo ""
echo "完成。$TAG"
