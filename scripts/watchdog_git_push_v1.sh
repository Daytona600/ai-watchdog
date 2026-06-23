#!/usr/bin/env bash
# Manually push reviewed local watchdog commits to the remote.
# Run by hand after checking `git log` / `git status` locally — this
# script does not auto-trigger from the master pipeline.
set -u

BASE="$HOME/ai-watchdog"
cd "$BASE" || exit 1

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
AHEAD="$(git rev-list --count "origin/$BRANCH..$BRANCH" 2>/dev/null || echo 0)"

if [ "$AHEAD" = "0" ]; then
  echo "Nothing to push. Local $BRANCH is up to date with origin/$BRANCH."
  exit 0
fi

echo "Pushing $AHEAD commit(s) on $BRANCH to origin:"
echo ""
git --no-pager log "origin/$BRANCH..$BRANCH" --oneline
echo ""

if git push origin "$BRANCH"; then
  echo "Pushed."
else
  echo "Push failed."
  exit 1
fi
