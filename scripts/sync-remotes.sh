#!/usr/bin/env bash
# 把当前仓库同步到全部远程并核对一致性。
#
# 用法：
#   bash scripts/sync-remotes.sh                 # 推 main 到全部远程
#   bash scripts/sync-remotes.sh --tags          # 同时推 tags
#   bash scripts/sync-remotes.sh --dry-run       # 只打印将要执行的命令
#
# 前置：本机凭据库已注入（见 ~/.zcode/secrets/README.md）。URL 里不要写 token。
# 退出码：0 = 所有远程都推成功且 tree 一致；1 = 有远程失败或 tree 不一致。
set -uo pipefail
cd "$(dirname "$0")/.."

PUSH_TAGS=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --tags) PUSH_TAGS=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

REMOTES=(
  "origin|https://gitcode.com/badhope/pocketmind.git"
  "gitee|https://gitee.com/badhope/pocketmind.git"
  "github|https://github.com/X33834/pocketmind.git"
  "gh-morning|https://github.com/Morningstar202604/pocketmind.git"
  "gh-badhope|https://github.com/badhope/pocketmind.git"
)

run() {
  if [ "$DRY" = 1 ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

echo "== 本地 HEAD =="
LOCAL_SHA="$(git rev-parse HEAD)"
LOCAL_TREE="$(git rev-parse HEAD^{tree})"
echo "commit $LOCAL_SHA"
echo "tree   $LOCAL_TREE"

FAILED=0
for entry in "${REMOTES[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  echo ""
  echo "== $name =="
  if ! git remote get-url "$name" >/dev/null 2>&1; then
    echo "  添加远程 $url"
    run git remote add "$name" "$url" || { FAILED=1; continue; }
  else
    # 远程地址漂移会让「推了但推错地方」很难发现，显式纠正
    current="$(git remote get-url "$name")"
    if [ "$current" != "$url" ]; then
      echo "  远程地址从 $current 纠正为 $url"
      run git remote set-url "$name" "$url" || { FAILED=1; continue; }
    fi
  fi
  if ! run git push "$name" HEAD:main; then
    echo "  ✗ 推送失败"
    FAILED=1
    continue
  fi
  if [ "$PUSH_TAGS" = 1 ]; then
    run git push "$name" --tags || echo "  （tags 推送失败，通常是无新 tag，忽略）"
  fi
  if [ "$DRY" = 1 ]; then continue; fi
  remote_sha="$(git ls-remote --heads "$name" main 2>/dev/null | awk '{print $1}')"
  if [ "$remote_sha" = "$LOCAL_SHA" ]; then
    echo "  ✓ main 已同步（$remote_sha）"
  else
    echo "  ✗ main 未同步：remote=$remote_sha local=$LOCAL_SHA"
    FAILED=1
  fi
done

echo ""
if [ "$FAILED" = 1 ]; then
  echo "结果：存在失败项（见上）"
  exit 1
fi
if [ "$DRY" = 1 ]; then
  echo "结果：dry-run 完成（未实际推送；上面列出的命令即真实执行内容）"
  exit 0
fi
echo "结果：全部远程已同步到 $LOCAL_SHA"
