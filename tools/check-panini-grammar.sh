#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
grammar="${1:-$repo_root/docs/grammars/panini-core.y}"
output_dir="${2:-${TMPDIR:-/tmp}/panini-grammar}"

if ! command -v bison >/dev/null 2>&1; then
  echo "error: GNU Bison is required to check $grammar" >&2
  exit 127
fi

mkdir -p "$output_dir"

bison \
  --warnings=all \
  -Werror=conflicts-sr \
  -Werror=conflicts-rr \
  --report=all \
  --report-file="$output_dir/panini-core.output" \
  --graph="$output_dir/panini-core.dot" \
  --xml="$output_dir/panini-core.xml" \
  --header="$output_dir/panini-core.h" \
  --output="$output_dir/panini-core.c" \
  "$grammar"

state_count="$(sed -n 's/^State \([0-9][0-9]*\)$/\1/p' "$output_dir/panini-core.output" | tail -1)"
if [[ -n "$state_count" ]]; then
  state_count="$((state_count + 1))"
else
  state_count="unknown"
fi

cat <<EOF
Panini grammar is LALR(1): no shift/reduce or reduce/reduce conflicts.
Automaton states: $state_count
Parser source:    $output_dir/panini-core.c
Token header:     $output_dir/panini-core.h
State report:     $output_dir/panini-core.output
Graphviz graph:   $output_dir/panini-core.dot
XML report:       $output_dir/panini-core.xml
EOF
