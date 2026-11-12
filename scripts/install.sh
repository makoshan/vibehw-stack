#!/usr/bin/env bash
# vibehw-stack 一键安装脚本（把 22 个硬件技能软链到 Claude Code / Codex / Agent Skills 目录）
# 用法：
#   bash scripts/install.sh
#   bash scripts/install.sh --ref v0.3.5
set -euo pipefail
SRC="${VIBEHW_SRC:-$HOME/.local/share/vibehw-stack}"
REPO_URL="${VIBEHW_REPO_URL:-https://github.com/makoshan/vibehw-stack.git}"
SKILL_DIRS="${VIBEHW_SKILL_DIRS:-$HOME/.claude/skills $HOME/.codex/skills $HOME/.agents/skills}"
REF="${1:-}"
if [ "${1:-}" = "--ref" ]; then REF="${2:?--ref 需要 tag/branch}"; fi

if [ -e "$SRC/.git" ]; then
  git -C "$SRC" fetch -q --tags origin
  br=$(git -C "$SRC" symbolic-ref --short -q HEAD || true)
  if [ -n "$REF" ]; then git -C "$SRC" checkout -q "$REF"
  elif [ "$br" = main ]; then git -C "$SRC" pull -q --ff-only origin main
  else echo "源码在 ${br:-游离 HEAD} 上，按当前内容安装" >&2; fi
elif [ -f "$(cd "$(dirname "$0")/.." && pwd)/plugin.json" ]; then
  SRC="$(cd "$(dirname "$0")/.." && pwd)"
else
  git clone -q "$REPO_URL" "$SRC"
  if [ -n "$REF" ]; then git -C "$SRC" checkout -q "$REF"; fi
fi

for d in $SKILL_DIRS; do
  mkdir -p "$d"
  for sk in "$SRC"/skills/*/; do
    s=$(basename "$sk")
    if [ -e "$d/$s" ] && [ ! -L "$d/$s" ]; then
      echo "跳过 ${d}/${s}：已存在实体目录（非软链）" >&2
      continue
    fi
    ln -sfn "${sk%/}" "$d/$s"
  done
done

echo "vibehw-stack $(grep -m1 '^version:' "$SRC/skills/vibehw-stack/SKILL.md" | awk '{print $2}') → ${SRC}（已链接至：${SKILL_DIRS}）"
