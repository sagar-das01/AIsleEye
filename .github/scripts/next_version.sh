#!/usr/bin/env bash
# Usage: next_version.sh <source-branch> <current-version e.g. v1.2.3>
# Prints "<bump> <next-version>" or "none <current-version>" when the branch type doesn't release.
set -euo pipefail

branch="${1:?source branch required}"
current="${2:-v0.0.0}"

case "$branch" in
  major/*|breaking/*)                 bump=major ;;
  release/*|feature/*|feat/*)         bump=minor ;;
  fix/*|bug/*|bugfix/*|hotfix/*)      bump=patch ;;
  *)                                  bump=none ;;
esac

if [[ ! "$current" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "invalid current version: $current" >&2
  exit 1
fi
major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"; patch="${BASH_REMATCH[3]}"

case "$bump" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
esac

echo "$bump v${major}.${minor}.${patch}"
