#!/usr/bin/env bash
# Start a release: tag the release branch as vX.Y.Z and push the tag. CI does
# the rest — it builds the image, publishes it and opens the PR in home-ops.
# A laptop never deploys. `deploy` runs this when base.release.enable is set.
#
#   deploy patch|minor|major
set -euo pipefail

BRANCH="${BASE_RELEASE_BRANCH:-main}"

die() {
  printf '\033[31m✗\033[0m %s\n' "$*" >&2
  exit 1
}

bump="${1:-}"
case "$bump" in
  patch | minor | major) ;;
  *) die "usage: deploy patch|minor|major" ;;
esac

[ -z "$(git status --porcelain)" ] || die "the working tree has changes — commit or stash them first"

current="$(git rev-parse --abbrev-ref HEAD)"
[ "$current" = "$BRANCH" ] || die "release from $BRANCH, not $current"

# The tag must point at what CI can fetch, so local and remote must agree.
git fetch --quiet --tags origin "$BRANCH"
[ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ] ||
  die "$BRANCH and origin/$BRANCH differ — pull or push first"

# The same suite CI runs. Skipped only where the shell has no `check`, which
# keeps this script testable on its own.
if command -v check >/dev/null; then
  check
fi

last="$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1)"
last="${last:-v0.0.0}"
IFS=. read -r major minor patch <<<"${last#v}"
case "$bump" in
  major) major=$((major + 1)) minor=0 patch=0 ;;
  minor) minor=$((minor + 1)) patch=0 ;;
  patch) patch=$((patch + 1)) ;;
esac
next="v$major.$minor.$patch"

git rev-parse -q --verify "refs/tags/$next" >/dev/null && die "tag $next already exists"

# A project that keeps a version in its own files gives a hook to write it, so
# the tag, the files and what the world reads cannot drift apart. The hook runs
# before the tag, and its change goes in its own commit, because the tree has to
# be clean again before the tag is made.
version="${next#v}"
if [ -x "scripts/set-version.sh" ]; then
  ./scripts/set-version.sh "$version"
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit --quiet -m "release $next"
    git push --quiet origin "$BRANCH"
    printf '\033[32m✓\033[0m wrote version %s and committed it\n' "$version"
  fi
fi

git tag -a "$next" -m "release $next"
git push --quiet origin "$next"
printf '\033[32m✓\033[0m tagged %s (was %s) and pushed it — CI releases it\n' "$next" "$last"

# Follow the run when gh can see it. The run needs a few seconds to appear.
# BASE_RELEASE_NO_WATCH=1 skips this, for scripts and the test suite.
if [ -z "${BASE_RELEASE_NO_WATCH:-}" ] && command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  sha="$(git rev-parse HEAD)"
  run=""
  for _ in 1 2 3 4 5 6; do
    sleep 5
    run="$(gh run list --commit "$sha" --event push --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)"
    [ -n "$run" ] && break
  done
  if [ -n "$run" ]; then
    gh run watch "$run" --exit-status
  else
    printf 'no CI run found yet — follow it with: gh run list --commit %s\n' "$sha"
  fi
fi
