#!/usr/bin/env bash
# Commits any uncommitted changes in the ai-watchdog repo, so manual edits
# made during the day (or files the watchdog/AI pipeline writes) get a
# timestamped checkpoint instead of sitting dirty.
#
# Usage: watchdog_git_autocommit_v1.sh [label]
#   label - shown in the commit message, e.g. "pre-run" or "post-run".
set -u

BASE="$HOME/ai-watchdog"
CONF="$BASE/config/watchdog_git_autocommit.conf"
LABEL="${1:-checkpoint}"

GIT_AUTOCOMMIT_ENABLED="1"
GIT_AUTOCOMMIT_PUSH="0"
GIT_AUTOCOMMIT_NAME="AI Watchdog"
GIT_AUTOCOMMIT_EMAIL="watchdog@localhost"

[ -f "$CONF" ] && source "$CONF"

if [ "$GIT_AUTOCOMMIT_ENABLED" != "1" ]; then
  echo "Git autocommit disabled. Skipping."
  exit 0
fi

cd "$BASE" || exit 1

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit ($LABEL)."
  exit 0
fi

git add -A

STAMP="$(date +'%Y-%m-%d %H:%M:%S')"
git -c user.name="$GIT_AUTOCOMMIT_NAME" -c user.email="$GIT_AUTOCOMMIT_EMAIL" \
  commit -m "Auto-commit ($LABEL): $STAMP" >/dev/null

echo "Committed changes ($LABEL) at $STAMP."
git --no-pager log -1 --stat

if [ "$GIT_AUTOCOMMIT_PUSH" = "1" ]; then
  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  if git push origin "$CURRENT_BRANCH"; then
    echo "Pushed to origin/$CURRENT_BRANCH."
  else
    echo "Push failed; commit is local only."
  fi
fi
