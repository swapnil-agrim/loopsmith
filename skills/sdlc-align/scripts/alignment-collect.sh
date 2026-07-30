#!/usr/bin/env bash
# LoopSmith — alignment-review evidence collector (read-only, deterministic).
#
# Gathers FACTS from git history + SDLC artifacts over an N-day window and
# prints ONE structured JSON "evidence pack" to stdout. It renders NO scores
# and NO verdicts — only facts and flags. The sdlc-align skill judges the pack.
#
# Three principles, non-negotiable (same as the other read-only collectors):
#   1. READ-ONLY & DETERMINISTIC. Only reads the repo. Same state + same window
#      => byte-identical output. Never mutates, commits, or opens a PR.
#   2. FAIL-OPEN. Any missing dep, non-git tree, or unparseable input -> a valid
#      MINIMAL JSON object with a machine-readable degraded[] code, exit 0.
#      Non-zero exit is reserved ONLY for "could not emit even minimal JSON".
#   3. SECRET SAFETY. The hard-stop scan reads diff bodies to find candidate
#      secrets/SQL/auth/contract patterns. For any hit it emits ONLY
#      {commit,file,line,pattern_id} — never the matched substring, the diff
#      line, a captured group, or a redacted sample. The diff text is scanned
#      in-stream and discarded. stdout is LLM-facing and may be committed.
#
# Usage: alignment-collect.sh [--since-days N]   (N integer, default 1)
# jq-free, bash-3.2-safe, zero-dep.

set -uo pipefail

SCHEMA="alignment-collect/v1"

# -- JSON helpers (no jq dependency) -------------------------------------------
json_string() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

# -- degraded[] accumulator ----------------------------------------------------
DEGRADED=""   # space-separated unique codes
add_degraded() {
  case " $DEGRADED " in *" $1 "*) return ;; esac
  DEGRADED="${DEGRADED:+$DEGRADED }$1"
}
degraded_json() {
  local first=1 c
  printf '['
  for c in $(printf '%s\n' $DEGRADED | LC_ALL=C sort); do
    [ "$first" -eq 1 ] && first=0 || printf ','
    json_string "$c"
  done
  printf ']'
}

# -- minimal fail-open pack (valid JSON, empty window/commits, empty d1..d7) ----
emit_minimal() {
  printf '{"schema":%s,' "$(json_string "$SCHEMA")"
  printf '"window":{"since_days":%d,"oldest":{"sha":"","date":""},"newest":{"sha":"","date":""},"commit_count":0},' "$SINCE_DAYS"
  printf '"degraded":%s,' "$(degraded_json)"
  printf '"commits":[],'
  printf '"dimensions":{'
  printf '"d1":{"commits_with_source":0,"commits_with_fresh_plan":0,"plan_existed_pct":0,"per_commit":[],"files_changed_outside_any_plan":[],"files_outside_plan_confidence":"low"},'
  printf '"d2":{"tests_touched_with_source_pct":0,"test_command_known":false},'
  printf '"d3":{"per_commit":[],"churn_hotspots":[]},'
  printf '"d4":{"net_lines_added_window":0,"new_files_added":0,"per_commit":[]},'
  printf '"d5":{"reviews_dir_present":false,"commits_with_review_pct":0},'
  printf '"d6":{"hits":[]},'
  printf '"d7":{"decisions_added":[],"repeated_revert_or_fixup_count":0}'
  printf '}}\n'
}

# -- parse args ----------------------------------------------------------------
SINCE_DAYS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --since-days)
      shift
      case "${1:-}" in (''|*[!0-9]*) : ;; (*) SINCE_DAYS="$1" ;; esac
      ;;
    --since-days=*)
      v="${1#--since-days=}"
      case "$v" in (''|*[!0-9]*) : ;; (*) SINCE_DAYS="$v" ;; esac
      ;;
  esac
  shift || true
done

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# -- fail-open gates -----------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  add_degraded "no_git"; emit_minimal; exit 0
fi
if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  add_degraded "no_git"; emit_minimal; exit 0
fi

# -- config (defaults + optional override) -------------------------------------
# Hardcoded inline. .sdlc/alignment-collect.conf is an OPTIONAL bash override only.
PLAN_FRESHNESS_HOURS=24
SOURCE_EXTS="js jsx ts tsx py go rb rs java kt swift c cc cpp h hpp cs php scala ex exs sh"
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.sdlc/alignment-collect.conf" ] && . "$PROJECT_DIR/.sdlc/alignment-collect.conf" 2>/dev/null

# Large-batch thresholds (inline constants).
LARGE_BATCH_LINES=400
LARGE_BATCH_FILES=15

# The loop's machine-accumulated knowledge (lessons/retros) is not "work in a
# direction" — exclude it from the commit walk, the way retrospectives were.
PATHSPEC_EXCLUDE=":(exclude).sdlc/knowledge/**"

# -- classify a path: source | test | docmeta | other -------------------------
# docs/ or the .sdlc/ layer = meta; else ext in SOURCE_EXTS = source; among
# source, *test*/*spec* = test; else other.
classify_path() {
  local f="$1" ext e
  case "$f" in docs/*|.sdlc/*|*/docs/*) printf 'docmeta'; return ;; esac
  ext="${f##*.}"
  for e in $SOURCE_EXTS; do
    if [ "$ext" = "$e" ]; then
      case "$f" in
        *test*|*spec*|*Test*|*Spec*|*_test.*|*.test.*|*.spec.*) printf 'test' ;;
        *) printf 'source' ;;
      esac
      return
    fi
  done
  printf 'other'
}

# -- freshness helper: is file mtime within PLAN_FRESHNESS_HOURS? --------------
file_fresh() {
  [ -f "$1" ] || return 1
  find "$1" -mmin "-$((PLAN_FRESHNESS_HOURS*60))" 2>/dev/null | grep -q .
}

# -- file mtime as epoch seconds (BSD/GNU stat) --------------------------------
date_mtime_epoch() {
  [ -f "$1" ] || return 1
  stat -f %m "$1" 2>/dev/null && return 0
  stat -c %Y "$1" 2>/dev/null && return 0
  return 1
}

# -- render a newline-delimited path list as a sorted JSON string array --------
NL=$'\n'
json_file_array() {
  local _label="$1" list="$2"
  if [ -z "$list" ]; then printf '[]'; return; fi
  printf '%s\n' "$list" | grep . | LC_ALL=C sort \
    | awk 'BEGIN{printf "["} { p=$0; gsub(/\\/,"\\\\",p); gsub(/"/,"\\\"",p);
            printf "%s\"%s\"", (NR>1?",":""), p } END{printf "]"}'
}

# -- window bounds -------------------------------------------------------------
SINCE_ARG="${SINCE_DAYS} days ago"

SHAS="$(git -C "$PROJECT_DIR" log --no-merges --reverse \
          --since="$SINCE_ARG" --format='%H' \
          -- . "$PATHSPEC_EXCLUDE" 2>/dev/null)" || SHAS=""

SHA_SORTED="$(printf '%s\n' $SHAS | grep . | LC_ALL=C sort)"
COMMIT_COUNT=0
[ -n "$SHA_SORTED" ] && COMMIT_COUNT="$(printf '%s\n' "$SHA_SORTED" | wc -l | tr -d ' ')"

OLDEST_SHA=""; OLDEST_DATE=""; NEWEST_SHA=""; NEWEST_DATE=""
if [ "$COMMIT_COUNT" -gt 0 ]; then
  read -r OLDEST_SHA OLDEST_DATE < <(git -C "$PROJECT_DIR" log --no-merges --reverse \
      --since="$SINCE_ARG" --format='%H %cI' -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | head -n1)
  read -r NEWEST_SHA NEWEST_DATE < <(git -C "$PROJECT_DIR" log --no-merges \
      --since="$SINCE_ARG" --format='%H %cI' -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | head -n1)
fi

# -- artifact inventories (paths relative to PROJECT_DIR) ----------------------
list_plan_paths() {
  [ -d "$PROJECT_DIR/.sdlc/plans" ] || return 0
  find "$PROJECT_DIR/.sdlc/plans" -maxdepth 1 -name '*.md' 2>/dev/null | LC_ALL=C sort
}
PLAN_PATHS="$(list_plan_paths)"

# .sdlc/reviews/ (risk-review + counter-review artifacts) presence.
REVIEWS_DIR_PRESENT=false
[ -d "$PROJECT_DIR/.sdlc/reviews" ] && REVIEWS_DIR_PRESENT=true

# -- d2: does the project DOCUMENT how it verifies? ----------------------------
# LoopSmith's proving command lives in .sdlc/config.json -> verify.command. We
# detect PRESENCE of a non-empty command (jq-free heuristic grep); we NEVER run
# anything derived from it.
TEST_COMMAND_KNOWN=false
if [ -f "$PROJECT_DIR/.sdlc/config.json" ] \
   && grep -Eq '"command"[[:space:]]*:[[:space:]]*"[^"]+' "$PROJECT_DIR/.sdlc/config.json" 2>/dev/null; then
  TEST_COMMAND_KNOWN=true
fi
[ "$TEST_COMMAND_KNOWN" = true ] || add_degraded "no_test_command"

# -- walk commits, accumulate per-commit + per-dimension facts -----------------
COMMITS_JSON=""
D1_PER=""; D3_PER=""; D4_PER=""; D6_HITS=""

COMMITS_WITH_SOURCE=0
COMMITS_WITH_FRESH_PLAN=0
COMMITS_SOURCE_WITH_TESTS=0
COMMITS_WITH_REVIEW=0
NET_LINES_WINDOW=0
NEW_FILES_WINDOW=0
REVERT_FIXUP_COUNT=0
RECOGNIZED_SOURCE_SEEN=0

declare -a HOTSPOT_FILES=()
OUTSIDE_PLAN_FILES=""

append() { # comma-join "$2" onto the accumulator named by "$1".
  local n="$1" cur="${!1}"
  if [ -z "$cur" ]; then printf -v "$n" '%s' "$2"; else printf -v "$n" '%s,%s' "$cur" "$2"; fi
}

# Scan one commit's diff for hard-stop candidates, emit LOCATION-ONLY hits.
# The matched bytes never reach a variable that is printed — only
# commit/file/line/pattern_id are captured (secret-safe).
# v1 LIMITATION: the unified-diff `+++` header has no `-z`/raw form, so for a hit
# on a file whose NAME contains a `"`, `\`, or control byte, the `file` field
# carries git's C-quoted literal (still valid JSON, still a findable location).
scan_hardstops() {
  local sha="$1"
  git -C "$PROJECT_DIR" show -p --no-color --format='' "$sha" \
      -- . "$PATHSPEC_EXCLUDE" 2>/dev/null \
    | awk -v sha="$sha" '
      function jesc(s){ gsub(/\\/,"\\\\",s); gsub(/"/,"\\\"",s); return s }
      /^\+\+\+ / { f=$0; sub(/^\+\+\+ [ab]\//,"",f); sub(/\t.*$/,"",f); file=f; next }
      /^@@ /     { h=$0; sub(/^@@ -[0-9,]+ \+/,"",h); sub(/[, ].*$/,"",h); newln=h+0; next }
      /^\+/ && $0 !~ /^\+\+\+/ {
        line=$0; sub(/^\+/,"",line)
        if (line ~ /(AWS_SECRET_ACCESS_KEY|aws_secret_access_key|api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|password)[ \t]*[:=]/)
          emit("secret","secret_key")
        else if (line ~ /(AKIA[0-9A-Z]{8}|ghp_[0-9A-Za-z]{8}|xox[baprs]-[0-9A-Za-z-]{8}|-----BEGIN[ A-Z]*PRIVATE KEY-----)/)
          emit("secret","generic_token")
        if (file ~ /\.env($|\.)/) emit("env_file","env_file")
        if (line ~ /(DROP[ \t]+(TABLE|DATABASE|SCHEMA)|TRUNCATE[ \t]+TABLE|DELETE[ \t]+FROM)/)
          emit("destructive_sql","destructive_sql")
        if (file ~ /\/(auth|billing|payment|payments|permissions)\// || line ~ /(stripe|[Cc]harge)/)
          emit("auth_billing","auth_billing")
        if (line ~ /(export[ \t]+(default[ \t]+)?(type|interface|enum)|public[ \t]+(api|interface))/)
          emit("exported_contract","exported_contract")
        newln++
        next
      }
      /^ / { newln++ }
      function emit(cat,pid,  loc) {
        loc = (newln>0 ? newln : 0)
        printf "{\"commit\":\"%s\",\"file\":\"%s\",\"line\":%d,\"pattern_id\":\"%s\",\"category\":\"%s\"}\n",
               jesc(sha), jesc(file), loc, jesc(pid), jesc(cat)
      }
    '
}

while IFS= read -r sha; do
  [ -n "$sha" ] || continue

  date="$(git -C "$PROJECT_DIR" show -s --format='%cI' "$sha" 2>/dev/null)"
  subject="$(git -C "$PROJECT_DIR" show -s --format='%s' "$sha" 2>/dev/null)"

  ins_total=0; del_total=0; file_count=0
  src_files=""; test_files=""; doc_files=""; other_files=""
  has_source=0; has_test=0
  while IFS= read -r -d '' tok; do
    ins="${tok%%$'\t'*}"; rest="${tok#*$'\t'}"
    del="${rest%%$'\t'*}"; path="${rest#*$'\t'}"
    if [ -z "$path" ]; then
      IFS= read -r -d '' _oldpath || true
      IFS= read -r -d '' path || true
    fi
    [ -n "${path:-}" ] || continue
    case "$ins" in (*[!0-9]*) ins=0 ;; esac
    case "$del" in (*[!0-9]*) del=0 ;; esac
    ins_total=$((ins_total + ins)); del_total=$((del_total + del))
    file_count=$((file_count + 1))
    HOTSPOT_FILES+=("$path")
    cls="$(classify_path "$path")"
    case "$cls" in
      source) has_source=1; RECOGNIZED_SOURCE_SEEN=1; src_files="${src_files}${src_files:+$NL}$path" ;;
      test)   has_source=1; has_test=1; RECOGNIZED_SOURCE_SEEN=1; test_files="${test_files}${test_files:+$NL}$path" ;;
      docmeta) doc_files="${doc_files}${doc_files:+$NL}$path" ;;
      other)  other_files="${other_files}${other_files:+$NL}$path" ;;
    esac
  done < <(git -C "$PROJECT_DIR" show --no-color --numstat -z --format='' "$sha" \
              -- . "$PATHSPEC_EXCLUDE" 2>/dev/null)

  net=$((ins_total - del_total))
  NET_LINES_WINDOW=$((NET_LINES_WINDOW + net))

  added_here="$(git -C "$PROJECT_DIR" show --no-color --diff-filter=A --name-only -z --format='' "$sha" \
                  -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | tr -cd '\0' | wc -c | tr -d ' ' || true)"
  added_here=${added_here:-0}
  NEW_FILES_WINDOW=$((NEW_FILES_WINDOW + added_here))

  # d1: plan freshness vs THIS commit (low-confidence mtime correlation).
  plan_present=false; plan_path=""
  if [ -n "$PLAN_PATHS" ] && [ -n "$date" ]; then
    commit_epoch="$(git -C "$PROJECT_DIR" show -s --format='%ct' "$sha" 2>/dev/null)"
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      p_epoch="$(date_mtime_epoch "$p")"
      [ -n "$p_epoch" ] || continue
      diff_h=$(( (commit_epoch > p_epoch ? commit_epoch - p_epoch : p_epoch - commit_epoch) / 3600 ))
      if [ "$diff_h" -le "$PLAN_FRESHNESS_HOURS" ]; then
        plan_present=true
        plan_path="${p#"$PROJECT_DIR"/}"
        break
      fi
    done <<< "$PLAN_PATHS"
  fi

  if [ "$has_source" -eq 1 ]; then
    COMMITS_WITH_SOURCE=$((COMMITS_WITH_SOURCE + 1))
    [ "$plan_present" = true ] && COMMITS_WITH_FRESH_PLAN=$((COMMITS_WITH_FRESH_PLAN + 1))
    [ "$has_test" -eq 1 ] && COMMITS_SOURCE_WITH_TESTS=$((COMMITS_SOURCE_WITH_TESTS + 1))

    while IFS= read -r sf; do
      [ -n "$sf" ] || continue
      found_in_plan=0
      if [ -n "$PLAN_PATHS" ]; then
        while IFS= read -r p; do
          [ -n "$p" ] || continue
          if grep -qF -- "$sf" "$p" 2>/dev/null; then found_in_plan=1; break; fi
        done <<< "$PLAN_PATHS"
      fi
      [ "$found_in_plan" -eq 0 ] && OUTSIDE_PLAN_FILES="${OUTSIDE_PLAN_FILES}${sf}
"
    done <<EOF
${src_files}
${test_files}
EOF
  fi

  # d5: commits_with_review — a review artifact dir exists (weak proxy).
  if [ "$REVIEWS_DIR_PRESENT" = true ]; then
    COMMITS_WITH_REVIEW=$((COMMITS_WITH_REVIEW + 1))
  fi

  # d7 weak signal: revert / fixup / amend subjects.
  case "$subject" in
    [Rr]evert*|*fixup!*|*squash!*|*amend*) REVERT_FIXUP_COUNT=$((REVERT_FIXUP_COUNT + 1)) ;;
  esac

  # d3 flags.
  large_batch=false
  { [ "$((ins_total + del_total))" -gt "$LARGE_BATCH_LINES" ] || [ "$file_count" -gt "$LARGE_BATCH_FILES" ]; } \
    && large_batch=true
  pure_fmt=false
  { [ "$((ins_total + del_total))" -gt 20 ] && [ "${net#-}" -le 2 ]; } && pure_fmt=true

  files_json="$(json_file_array source "$src_files")"
  tfiles_json="$(json_file_array test "$test_files")"
  dfiles_json="$(json_file_array doc "$doc_files")"
  ofiles_json="$(json_file_array other "$other_files")"

  commit_obj="$(printf '{"sha":%s,"date":%s,"subject":%s,"insertions":%d,"deletions":%d,"files_changed":%d,"source_files":%s,"test_files":%s,"doc_files":%s,"other_files":%s}' \
    "$(json_string "$sha")" "$(json_string "$date")" "$(json_string "$subject")" \
    "$ins_total" "$del_total" "$file_count" \
    "$files_json" "$tfiles_json" "$dfiles_json" "$ofiles_json")"
  append COMMITS_JSON "$commit_obj"

  d1_obj="$(printf '{"commit":%s,"plan_present":%s,"plan_path":%s}' \
    "$(json_string "$sha")" "$plan_present" "$(json_string "$plan_path")")"
  append D1_PER "$d1_obj"

  d3_obj="$(printf '{"commit":%s,"insertions":%d,"deletions":%d,"files_changed":%d,"large_batch_flag":%s,"pure_formatting_suspect":%s}' \
    "$(json_string "$sha")" "$ins_total" "$del_total" "$file_count" "$large_batch" "$pure_fmt")"
  append D3_PER "$d3_obj"

  d4_obj="$(printf '{"commit":%s,"net_lines_added":%d,"new_files_added":%d}' \
    "$(json_string "$sha")" "$net" "$added_here")"
  append D4_PER "$d4_obj"

  hits_for_commit="$(scan_hardstops "$sha")"
  if [ -n "$hits_for_commit" ]; then
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      append D6_HITS "$hit"
    done <<< "$hits_for_commit"
  fi
done <<< "$SHA_SORTED"

if [ "$COMMIT_COUNT" -gt 0 ] && [ "$RECOGNIZED_SOURCE_SEEN" -eq 0 ]; then
  add_degraded "no_recognized_source"
fi

plan_existed_pct=0
tests_pct=0
review_pct=0
if [ "$COMMITS_WITH_SOURCE" -gt 0 ]; then
  plan_existed_pct=$(( COMMITS_WITH_FRESH_PLAN * 100 / COMMITS_WITH_SOURCE ))
  tests_pct=$(( COMMITS_SOURCE_WITH_TESTS * 100 / COMMITS_WITH_SOURCE ))
fi
if [ "$COMMIT_COUNT" -gt 0 ]; then
  review_pct=$(( COMMITS_WITH_REVIEW * 100 / COMMIT_COUNT ))
fi

HOTSPOTS_JSON="$(
  if [ "${#HOTSPOT_FILES[@]}" -gt 0 ]; then
    printf '%s\n' "${HOTSPOT_FILES[@]}" | LC_ALL=C sort | uniq -c | sort -rn \
      | awk '{ c=$1; $1=""; sub(/^ /,""); path=$0;
               gsub(/\\/,"\\\\",path); gsub(/"/,"\\\"",path);
               printf "%s{\"file\":\"%s\",\"changes\":%d}", (NR>1?",":""), path, c }'
  fi
)"

OUTSIDE_JSON="$(
  printf '%s' "$OUTSIDE_PLAN_FILES" | grep . | LC_ALL=C sort -u \
    | awk '{ p=$0; gsub(/\\/,"\\\\",p); gsub(/"/,"\\\"",p);
             printf "%s\"%s\"", (NR>1?",":""), p }'
)"

# decisions changed in window — LoopSmith's decision registry (.sdlc/decisions.json).
DECISIONS_JSON="$(
  if [ "$COMMIT_COUNT" -gt 0 ]; then
    git -C "$PROJECT_DIR" log --no-merges --since="$SINCE_ARG" --name-only -z --format='' \
        -- '.sdlc/decisions.json' 2>/dev/null \
      | tr '\0' '\n' | grep . | LC_ALL=C sort -u \
      | awk '{ p=$0; gsub(/\\/,"\\\\",p); gsub(/"/,"\\\"",p);
               printf "%s\"%s\"", (NR>1?",":""), p }'
  fi
)"

# -- assemble output (stable key order) ----------------------------------------
{
printf '{"schema":%s,' "$(json_string "$SCHEMA")"

printf '"window":{"since_days":%d,' "$SINCE_DAYS"
printf '"oldest":{"sha":%s,"date":%s},' "$(json_string "$OLDEST_SHA")" "$(json_string "$OLDEST_DATE")"
printf '"newest":{"sha":%s,"date":%s},' "$(json_string "$NEWEST_SHA")" "$(json_string "$NEWEST_DATE")"
printf '"commit_count":%d},' "$COMMIT_COUNT"

printf '"degraded":%s,' "$(degraded_json)"

printf '"commits":[%s],' "$COMMITS_JSON"

printf '"dimensions":{'

printf '"d1":{"commits_with_source":%d,"commits_with_fresh_plan":%d,"plan_existed_pct":%d,"per_commit":[%s],"files_changed_outside_any_plan":[%s],"files_outside_plan_confidence":"low"},' \
  "$COMMITS_WITH_SOURCE" "$COMMITS_WITH_FRESH_PLAN" "$plan_existed_pct" "$D1_PER" "$OUTSIDE_JSON"

printf '"d2":{"tests_touched_with_source_pct":%d,"test_command_known":%s},' \
  "$tests_pct" "$TEST_COMMAND_KNOWN"

printf '"d3":{"per_commit":[%s],"churn_hotspots":[%s]},' "$D3_PER" "$HOTSPOTS_JSON"

printf '"d4":{"net_lines_added_window":%d,"new_files_added":%d,"per_commit":[%s]},' \
  "$NET_LINES_WINDOW" "$NEW_FILES_WINDOW" "$D4_PER"

printf '"d5":{"reviews_dir_present":%s,"commits_with_review_pct":%d},' \
  "$REVIEWS_DIR_PRESENT" "$review_pct"

printf '"d6":{"hits":[%s]},' "$D6_HITS"

printf '"d7":{"decisions_added":[%s],"repeated_revert_or_fixup_count":%d}' \
  "$DECISIONS_JSON" "$REVERT_FIXUP_COUNT"

printf '}}\n'
}

exit 0
