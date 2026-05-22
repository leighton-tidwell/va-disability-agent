#!/usr/bin/env bash
# Install agent skills pinned in skills-lock.json into .agents/skills/
# and symlink .claude/skills -> ../.agents/skills so Claude Code finds them.
#
# Requires: git, jq

set -euo pipefail

LOCKFILE="skills-lock.json"
AGENTS_DIR=".agents/skills"
CLAUDE_LINK=".claude/skills"

if [[ ! -f "$LOCKFILE" ]]; then
  echo "error: $LOCKFILE not found" >&2
  exit 1
fi

command -v jq >/dev/null || { echo "error: jq is required" >&2; exit 1; }
command -v git >/dev/null || { echo "error: git is required" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$AGENTS_DIR"

# Group skills by source repo so we clone each repo once.
sources=$(jq -r '[.skills[] | select(.sourceType == "github") | .source] | unique | .[]' "$LOCKFILE")

for source in $sources; do
  repo_dir="$tmp/$(echo "$source" | tr '/' '_')"
  echo "==> cloning $source"
  git clone --depth 1 --quiet "https://github.com/$source.git" "$repo_dir"

  # For each skill backed by this source, copy its skill directory.
  jq -r --arg src "$source" '
    .skills | to_entries[]
    | select(.value.source == $src and .value.sourceType == "github")
    | "\(.key)\t\(.value.skillPath)"
  ' "$LOCKFILE" | while IFS=$'\t' read -r name skill_path; do
    skill_src_dir="$repo_dir/$(dirname "$skill_path")"
    skill_dest_dir="$AGENTS_DIR/$name"

    if [[ ! -d "$skill_src_dir" ]]; then
      echo "  ! skipping $name: $skill_src_dir not found in repo" >&2
      continue
    fi

    rm -rf "$skill_dest_dir"
    cp -R "$skill_src_dir" "$skill_dest_dir"
    echo "  + $name"
  done
done

# Symlink .claude/skills -> ../.agents/skills (Claude Code reads .claude/skills).
mkdir -p .claude
if [[ -L "$CLAUDE_LINK" ]]; then
  rm "$CLAUDE_LINK"
elif [[ -e "$CLAUDE_LINK" ]]; then
  echo "error: $CLAUDE_LINK exists and is not a symlink; refusing to overwrite" >&2
  exit 1
fi
ln -s "../$AGENTS_DIR" "$CLAUDE_LINK"

echo "==> done. $(ls "$AGENTS_DIR" | wc -l | tr -d ' ') skills installed."
