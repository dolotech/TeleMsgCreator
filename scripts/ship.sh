#!/usr/bin/env bash
#
# scripts/ship.sh —— 规范化推送
#
# 推送前把该查的都查一遍，任何一项不过就直接停，不做「先推了再说」。
#
#   scripts/ship.sh                 推送当前分支
#   scripts/ship.sh --dry-run       只跑检查，不推送
#   scripts/ship.sh --skip-tests    跳过测试（仅限紧急修文档时使用）
#
set -euo pipefail

cd "$(dirname "$0")/.."

DRY_RUN=0
RUN_TESTS=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skip-tests) RUN_TESTS=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

step()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✔\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()   { printf '\n\033[1;31m✖ %s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 1. 仓库
step "检查仓库状态"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" != "HEAD" ] || die "处于 detached HEAD，先切到分支再推送"
ok "分支：$BRANCH"

if [ -n "$(git status --porcelain)" ]; then
  git status --short
  die "有未提交的改动。请先 git add + git commit，或 git stash 后再推送"
fi
ok "工作区干净"

UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
[ -n "$UPSTREAM" ] || die "分支 $BRANCH 没有上游。先执行：git push -u origin $BRANCH"
ok "上游：$UPSTREAM"

# ------------------------------------------------------------ 2. 敏感信息
step "扫描敏感信息"
if ! git check-ignore -q .env; then
  die ".env 没有被 .gitignore 忽略——这会把 Bot Token 提交进仓库"
fi
ok ".env 已被忽略"

# 形如 123456789:AAH... 的 token
LEAK_RE='[0-9]{6,10}:[A-Za-z0-9_-]{30,}'
# 允许的占位写法（示例、测试假值）
PLACEHOLDER_RE='(Example|example|EXAMPLE|fake|FAKE|Fake|xxxx|XXX|redacted|REDACTED|YOUR|your_)'

LEAKS=$(git grep -I -nE "$LEAK_RE" -- . ':!*.example' 2>/dev/null | grep -vE "$PLACEHOLDER_RE" || true)
if [ -n "$LEAKS" ]; then
  printf '%s\n' "$LEAKS" | head -10
  die "疑似把真实凭证写进了代码。若确为示例值，请改写成含 xxx / Example 的占位形式"
fi
ok "没有发现疑似真实凭证"

# 与本机 .env 里的真实 token 精确比对
if [ -f .env ]; then
  REAL_TOKEN=$(grep -E '^TELEMSG_BOT_TOKEN=' .env | head -1 | cut -d= -f2- || true)
  if [ -n "${REAL_TOKEN:-}" ] && git grep -qF "$REAL_TOKEN" -- . >/dev/null 2>&1; then
    git grep -nF "$REAL_TOKEN" -- .
    die "检测到 .env 中的真实 Bot Token 已存在于被跟踪文件里"
  fi
  [ -n "${REAL_TOKEN:-}" ] && ok ".env 中的 token 未出现在任何被跟踪文件里"
fi

for pattern in 'PRIVATE KEY' 'ssh-rsa AAAA'; do
  if git grep -qI -F "$pattern" -- . 2>/dev/null; then
    die "检测到疑似私钥内容（$pattern）"
  fi
done
ok "没有发现私钥"

# ---------------------------------------------------------------- 3. 质量
if [ "$RUN_TESTS" -eq 1 ]; then
  step "运行测试与静态检查"
  "$PY" -m ruff check src tests scripts || die "ruff 检查未通过"
  ok "ruff 通过"
  "$PY" -m pytest -q || die "测试未通过"
  ok "测试通过"
else
  step "跳过测试（--skip-tests）"
  warn "本次没有跑测试，请确认这是有意为之"
fi

# ---------------------------------------------------------------- 4. 同步
step "与远端同步"
git fetch origin --quiet || die "git fetch 失败，检查网络或 SSH 配置"
AHEAD=$(git rev-list --count "$UPSTREAM"..HEAD 2>/dev/null || echo 0)
BEHIND=$(git rev-list --count HEAD.."$UPSTREAM" 2>/dev/null || echo 0)
ok "领先 $AHEAD 个提交，落后 $BEHIND 个提交"

if [ "$BEHIND" -gt 0 ] && [ "$AHEAD" -gt 0 ]; then
  die "本地与远端已分叉。请先 git pull --rebase 解决冲突，再重新推送"
fi
if [ "$BEHIND" -gt 0 ]; then
  warn "远端有新提交，正在快进本地"
  git merge --ff-only "$UPSTREAM" || die "无法快进，请手动处理"
fi
if [ "$AHEAD" -eq 0 ]; then
  ok "没有需要推送的提交，已是最新"
  exit 0
fi

step "即将推送的提交"
git log --oneline --no-decorate "$UPSTREAM"..HEAD | sed 's/^/  /'

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\n\033[33m--dry-run：检查全部通过，未执行推送\033[0m\n'
  exit 0
fi

# ---------------------------------------------------------------- 5. 推送
step "推送到 $UPSTREAM"
git push origin "$BRANCH" || die "推送失败"

git fetch origin --quiet
LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse "$UPSTREAM")
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || die "推送后本地与远端仍不一致"
ok "已同步到 $UPSTREAM @ ${LOCAL_SHA:0:7}"

printf '\n\033[1;32m完成\033[0m\n'
