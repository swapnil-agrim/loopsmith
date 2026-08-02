#!/usr/bin/env bash
# LoopSmith — discovery scan (read-only, deterministic).
#
# Greps tracked source for mechanical tech-debt signals and emits candidate
# backlog items as JSON. It writes NOTHING — `pipeline.py discover` turns
# candidates into `proposed` goal files (which the loop never runs until a human
# promotes them to `pending`). Two v1 signals, aggregated per file:
# TODO/FIXME/HACK/XXX markers (tech-debt) and skipped/xfail tests (test-gap).
#
# Principles (same as the other read-only collectors, e.g. risk-detect.sh):
#   READ-ONLY & DETERMINISTIC · FAIL-OPEN (no git → empty, exit 0) ·
#   SECRET SAFETY — a candidate carries the marker LOCATION (file:line) + a count,
#   NEVER the comment/marker text (a TODO may contain a secret).
#
# Output: {"schema":"discovery-scan/v1","candidates":[{title,category,source,priority,evidence[]}...]}
# jq-free, bash-3.2-safe, zero-dep.

set -uo pipefail
SCHEMA="discovery-scan/v1"

json_string() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}
append() { local n="$1" cur="${!1}"; if [ -z "$cur" ]; then printf -v "$n" '%s' "$2"; else printf -v "$n" '%s,%s' "$cur" "$2"; fi; }

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
emit_empty() { printf '{"schema":%s,"candidates":[]}\n' "$(json_string "$SCHEMA")"; }

command -v git >/dev/null 2>&1 || { emit_empty; exit 0; }
git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { emit_empty; exit 0; }

# Search tracked source only; exclude the harness, docs, and common vendor dirs.
EXCL=(-- . ':(exclude).sdlc/**' ':(exclude)docs/**' ':(exclude)node_modules/**' ':(exclude)vendor/**' ':(exclude)dist/**' ':(exclude)build/**')

# NOTE: git grep's default engine is POSIX ERE, which has NO `\b` word boundary —
# we bracket markers with line-edges / non-letters instead so they match portably
# without -P (PCRE, which isn't guaranteed to be compiled in).
TODO_RE='(^|[^A-Za-z])(TODO|FIXME|HACK|XXX)([^A-Za-z]|$)'
SKIP_RE='(\.skip\(|it\.skip|describe\.skip|test\.skip|xit\(|xdescribe\(|@pytest\.mark\.skip|@unittest\.skip|xfail|t\.Skip\(|@Ignore|@Disabled)'

CANDS=""

# v1 LIMITATION (parity with the other collectors): git grep -l C-quotes paths
# containing `"`/`\`/control bytes, so a marker in an exotically named file is
# silently DROPPED (fail-safe under-report — never a crash or leak).
scan_category() { # regex category title_fmt priority
  local re="$1" cat="$2" fmt="$3" prio="$4"
  local file row rest ln ev cnt evcount title
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    ev=""; cnt=0; evcount=0
    # Per-file: collect line numbers ONLY (strip the known "file:" prefix, take
    # the leading digits; the match text after is never read into output).
    while IFS= read -r row; do
      rest="${row#"$file":}"; ln="${rest%%:*}"
      case "$ln" in (''|*[!0-9]*) continue ;; esac
      cnt=$((cnt + 1))
      if [ "$evcount" -lt 10 ]; then append ev "$(json_string "$file:$ln")"; evcount=$((evcount + 1)); fi
    done < <(git -C "$PROJECT_DIR" grep -nI -E "$re" -- "$file" 2>/dev/null)
    [ "$cnt" -gt 0 ] || continue
    title="$(printf "$fmt" "$cnt" "$file")"
    append CANDS "$(printf '{"title":%s,"category":%s,"source":"discovery","priority":%s,"count":%s,"evidence":[%s]}' \
      "$(json_string "$title")" "$(json_string "$cat")" "$(json_string "$prio")" "$cnt" "$ev")"
  done < <(git -C "$PROJECT_DIR" grep -lI -E "$re" "${EXCL[@]}" 2>/dev/null | LC_ALL=C sort)
}

scan_category "$TODO_RE" tech-debt "Resolve %s TODO/FIXME marker(s) in %s" low
scan_category "$SKIP_RE" test-gap  "Re-enable %s skipped test(s) in %s"    med

printf '{"schema":%s,"candidates":[%s]}\n' "$(json_string "$SCHEMA")" "$CANDS"
exit 0
