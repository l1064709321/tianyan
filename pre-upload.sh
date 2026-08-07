#!/usr/bin/env bash
# pre-upload.sh — 上传前安全扫描
# 用法: bash pre-upload.sh
# 退出码 0 = 干净可上传;非 0 = 发现敏感信息,已拦截
set -u
cd "$(dirname "$0")"

echo "==> [1/4] 确认 git 仓库..."
[ -d .git ] || { echo "✗ 不是 git 仓库,先 git init"; exit 1; }

echo "==> [2/4] 扫描 .gitignore 必含项..."
MISSING=()
for f in config.yaml __pycache__ data .tianyan "*.log"; do
  grep -qE "^${f//\*/\\*}" .gitignore 2>/dev/null || MISSING+=("$f")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "⚠ .gitignore 缺少: ${MISSING[*]} (建议补上)"
fi

echo "==> [3/4] 扫描暂存区与改动文件中的敏感信息..."
# 模式:常见密钥前缀、token、私钥头
PATTERNS=(
  'sk-[a-zA-Z0-9_\-]{16,}'          # OpenAI/DeepSeek 风格
  'sk-ant-[a-zA-Z0-9_\-]{16,}'      # Anthropic
  'AIza[a-zA-Z0-9_\-]{16,}'         # Google API key
  'github_pat_[a-zA-Z0-9_]{20,}'    # GitHub PAT
  'ghp_[a-zA-Z0-9]{20,}'            # GitHub classic PAT
  'xox[baprs]-[a-zA-Z0-9\-]{10,}'  # Slack
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)

# 扫描范围:已暂存 + 已修改(未暂存) + 未追踪 的文本文件
FILES=$(git status --short --untracked-files=all | awk '{print $2}')
[ -z "$FILES" ] && { echo "✓ 无改动文件"; exit 0; }

HITS=0
for f in $FILES; do
  [ -f "$f" ] || continue
  # 跳过二进制
  file "$f" 2>/dev/null | grep -qi "text" || continue
  for pat in "${PATTERNS[@]}"; do
    if grep -qE "$pat" "$f" 2>/dev/null; then
      echo "✗ 命中敏感模式 [$pat] → $f"
      grep -nE "$pat" "$f" 2>/dev/null | head -3
      HITS=$((HITS+1))
    fi
  done
done

if [ "$HITS" -gt 0 ]; then
  echo ""
  echo "✗ 发现 $HITS 处敏感信息,已拦截上传。请删除/替换为环境变量后再提交。"
  exit 2
fi

echo "==> [4/4] 确认将被上传的文件清单(已遵循 .gitignore):"
git status --short --untracked-files=all | head -30
echo "..."
echo ""
echo "✓ 扫描通过,可安全上传。"
