#!/usr/bin/env bash
# Commit the bot's signals / research to main (used twice by the workflow: right after a research merge and at the end).
# The PC auto-trader may have pushed signals meanwhile: keep both sides (union by signal id), retry on a race.
git config user.name "tradinglab-bot"
git config user.email "tradinglab-bot@users.noreply.github.com"
for i in 1 2 3 4; do
  git fetch -q origin main
  python -m tools.merge_signals origin/main
  git add data/signals.json data/research; git add data/cloud_status.json 2>/dev/null || true
  git checkout -- . 2>/dev/null || true     # drop other edits (pc_status.json copied from origin by the scan) so the rebase can run
  if ! git diff --cached --quiet; then git commit -q -m "${1:-cloud: update signals/research} [skip ci]"; fi
  if [ "$(git rev-list --count origin/main..HEAD)" = "0" ]; then echo "nothing to push"; exit 0; fi
  git rebase -X theirs origin/main || { git rebase --abort; git reset -q --soft origin/main; continue; }
  if git push -q origin HEAD:main; then exit 0; fi
done
echo "::warning::could not push signals after 4 tries"
