#!/usr/bin/env bash
# Run on the Mac after committing a completed change on main.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

fail() { echo "$*" >&2; exit 1; }

[[ "$(git branch --show-current)" == main ]] || fail 'Switch to main before syncing.'
[[ -z "$(git status --porcelain)" ]] || fail 'Commit or resolve local changes before syncing.'
[[ "$(git remote get-url origin)" == 'jetson:repos/baby-monitor-public.git' ]] || fail 'Unexpected origin; review the synchronization configuration.'
[[ "$(git remote get-url --push origin)" == 'jetson:repos/baby-monitor-public.git' ]] || fail 'Unexpected push destination; review the synchronization configuration.'

# Preserve edits on the Jetson. Published remote commits can be pulled normally.
ssh -o BatchMode=yes -o ConnectTimeout=10 jetson '
set -eu
cd "$HOME/baby-monitor-public"
test "$(git branch --show-current)" = main || { echo "Jetson is not on main." >&2; exit 1; }
test -z "$(git status --porcelain)" || { echo "Jetson has uncommitted changes; commit and push them there first." >&2; exit 1; }
git fetch origin
test "$(git rev-list --count origin/main..HEAD)" = 0 || { echo "Jetson has unpublished commits; push them there first." >&2; exit 1; }
'

# Fast-forward only: divergence needs an explicit merge or rebase by the developer.
git pull --ff-only origin main
git push origin main

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 jetson '
set -eu
cd "$HOME/baby-monitor-public"
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
git pull --ff-only origin main
'; then
    fail 'Push succeeded, but the Jetson working copy did not update. Resolve its state and rerun; no reset was performed.'
fi

local_commit="$(git rev-parse HEAD)"
remote_commit="$(ssh -o BatchMode=yes -o ConnectTimeout=10 jetson 'git -C "$HOME/baby-monitor-public" rev-parse HEAD')"
[[ "$local_commit" == "$remote_commit" ]] || fail 'Commit IDs differ; inspect both repositories before continuing.'
echo "Mac and Jetson are synchronized: $local_commit"
