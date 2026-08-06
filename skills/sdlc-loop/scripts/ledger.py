#!/usr/bin/env python3
"""Team coordination ledger: an append-only record of what the loop actually did.

WHY PER-AUTHOR-PROCESS FILES. Every writer owns exactly one file
(`ledger/entries/<actor>-<pid>.jsonl`) and never touches anyone else's, so two people running
the loop against one repo cannot conflict on a write — the "team view" is the UNION of those
files, computed on read. The obvious alternative (one shared file everyone appends to) needs a
lock this kit does not have, and git would turn every concurrent append into a merge conflict.
The pid, not just the actor, is load-bearing: the actor resolves to the authenticated `gh`
login, and several parallel loops can share one login (F10) — without the pid, two such
loops would collide on the SAME file and could mint the same `id`.

WHAT IT IS FOR. The review queue answers "what stopped?" for one person on one machine
(and it is gitignored, so nobody else ever sees it). The ledger answers "what has the team
done, and what is waiting on whom?" — it is committed, it carries a timestamp and an actor
on every line, and a hand-off entry names the person who has to act.

Default OFF: with no `ledger` block in config.json every entry point is a no-op, so a repo
that has not opted in behaves exactly as it did before this module existed. Zero deps.
"""
import calendar
import json
import os
import pathlib

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    import sys as _sys  # doesn't garble to '?' or crash on a non-UTF-8 console (the Windows cp1252
    _sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # default); a stream without
    _sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # reconfigure is left as-is
except Exception:
    pass
import subprocess
import sys
import time

#: Every kind a ledger line may carry. Small on purpose — an open vocabulary would make the
#: team view unreadable within a week. `merged` records a PR the loop actually landed. `merge-armed`
#: (F26/#344) is the separate, honest kind for the moment `work.py merge` calls `gh pr merge --auto`:
#: that call only ARMS GitHub's auto-merge, it does not confirm the PR landed — a later-failing check
#: or a cancelled auto-merge can still mean it never does. Logging that moment as `merged` was a false
#: "landed" claim in TEAM.md's shared view. Nothing in this codebase watches for the real merge event
#: yet, so `merged` currently has no write site — it stays declared for when one does.
#: Sibling pin: tests/test_ledger.py::test_vocabulary_constants_match_spec_table (this file) and
#: insight/contract/vocabulary.json's "entries_kinds" -- issue #298. Update both by hand.
KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "note", "merged", "merge-armed")

#: Lifecycle of a hand-off, from the point of view of the person it is addressed TO.
STATES = ("open", "accepted", "deferred", "declined", "resolved")

#: Kinds that belong in the shared/team view even with no explicit addressee. `claimed` is shared so
#: the team view records WHO started a ticket and WHEN (it pairs with `done` to show start→finish);
#: `merged`/`merge-armed` are shared because a landed-or-armed PR is a team event; `note` stays
#: personal unless it names a `to`.
SHARED_KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "merged", "merge-armed")

#: Optional fields, all free-form except `state` (validated) — additive by design: an older
#: reader ignores a field it does not know rather than failing. `pr` carries a pull-request number.
OPTIONAL_FIELDS = ("area", "to", "issue", "priority", "why", "state", "ref", "pr")

ENTRIES, EVENTS = "entries", "events"
STREAMS = (ENTRIES, EVENTS)

#: The event-stream analogue of KINDS. Deliberately separate from KINDS/SHARED_KINDS
#: (untouched, per spec A.1) so the entries vocabulary a lead reads in TEAM.md can never
#: be diluted by adding a telemetry kind here.
#: Sibling pin: tests/test_ledger.py::test_vocabulary_constants_match_spec_table (this file) and
#: insight/contract/vocabulary.json's "event_kinds" -- issue #298. Update both by hand.
EVENT_KINDS = ("phase", "gate", "verify", "slice", "spend", "retro", "park", "scan")

#: Per-kind field whitelist for the events stream — the events-stream equivalent of
#: OPTIONAL_FIELDS. A closed list per kind, not one shared list, because event kinds do not
#: share a field namespace (e.g. `gate.verdict` vs `retro.grade`) the way entries kinds do.
EVENT_FIELDS = {
    "phase": ("phase", "state", "ms", "tokens_in", "tokens_out"),
    "gate": ("gate", "verdict", "cycle", "why"),
    "verify": ("ok", "exit", "ms", "command_sha256", "absent"),
    "slice": ("slice", "wave", "mode", "files_declared", "ms"),
    "spend": ("phase", "model", "tokens_in", "tokens_out", "cost_cents"),
    "retro": ("grade", "debt_count", "lessons_count"),
    "park": ("reason_class", "why"),
    "scan": ("category", "file", "count"),
}

#: Every EVENT_KINDS value must have a whitelist entry — append() indexes EVENT_FIELDS[kind]
#: directly (not .get(kind, ())) so a future EVENT_KINDS addition with no matching whitelist
#: entry raises immediately at import time instead of silently dropping every field it writes.
assert set(EVENT_KINDS) == set(EVENT_FIELDS), "EVENT_KINDS and EVENT_FIELDS have drifted apart"

#: #141: the closed set of EVENTS-stream fields that carry agent-/hook-authored free prose —
#: append() flatten+scrub+caps exactly these, for every kind, at the one chokepoint every write
#: path already funnels through. Nothing else in EVENT_FIELDS is prose: everything else is an
#: enum, a count, a duration, a bool, or (`verify.command_sha256`) a hash.
EVENT_FREE_TEXT_FIELDS = {
    "gate": ("why",),
    "park": ("why",),
    "spend": ("model",),
}

#: #141 amendment B: the completeness half of the guard below. A field NOT listed in
#: EVENT_FREE_TEXT_FIELDS is not automatically "safe" — the two look identical in Python (both are
#: plain `str`), so a type check alone can never catch a forgotten prose field. Every field of
#: every kind must be named HERE or above, on purpose, or the assertion below fails at import time
#: instead of a future story shipping an uncapped/unscrubbed/newline-tolerant field for years with
#: green tests.
#:
#: POST-REVIEW FIX: this used to be where the story stopped — a field named here was "safe", full
#: stop, no further check. An independent PR review BLOCKED #249 by proving that was never enough:
#: it proved every field LABELLED prose-or-not, but never that a "non-prose" label was actually
#: TRUE. `ms`/`tokens_in`/`tokens_out`/`cost_cents`/`cycle`/`debt_count`/`lessons_count` were plain
#: CLI strings with zero numeric validation anywhere on the write path, and `scan.file`/
#: `slice.slice` were "safe" purely BY CONVENTION (a filesystem path and a plan-authored id,
#: "neither expected to carry ... a secret shape" — an expectation, not a check). A payload with no
#: literal newline sailed through every one of those untouched. Every field named here now ALSO
#: carries a declared VALUE TYPE below (EVENT_NUMERIC_FIELDS / EVENT_BOOL_FIELDS /
#: EVENT_ENUM_FIELDS / EVENT_BOUNDED_ID_FIELDS), and `_assert_non_prose_fields_are_typed` (run at
#: import time, same as the guard below) fails closed if one is missing. `append()` enforces
#: numeric/bool at its chokepoint; `loop.py`'s `_validate_event` enforces the same thing again,
#: earlier, at the CLI, with a usable refusal instead of a silent sanitize.
EVENT_NON_PROSE_FIELDS = {
    "phase": ("phase", "state", "ms", "tokens_in", "tokens_out"),
    "gate": ("gate", "verdict", "cycle"),
    "verify": ("ok", "exit", "ms", "command_sha256", "absent"),
    "slice": ("slice", "wave", "mode", "files_declared", "ms"),
    "spend": ("phase", "tokens_in", "tokens_out", "cost_cents"),
    "retro": ("grade", "debt_count", "lessons_count"),
    "park": ("reason_class",),
    "scan": ("category", "file", "count"),
}

def _assert_event_fields_classified(event_kinds, event_fields, free_text_fields, non_prose_fields):
    """The completeness check itself, as a function rather than inlined at import time — so a test
    can call it directly against a deliberately-INCOMPLETE copy of the field maps (simulating a
    future story that adds a free-text field and forgets to classify it) and prove the guard
    actually fires, without needing to add a second real member to module-level EVENT_KINDS just
    to exercise the failure path. Called once below, at import time, against the real constants."""
    assert set(free_text_fields) <= set(event_kinds), \
        "EVENT_FREE_TEXT_FIELDS names a kind that isn't in EVENT_KINDS"
    assert set(non_prose_fields) <= set(event_kinds), \
        "EVENT_NON_PROSE_FIELDS names a kind that isn't in EVENT_KINDS"
    for kind in event_kinds:
        prose = set(free_text_fields.get(kind, ()))
        safe = set(non_prose_fields.get(kind, ()))
        assert not (prose & safe), \
            f"{kind!r}: a field cannot be both prose and declared-safe: {prose & safe}"
        real = set(event_fields[kind])
        assert prose <= real, f"EVENT_FREE_TEXT_FIELDS[{kind!r}] names a field not in EVENT_FIELDS"
        assert safe <= real, f"EVENT_NON_PROSE_FIELDS[{kind!r}] names a field not in EVENT_FIELDS"
        unclassified = real - (prose | safe)
        assert not unclassified, (
            f"EVENT_FIELDS[{kind!r}] has unclassified field(s) {unclassified} — declare each one in "
            "EVENT_FREE_TEXT_FIELDS (prose: flatten+scrub+cap) or EVENT_NON_PROSE_FIELDS (safe as-is); "
            "an unclassified field is a classification bug, not a safe default")


_assert_event_fields_classified(EVENT_KINDS, EVENT_FIELDS, EVENT_FREE_TEXT_FIELDS, EVENT_NON_PROSE_FIELDS)

#: POST-REVIEW FIX (the four buckets EVENT_NON_PROSE_FIELDS now has to account for). Every field
#: named in EVENT_NON_PROSE_FIELDS above must land in EXACTLY ONE of these — the completeness half
#: of `_assert_non_prose_fields_are_typed` below fails at import time if a field is in neither, and
#: the mutual-exclusion half fails if a field is claimed by two.
#:
#:   - numeric: must parse as a whole number (`_looks_numeric`). Enforced twice: `loop.py`'s
#:     `_validate_event` refuses a bad value at the CLI (exit 2, nothing written); `append()`
#:     sanitizes (never raises) for every other call site.
#:   - bool: must be an actual bool or a recognised spelling (`_looks_bool`). Same two enforcement
#:     points as numeric.
#:   - enum: already vocabulary-checked elsewhere — PHASE_KINDS/GATE_KINDS/VERDICTS/RETRO_GRADES in
#:     `loop.py`'s `_validate_event` for the fields an agent can type; `category`/`mode` are
#:     produced only by deterministic code (discovery-scan's fixed categories, `slices.dispatch`'s
#:     two dispatch modes), never agent-typed, so there is nothing to enforce at a CLI they never
#:     reach. Written as-is, same as before this story.
#:   - bounded_id: `slice`/`file`/`command_sha256` — ids and short paths, not prose, but no longer
#:     "safe by convention" either. Capped SHORT (BOUNDED_ID_CAP, not FREE_TEXT_CAP) and scrubbed,
#:     the same treatment as prose, closing the `slice.slice` gap the review flagged (an agent-
#:     authored plan `id` landing verbatim with no length/shape check at all).
EVENT_NUMERIC_FIELDS = {
    "phase": ("ms", "tokens_in", "tokens_out"),
    "gate": ("cycle",),
    "verify": ("exit", "ms"),
    "slice": ("wave", "files_declared", "ms"),
    "spend": ("tokens_in", "tokens_out", "cost_cents"),
    "retro": ("debt_count", "lessons_count"),
    "scan": ("count",),
}

EVENT_BOOL_FIELDS = {
    "verify": ("ok", "absent"),
}

EVENT_ENUM_FIELDS = {
    "phase": ("phase", "state"),
    "gate": ("gate", "verdict"),
    "slice": ("mode",),
    "spend": ("phase",),
    "retro": ("grade",),
    "park": ("reason_class",),
    "scan": ("category",),
}

EVENT_BOUNDED_ID_FIELDS = {
    "verify": ("command_sha256",),
    "slice": ("slice",),
    "scan": ("file",),
}


def _assert_non_prose_fields_are_typed(event_kinds, non_prose_fields, type_maps):
    """The TYPE half of the guard, layered on top of `_assert_event_fields_classified` above. That
    guard already proves every field is LABELLED prose-or-not; it never proves a "non-prose" label
    is TRUE — a reviewer demonstrated by execution that `tokens_in`/`cycle`/`debt_count` (all
    "safe" by that label) were plain CLI strings with zero shape enforcement. This confirms every
    field named in `non_prose_fields` also has exactly one declared VALUE TYPE, and fails at IMPORT
    TIME — not years later in production — the moment a new field ships with a label but no type.

    `type_maps` is `{type_name: {kind: (field, ...)}}`; called once below, at import time, against
    the real constants, and directly by tests against a deliberately-broken copy to prove the
    failure path fires (mirroring `_assert_event_fields_classified`'s own test-injection shape)."""
    for type_name, type_map in type_maps.items():
        assert set(type_map) <= set(event_kinds), \
            f"{type_name!r} type map names a kind that isn't in EVENT_KINDS"
    for kind in event_kinds:
        safe = set(non_prose_fields.get(kind, ()))
        owner = {}
        for type_name, type_map in type_maps.items():
            fields = set(type_map.get(kind, ()))
            assert fields <= safe, (
                f"{type_name!r}[{kind!r}] names field(s) {fields - safe} not declared non-prose "
                f"for {kind!r} in EVENT_NON_PROSE_FIELDS")
            for field in fields:
                assert field not in owner, (
                    f"{kind!r}.{field!r} is classified as both {owner[field]!r} and {type_name!r} "
                    "— a field must have exactly one VALUE TYPE")
                owner[field] = type_name
        untyped = safe - set(owner)
        assert not untyped, (
            f"EVENT_NON_PROSE_FIELDS[{kind!r}] has field(s) {untyped} with no declared VALUE TYPE — "
            "add each to EVENT_NUMERIC_FIELDS, EVENT_BOOL_FIELDS, EVENT_ENUM_FIELDS, or "
            "EVENT_BOUNDED_ID_FIELDS; an untyped field is a classification bug, not a safe default")


_assert_non_prose_fields_are_typed(EVENT_KINDS, EVENT_NON_PROSE_FIELDS, {
    "numeric": EVENT_NUMERIC_FIELDS, "bool": EVENT_BOOL_FIELDS,
    "enum": EVENT_ENUM_FIELDS, "bounded_id": EVENT_BOUNDED_ID_FIELDS,
})

#: #141: the length every declared prose field is capped to, after flatten+scrub.
FREE_TEXT_CAP = 200

#: POST-REVIEW FIX: the length a declared bounded-identifier field (slice/file/command_sha256) is
#: capped to, after flatten+scrub — deliberately shorter than FREE_TEXT_CAP. These are ids and
#: short paths, not prose: a legitimate `slice` id, a repo-relative file path, or a sha256 hex
#: digest (64 chars) is always well under 120; anything longer is already suspicious.
BOUNDED_ID_CAP = 120

#: Digits a numeric field may carry. A syntactic "parses as an int" check is NOT a safety check:
#: an arbitrary secret base-10-encoded as one integer parses cleanly, so without a bound it would
#: skip the scrubber and both caps and land raw, recoverable byte-for-byte with int(v).to_bytes().
#: 20 digits clears a signed 64-bit int, so every real duration, token count, cost and cycle fits
#: with room to spare.
#:
#: WHAT THIS DOES NOT CLOSE, stated plainly because an earlier version of this comment claimed it
#: did and that was false. A per-field bound does nothing against a payload SPLIT ACROSS fields: a
#: `phase` event carries three numeric fields, so ~24 bytes — enough for a whole AWS key — round-
#: trips in one legitimate `emit` call, and successive events cost nothing. That channel is
#: inherent to "a bounded numeric field trusts its own digits" and no cap can remove it.
#:
#: The accepted risk, stated at its real size. A first version of this note argued the gap was
#: cheap to accept because an agent that wants to exfiltrate can write
#: `.sdlc/ledger/events/<actor>.jsonl` directly with a shell redirect anyway, so closing the CLI
#: channel buys nothing. Review showed that reasoning is wrong, and the correction matters more
#: than the mechanics: it assumes the blast radius is the writer's own machine. It is not. The
#: events stream is PUSHED to the shared `sdlc-ledger` ops branch by the documented default path
#: (`sync.py bootstrap` — "pushes it so the team can see it"), and `insight`'s manager dashboard
#: reads it team-wide. There is currently no config that keeps it local: doctor reports that even
#: `share: false` "still publishes with the ledger, same as share: true", because the write side of
#: that flag is unimplemented (#244).
#:
#: So the honest statement is: a chunked secret entered through the SANCTIONED `loop.py emit` CLI
#: — the exact command an agent is told to run — reaches a shared team branch and a manager
#: dashboard whose readers trust it as sanitised. That is a different threat class from #140's
#: allowlist precedent, which is about an agent lying to ITSELF on one machine. A raw shell
#: redirect is unstoppable from in-process and genuinely out of scope; this is not that.
#:
#: It is still not fixed HERE, because no per-field bound can fix it — a `phase` event has three
#: numeric fields and successive events are free — and tightening the cap to a few digits would
#: break real values (a 27-hour `ms`, a seven-figure token count) while leaving the channel open.
#: The real mitigations are elsewhere and owned: #244 makes `share: false` actually keep events
#: local, and #248 covers whether ingested data can be trusted at all. Recorded on #141 so it is a
#: known accepted risk with the right size attached, not an inherited "local, so low-stakes".
NUMERIC_DIGIT_CAP = 20

#: Controlled vocabularies from spec §A.3. PHASE_KINDS/GATE_KINDS/REASON_CLASSES exist for
#: downstream consumers (ingest, docs, future validation) but are NOT enforced by append() in
#: #136 — see the "unknown phase/gate/reason_class" decision in the append() docstring.
#: VERDICTS IS enforced (issue requires it).
#: Sibling pin (all four below): tests/test_ledger.py::test_vocabulary_constants_match_spec_table
#: (this file) and insight/contract/vocabulary.json's "phase_kinds"/"gate_kinds"/"verdicts"/
#: "reason_classes" -- issue #298. Update both sides by hand.
PHASE_KINDS = ("goal", "research", "plan", "plan_review", "implement", "review", "retro")
GATE_KINDS = ("plan_review", "code_review", "post_review", "merge", "decision", "alignment",
              "verify", "risk_security", "risk_contract", "risk_migration", "risk_release",
              "risk_debug")
VERDICTS = ("pass", "block", "warn", "absent")
REASON_CLASSES = ("irreversible", "needs_decision", "merge_conflict", "failing_check",
                   "no_evidence", "dependency", "review_cap", "budget", "unknown")

#: #140: spec §A.3's `retro.grade` vocabulary, mirrored from `sdlc-retro/SKILL.md` §3's
#: achieved/partial/diverged bullets — it had no Python home before this. Like
#: PHASE_KINDS/GATE_KINDS above, documented but deliberately NOT enforced by append() itself;
#: `loop.py emit` is what validates a `retro` event's `--grade` value against it.
#: Sibling pin: tests/test_ledger.py::test_retro_grades_matches_sdlc_retro_skill_prose (this
#: file) and insight/contract/vocabulary.json's "retro_grades" -- issue #298. Update both by hand.
RETRO_GRADES = ("achieved", "partial", "diverged")

_ACTOR_CACHE = {}


# --------------------------------------------------------------------------- config


def _config(sdlc_dir):
    """Read config.json. Deliberately a 1-line duplicate of state.load_config rather than an
    import: this module stays usable (and testable) without pulling the run-state layer in."""
    return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())


def settings(config):
    return (config or {}).get("ledger") or {}


def enabled(config):
    """Strict `is True` — a truthy string or a stray 1 does not silently switch a team
    coordination surface on."""
    return settings(config).get("enabled") is True


#: a crashed claimer's lock auto-expires; goals live hours, not days. Was loop.py-local
#: (`_DEFAULT_LEASE_TTL_HOURS`) until `work.py start()` (F10.5/#374) also needed it — moved here,
#: the shared home for lease/claim logic, rather than duplicated a second time.
DEFAULT_LEASE_TTL_HOURS = 12


def lease_ttl_seconds(config):
    """`ledger.lease.ttl_hours` (default `DEFAULT_LEASE_TTL_HOURS`) as seconds for
    `open_claims`/`open_claims_detailed`'s `ttl_seconds`, or None for "never expire" (config
    `0`/`false`). One place to read this setting so `loop.py`'s `_lease()` and `work.py`'s
    resume-safety guard can never drift on what "expired" means."""
    hours = (settings(config).get("lease") or {}).get("ttl_hours", DEFAULT_LEASE_TTL_HOURS)
    return float(hours) * 3600 if hours else None


def telemetry_settings(config):
    return (config or {}).get("telemetry") or {}


def telemetry_enabled(config):
    """Strict `is True`, mirroring enabled() — a truthy string or a stray 1 must not switch a
    second write surface on silently."""
    return telemetry_settings(config).get("enabled") is True


# --------------------------------------------------------------------------- paths


def ledger_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "ledger"


def entries_dir(sdlc_dir, stream=ENTRIES):
    if stream not in STREAMS:
        raise ValueError(f"unknown ledger stream {stream!r} (expected one of {', '.join(STREAMS)})")
    return ledger_dir(sdlc_dir) / stream


def entry_file(sdlc_dir, who, stream=ENTRIES):
    """F10: the filename carries the WRITING PROCESS, not just the actor. The per-author-file design
    assumed one writer per file, but the actor resolves to the authenticated `gh` login — and several
    parallel loops under a single shared account (all loops authenticate as the same user) all
    resolve to the SAME `who`. Two concurrent appends to one file then read the same `_line_count`
    and mint the same `id` (`shared:1 == shared:1`), breaking the monotonic-per-author guarantee the
    watcher-resume cursor + `open_claims` lease rely on. The pid makes concurrent writers land in
    different files instead — `read_all()`/`render()` already union EVERY `*.jsonl` under the stream
    dir and attribute by the `actor` FIELD inside each entry (never the filename), so THOSE needed no
    change. `sync.py`'s `publish()`/`bootstrap()` are the exception: they name a specific file to
    stage rather than reading via `read_all()`, so they go through `files_for()` below to find every
    file a given actor (any pid) has written."""
    return entries_dir(sdlc_dir, stream) / f"{_safe_name(who)}-{os.getpid()}.jsonl"


def files_for(directory, who):
    """Every file `who` has ever written into a ledger stream directory (an `entries_dir()`/
    `events_dir()` result) — one per writing process (see `entry_file()`), plus a pre-#337 bare
    `<who>.jsonl` if one is still sitting there unpublished from before this plugin version.
    `sync.py`'s `publish()`/`bootstrap()` need this: unlike `read_all()`, which safely unions every
    `*.jsonl` in the directory, they must name exactly which files are (or might be) `who`'s to
    stage — never a teammate's.

    Matches the safe name exactly, never as a prefix: a naive `<safe>-*` glob would also match a
    DIFFERENT actor whose safe name happens to start with `<safe>-` (e.g. "team" matching
    "team-bot"'s files), so each candidate is split on its trailing `-<pid>` and the remainder is
    compared for equality first."""
    d = pathlib.Path(directory)
    if not d.is_dir():
        return []
    safe = _safe_name(who)
    out = []
    for p in d.glob(f"{safe}-*.jsonl"):
        name, _, pid = p.stem.rpartition("-")
        if name == safe and pid.isdigit():
            out.append(p)
    legacy = d / f"{safe}.jsonl"
    if legacy.exists():
        out.append(legacy)
    return sorted(out)


def _safe_name(who):
    """A GitHub login can contain a hyphen but never a path separator; be defensive anyway so
    a bad config value can never write outside the entries directory."""
    keep = [c for c in str(who) if c.isalnum() or c in "-_."]
    return ("".join(keep).strip(".-_") or "unknown")[:64]


# --------------------------------------------------------------------------- actor


def _run_gh(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def actor(config, run=None):
    """Who is writing. Config wins; else the authenticated account; else the shell user.
    Never raises and never blocks — an unresolvable actor writes as `unknown` rather than
    losing the entry."""
    configured = (settings(config).get("actor") or "").strip()
    if configured:
        return _safe_name(configured)
    key = id(run) if run else "default"
    if key in _ACTOR_CACHE:
        return _ACTOR_CACHE[key]
    resolved = ""
    try:
        resolved = (run or _run_gh)(["api", "user", "-q", ".login"])
    except Exception:
        resolved = ""
    resolved = resolved or os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    _ACTOR_CACHE[key] = _safe_name(resolved)
    return _ACTOR_CACHE[key]


def reset_actor_cache():
    """Tests (and a long-lived process whose auth changed) need to re-resolve."""
    _ACTOR_CACHE.clear()


# --------------------------------------------------------------------------- free-text sanitizing (#141)

_SCRUB_MODULE = None
_SCRUB_LOAD_ATTEMPTED = False


def _scrub_module():
    """Lazily importlib-load `hooks/research_capture.py` so `ledger.py` reuses its `_scrub`/
    `_SECRET_PATTERNS` — the one scrubber in the repo — instead of duplicating the pattern table
    (two tables is exactly the drift this repo has already been bitten by). This is the REVERSE
    direction of `hooks/decision_gate.py`'s own cross-load (that file loads THIS module the other
    way, at its `_emit_decision_event`): both are the same `importlib.util.spec_from_file_location`
    sibling-load idiom, and both are invisible to `tests/test_import_boundary.py`'s ast-based guard
    (that checker only parses real `ast.Import`/`ast.ImportFrom` nodes; `spec_from_file_location`
    produces neither — confirmed against the guard's own module docstring).

    Cached after the FIRST attempt, success or failure — one load per process, not one per call.
    Fails open to `None` (never raises) if `hooks/research_capture.py` is missing or broken in some
    checkout; the caller then treats scrub as a no-op and degrades to flatten+cap only. That
    degrade is a real privacy reduction on its own (flatten/cap are plain string ops right here in
    `ledger.py` and never touch this loader, so the worst case becomes "<=200 chars, single line,
    unredacted" instead of unbounded raw text) — but it must never be SILENT: one line to stderr,
    the same idiom `safe_append` already uses for its own non-fatal failures (see that function's
    "ledger: entry skipped (non-fatal): ..." message), so a broken install is visible in the log
    even though no run ever breaks because of it."""
    global _SCRUB_MODULE, _SCRUB_LOAD_ATTEMPTED
    if _SCRUB_LOAD_ATTEMPTED:
        return _SCRUB_MODULE
    _SCRUB_LOAD_ATTEMPTED = True
    try:
        import importlib.util
        # ledger.py lives at skills/sdlc-loop/scripts/ledger.py; hooks/ is a sibling of skills/ at
        # the repo root — four `.parent`s up from this file, matching decision_gate.py's own
        # two-`.parent`s-up-then-down-into-skills/sdlc-loop/scripts symmetry in the other direction.
        repo_root = pathlib.Path(__file__).resolve().parent.parent.parent.parent
        path = repo_root / "hooks" / "research_capture.py"
        spec = importlib.util.spec_from_file_location("research_capture", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SCRUB_MODULE = mod
    except Exception as exc:                                    # noqa: BLE001 - fail-open by design
        print(f"ledger: scrub unavailable, degrading to flatten+cap only (non-fatal): {exc}",
              file=sys.stderr)
        _SCRUB_MODULE = None
    return _SCRUB_MODULE


def _sanitize_free_text(value, cap=FREE_TEXT_CAP):
    """flatten -> scrub -> cap, IN THAT EXACT ORDER — never reorder this. An independent review
    proved by execution that a secret whose match starts near char 195 of a 215-char string is
    fully redacted when scrub runs BEFORE the 200-char cap, but capping first truncates the match
    mid-pattern and leaves an unredacted FRAGMENT (e.g. the literal `AKIAI...`) inside the capped
    text, because the scrubber never gets to see the whole shape once it's been cut off. Flattening
    first (not last) matters too: it turns a raw multi-line block into one line before either the
    scrubber or the cap sees it, so a cap can never land mid-multi-line-secret in a way flatten
    would otherwise have prevented.

    `cap` defaults to FREE_TEXT_CAP (declared prose). POST-REVIEW FIX: the bounded-identifier and
    the numeric/bool-fallback call sites in `append()` below pass `cap=BOUNDED_ID_CAP` — same
    function, same order, just a shorter ceiling for a value that's an id/path/miscoerced-field
    rather than prose.

    Never raises on its own: a missing/broken scrub module (see `_scrub_module`) degrades to
    flatten+cap only, and a scrub call that itself raises is caught here too — this function is
    called from inside `append()`'s field-writing loop, which must stay exception-safe for the
    fail-open call sites (a hook's `deny`, an autonomous park) that route through it. `str(value)`
    never raises regardless of what lands here — a dict, an int, bytes, `None` all stringify
    cleanly, which is exactly why sanitizing (rather than coercing) is the safe fallback for a
    declared-numeric/bool field that turns out not to hold one."""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    mod = _scrub_module()
    if mod is not None:
        try:
            text = mod._scrub(text)
        except Exception as exc:                                # noqa: BLE001 - fail-open by design
            print(f"ledger: scrub failed, degrading to flatten+cap only (non-fatal): {exc}",
                  file=sys.stderr)
    return text[:cap]


_BOOL_SPELLINGS = {"true", "false", "1", "0", "yes", "no"}


def _looks_numeric(value):
    """True when `value` parses cleanly as a whole number — an int (or bool, its subclass) as-is,
    or a string shaped like one. Shared by `append()`'s chokepoint sanitizer below AND `loop.py`'s
    `_validate_event` CLI refusal (which reaches in directly — the same cross-module idiom
    `_scrub_module` above already uses in the other direction, importing `hooks/research_capture`'s
    `_scrub`) so a numeric field's definition of "valid" can never drift between the two
    enforcement points. Never raises.

    NUMERIC_DIGIT_CAP is the whole point of the second condition, not belt-and-braces. Parsing as
    an int says nothing about what the digits ENCODE: base-10-encoding a secret as one giant
    integer — `str(int.from_bytes(b"AKIA...|ghp_...", "big"))`, 118 digits — passes a purely
    syntactic check, so it skips the scrubber and the cap and lands raw, and `int(v).to_bytes()`
    reads it straight back off disk. A digit cap closes that channel's bandwidth below anything
    useful, and closes the plain unbounded-length hole (50k digits written verbatim) in the same
    stroke. 20 digits clears a signed 64-bit int, so no real ms / tokens / cost / count / cycle
    value is anywhere near it."""
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return len(str(value).strip().lstrip("+-").replace("_", "")) <= NUMERIC_DIGIT_CAP


def _looks_bool(value):
    """True for an actual bool, or a string spelled like one (`_BOOL_SPELLINGS`, case-insensitive).
    Same sharing rationale as `_looks_numeric`. Never raises."""
    if isinstance(value, bool):
        return True
    return isinstance(value, str) and value.strip().lower() in _BOOL_SPELLINGS


def reject_newline(value, label):
    """The hard-reject-a-raw-newline rule shared by every synchronous, agent-typed CLI verb that
    must REFUSE (not merely flatten) a multi-line value: `loop.py`'s `_validate_event` and
    `work.py`'s `main()` post-review branch hand-wrote this identically twice before this — the
    same "guard duplicated at call sites instead of chokepointed" shape #141's whole story is
    about. A third CLI verb now has one shared place to call instead of a third drifting copy.

    `append()` itself must NEVER call this: it flattens instead of rejecting (see
    `_sanitize_free_text`), because it also sits behind the three deterministic, fail-open call
    sites (a hook's `deny`, an autonomous park) that must never raise on bad input.

    Returns the refusal message (already naming `label`), or `None` when `value` has no raw
    newline. Never raises — `str(value)` handles anything."""
    if "\n" in str(value):
        return f"newline not allowed in {label} (the events stream is single-line only)"
    return None


# --------------------------------------------------------------------------- write


def append(sdlc_dir, config, kind, goal, run=None, now=None, stream=ENTRIES, **fields):
    """Append one line to THIS actor's file on the given stream. Returns the entry, or None
    when the ledger is off.

    `id` is `<actor>:<seq>` where seq counts this file's existing lines — monotonic per
    `(actor, stream)`, which is exactly what a watcher needs for a resume cursor, and needs no
    shared counter.

    `stream` defaults to `ENTRIES` (the pre-#136 behavior, byte-for-byte). `EVENTS` is a
    separate closed vocabulary (`EVENT_KINDS`/`EVENT_FIELDS`) written to its own per-actor file,
    so it can never collide with or dilute the entries kinds a lead reads in TEAM.md.

    Unknown `phase`/`gate`/`reason_class` VALUE does not raise — only `kind` (both streams) and
    `verdict` (events/`gate` only) are enforced, matching the issue's Done criteria verbatim.
    `PHASE_KINDS`/`GATE_KINDS`/`REASON_CLASSES` are the documented vocabulary but tightening
    them to enforced-at-write is a scope expansion later work can add, the same way `STATES`
    already does for entries, without another signature change.

    THE EVENTS GATE IS `enabled(config) AND telemetry_enabled(config)` — BOTH, not either. This
    looks like it should be an OR against spec Section A.2's promise that telemetry keeps writing
    locally even with the ledger off entirely, and someone will be tempted to "fix" it. Don't:
    `append()` does `path.parent.mkdir(parents=True, ...)` below and needs no ledger worktree to
    write, so an OR/telemetry-alone gate would happily create `.sdlc/ledger/events/<actor>.jsonl`
    on a repo where `.sdlc/ledger/` is a git WORKTREE that is only reliably gitignored once
    `/sdlc-ledger`'s `ensure_ignore()` has run (`doctor.py`'s own `_ignore_mechanism()` reports
    "NOT ignored" as a live state) — i.e. it would risk leaking event files into a commit. Spec
    A.2's independence promise needs #244's real `.sdlc/events/` local path first; that is a
    sequencing dependency, not something this gate expression can fix. And on the OTHER side, an
    OR would make every repo that already ships with `ledger.enabled: true` and no `telemetry`
    block (the overwhelmingly common shape once callers land) start writing an events stream with
    zero opt-in — directly contradicting the `_comment` shipped alongside the `telemetry` block.
    So: strict `is True` on both, matching `enabled()`'s own idiom. ENTRIES stays gated on
    `enabled(config)` alone, unchanged."""
    if stream not in STREAMS:
        raise ValueError(f"unknown ledger stream {stream!r} (expected one of {', '.join(STREAMS)})")

    if stream == ENTRIES:
        if not enabled(config):
            return None
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r} (expected one of {', '.join(KINDS)})")
        state = fields.get("state")
        if state is not None and state not in STATES:
            raise ValueError(f"unknown ledger state {state!r} (expected one of {', '.join(STATES)})")
        field_whitelist = OPTIONAL_FIELDS
    else:  # EVENTS
        if not (enabled(config) and telemetry_enabled(config)):
            return None
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r} (expected one of {', '.join(EVENT_KINDS)})")
        if kind == "gate":
            verdict = fields.get("verdict")
            if verdict is not None and verdict not in VERDICTS:
                raise ValueError(f"unknown event verdict {verdict!r} (expected one of {', '.join(VERDICTS)})")
        if kind == "phase":
            state = fields.get("state")
            if state is not None and state not in ("start", "end"):
                raise ValueError(f"unknown phase state {state!r} (expected start or end)")
        field_whitelist = EVENT_FIELDS[kind]

    who = actor(config, run)
    path = entry_file(sdlc_dir, who, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = _line_count(path) + 1

    entry = {
        # F10: the pid rides in `id` too, not just the filename — `watch_classify.py`'s cursor
        # tracks a per-WRITER (not per-actor) high-water seq, and needs the pid to key on. Every
        # existing `id` consumer (`_seq()` here and in watch_classify.py, and the 3 call sites that
        # just print `entry["id"]` verbatim) takes the LAST `:`-segment or the whole string, so a
        # 3-part id is a no-op for all of them — confirmed by reading every "id" reference in the
        # plugin.
        "id": f"{who}:{os.getpid()}:{seq}",
        "ts": _stamp(now),
        "actor": who,
        "kind": kind,
        "goal": str(goal),
    }
    for name in field_whitelist:
        value = fields.get(name)
        if value not in (None, ""):
            # #141: cap+scrub lives HERE, once, for every caller — never at a call site. #141
            # scoped it to stream == EVENTS only, on the theory the ENTRIES stream's own `why`
            # (hand-offs/notes a lead reads in TEAM.md) was out of that story's scope. It wasn't
            # safe to leave unscrubbed: `why` is committed byte-for-byte to the shared `sdlc-ledger`
            # branch AND rendered into TEAM.md, so a `handoff.py open ... --why "<secret>"` (a
            # sanctioned command) lands a secret/client string in version control. Same
            # flatten->scrub->cap treatment as EVENTS' free-text fields, gated on the one ENTRIES
            # field that is actually prose — see OPTIONAL_FIELDS: `area`/`to`/`issue`/`priority`/
            # `state`/`ref`/`pr` are all short enums/ids, never free text.
            if stream == ENTRIES and name == "why":
                value = _sanitize_free_text(value)
            elif stream == EVENTS:
                if name in EVENT_FREE_TEXT_FIELDS.get(kind, ()):
                    value = _sanitize_free_text(value)
                elif name in EVENT_BOUNDED_ID_FIELDS.get(kind, ()):
                    # POST-REVIEW FIX: `slice`/`file`/`command_sha256` were "safe by convention"
                    # only — an agent-authored plan `id` (`slice.slice`) landed verbatim with no
                    # length/shape check at all. Same treatment as prose, just a SHORTER cap
                    # (BOUNDED_ID_CAP, not FREE_TEXT_CAP): these are ids/paths, not prose, and a
                    # legitimate one is always short — closes the gap with enforcement, not habit.
                    value = _sanitize_free_text(value, cap=BOUNDED_ID_CAP)
                elif name in EVENT_ENUM_FIELDS.get(kind, ()):
                    # POST-REVIEW FIX (round 3): the enum bucket had NO enforcement here at all.
                    # `gate.verdict` and `phase.state` raise below, and `loop.py`'s `_validate_event`
                    # vocabulary-checks the agent-typed ones — but that is a CLI-only helper, so a
                    # direct `append()` call could put 175 chars of secret-laden prose into
                    # `scan.category` / `slice.mode` / `retro.grade` / `park.reason_class` /
                    # `phase.phase` and have it written raw. No shipped caller does (they all pass
                    # hard-coded constants), which is exactly the "safe by convention, one level
                    # further out" pattern that caused the two earlier blocks. A vocabulary check
                    # here would reverse #136's deliberate decision to leave phase/gate/reason_class
                    # unvalidated, so this is the floor instead: an enum can never carry prose,
                    # whatever a future caller passes.
                    value = _sanitize_free_text(value, cap=BOUNDED_ID_CAP)
                elif name in EVENT_NUMERIC_FIELDS.get(kind, ()) and _looks_numeric(value):
                    # POST-REVIEW FIX (round 3): the predicate normalised the string
                    # (`.replace("_", "")`) but the RAW value was written, so
                    # "1_2_3_4_5_6_7_8_9_0_1_2_3_4_5_6_7_8_9_0" passed a 20-digit check and landed
                    # at 39 characters. Re-stringify through int() so what is stored is what was
                    # checked — which also normalises leading zeros, padding and sign whitespace.
                    # Same shape of bug as the two before it: a predicate applied to one
                    # representation, enforcement applied to another.
                    if not isinstance(value, bool):
                        value = type(value)(int(value)) if isinstance(value, int) else str(int(value))
                elif name in EVENT_NUMERIC_FIELDS.get(kind, ()):
                    # POST-REVIEW FIX (THE LEAK): a declared-numeric field that does not actually
                    # hold a number must never be written raw — this is the reviewer's exact repro
                    # (`tokens_in`/`cycle`/`debt_count` as a secret-bearing string with no literal
                    # newline, sailing through untouched because nothing validated the VALUE, only
                    # the field's presence in a "safe" list). `append()` must NEVER raise for this
                    # — the three deterministic fail-open call sites (a hook's `deny`, an
                    # autonomous park) depend on it — so sanitize (scrub+cap) rather than reject:
                    # it can neither leak the value nor lose the event. `loop.py`'s
                    # `_validate_event` enforces the same rule again, earlier, at the CLI, where a
                    # loud refusal is possible and safe.
                    #
                    # The cap here is NUMERIC_DIGIT_CAP, not FREE_TEXT_CAP, and that is the second
                    # half of the fix. Scrubbing does not touch a digit string, so a base-10-encoded
                    # secret sanitized at the 200-char prose cap still survived — 118 digits is ~49
                    # recoverable bytes. A field declared numeric can never legitimately need more
                    # room than a number, so it does not get prose's allowance.
                    value = _sanitize_free_text(value, cap=NUMERIC_DIGIT_CAP)
                elif name in EVENT_BOOL_FIELDS.get(kind, ()) and not _looks_bool(value):
                    # Same rationale as the numeric branch, for the (currently CLI-unreachable,
                    # but append()-reachable) bool fields `verify.ok` / `verify.absent`. A bool
                    # needs even less room than a number, so it borrows the same tight cap.
                    value = _sanitize_free_text(value, cap=NUMERIC_DIGIT_CAP)
            entry[name] = value
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def safe_append(sdlc_dir, kind, goal, config=None, **fields):
    """The form the loop calls. A ledger problem must never break a run, so everything —
    including reading config.json — is inside the guard."""
    try:
        cfg = config if config is not None else _config(sdlc_dir)
        return append(sdlc_dir, cfg, kind, goal, **fields)
    except Exception as exc:                                    # noqa: BLE001 - fail-open by design
        print(f"ledger: entry skipped (non-fatal): {exc}", file=sys.stderr)
        return None


def _line_count(path):
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


# --------------------------------------------------------------------------- read


def read_all(sdlc_dir, stream=ENTRIES):
    """The team view: the union of every author's file on the given stream, oldest first.
    Reads one stream, `entries` by default. A malformed line is skipped, never fatal — one
    bad append must not blind the whole team."""
    out = []
    base = entries_dir(sdlc_dir, stream)
    if not base.exists():
        return out
    for path in sorted(base.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("kind"):
                out.append(item)
    out.sort(key=lambda e: (e.get("ts", ""), e.get("actor", ""), _seq(e)))
    return out


def _seq(entry):
    ident = str(entry.get("id", ""))
    tail = ident.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def team(entries):
    """Everything the whole team should see: anything addressed to someone, plus outcomes."""
    return [e for e in entries if e.get("to") or e.get("kind") in SHARED_KINDS]


def addressed_to(entries, who):
    return [e for e in entries if e.get("to") == who]


def handoff_key(entry):
    """What pairs a hand-off with its answers. The issue when there is one, else the goal — a local
    backlog has no issue numbers but still needs the two halves to find each other."""
    return str(entry.get("issue") or entry.get("goal"))


def handoff_states(entries):
    """{key: the latest ack state}, `open` where nobody has answered.

    A lead has to tell "nobody has even looked at this" from "someone took it and is working on it".
    Both are outstanding — the blocker is still real until it is `resolved` — but only the first one
    needs chasing, and a bare count cannot say which is which."""
    latest = {}
    for entry in entries:
        if entry.get("kind") == "ack" and entry.get("state"):
            latest[handoff_key(entry)] = entry["state"]
    return latest


def unanswered(entries):
    """Outstanding hand-offs nobody has replied to at all — the ones that are actually stuck."""
    states = handoff_states(entries)
    return [e for e in outstanding(entries) if handoff_key(e) not in states]


def outstanding(entries):
    """Hand-offs nobody has closed out. A hand-off is settled once an `ack` for the same issue
    reaches a terminal state; `deferred` deliberately stays outstanding — a promise to look
    later is not a resolution."""
    settled = set()
    for entry in entries:
        if entry.get("kind") == "ack" and entry.get("state") in ("declined", "resolved"):
            key = entry.get("issue") or entry.get("goal")
            if key is not None:
                settled.add(str(key))
    open_ones = []
    for entry in entries:
        if entry.get("kind") != "handoff":
            continue
        key = str(entry.get("issue") or entry.get("goal"))
        if key not in settled:
            open_ones.append(entry)
    return open_ones


def counts(entries):
    out = {kind: 0 for kind in KINDS}
    for entry in entries:
        if entry.get("kind") in out:
            out[entry["kind"]] += 1
    return out


# --------------------------------------------------------------------------- claim lease


def _epoch(ts):
    """A ledger UTC stamp back to epoch seconds; -1 if unparseable, so a claim with no readable
    timestamp is treated as ancient (expirable) rather than immortal."""
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return -1


def pid_alive(pid):
    """True iff `pid` names a live process on THIS machine, RIGHT NOW. Used to tell a live
    sibling process's claim (do not touch — see `open_claims_detailed()`) apart from a dead one
    safe to reclaim, without waiting the full `ledger.lease.ttl_hours` (default 12h) for a crash
    the machine itself could already confirm.

    `os.kill(pid, 0)` sends no signal, only probes: `ProcessLookupError` means genuinely gone (the
    kernel has no such pid) -> False. `PermissionError` means the kernel found a live process at
    that pid owned by someone else -> still True, existence is what this asks, not ownership (in
    practice this process's own prior pids are always the same OS user, so this branch is a
    defensive fallback, not the expected path). Any OTHER exception (a platform where signal 0
    behaves differently, e.g. some Windows paths) fails toward True — "maybe still alive, don't
    reclaim yet" — because the existing TTL fallback is the real backstop for anything this check
    cannot resolve; the risk of guessing False and reclaiming a still-live sibling's goal (Girijesh's
    exact bug) is far worse than the risk of guessing True and waiting out the TTL like today.

    SINGLE-MACHINE ONLY, BY DESIGN — stated scope for this feature (one person, one machine, many
    of their own issues; see #374/loopsmith-parallel-autoupdate-plan.md). A pid recorded by a
    DIFFERENT machine under the same actor is meaningless to probe here (pids are a per-machine
    namespace: `ProcessLookupError` would wrongly read as "dead" for a claim that is very much
    alive on its own machine). Extending this cross-machine would need a machine-identity
    dimension in the writer string itself, not a change to this function — not attempted here."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:                # noqa: BLE001 - fail toward "assume alive", see docstring
        return True


def _writer(entry):
    """The claim's writer identity: `who:pid` for a post-#337 3-part `id`, or bare `actor` for a
    legacy 2-part id (no pid to distinguish — there was only ever one writer file per actor then).
    Mirrors watch_classify.py's own `_writer()` exactly, kept as an independent copy rather than
    imported — matching this module's existing per-module id-parsing precedent (`_seq()` below is
    ALSO duplicated in watch_classify.py and insight/ingest/ledger_reader.py, on purpose, so each
    stays free of a cross-module dependency for a five-line pure function)."""
    parts = str(entry.get("id", "")).split(":")
    if len(parts) >= 3:
        return f"{parts[0]}:{parts[1]}"
    return entry.get("actor", "")


def my_writer(config):
    """This process's own writer identity, in the exact `who:pid` shape `_writer()` parses out of
    a live entry's `id` — what `open_claims_detailed()`'s per-claim writer is compared against to
    tell "my own current process" apart from "a different process of mine" (F10/#337 already made
    every entry this process writes carry this pid; this is the read-side counterpart). Computed
    fresh every call, never cached — `os.getpid()` is the one thing riskier to cache than `actor`."""
    return f"{actor(config)}:{os.getpid()}"


def writer_pid(writer):
    """The pid embedded in a `writer` string from `open_claims_detailed()` (`actor:pid`), or None
    for a legacy bare-actor writer (pre-#337, no pid to extract) or anything else unparseable. A
    `gh` login can never itself contain `:` (GitHub's own username rules), so a writer with exactly
    one `:` is unambiguously `actor:pid`, never an actor name that happens to look like one."""
    _, sep, tail = str(writer).rpartition(":")
    return int(tail) if sep and tail.isdigit() else None


def claim_belongs_to_me(holder_actor, holder_writer, me, my_writer):
    """True iff an open claim (`holder_actor`, `holder_writer` — a value from
    `open_claims_detailed()`) is safe for ME (`me`/`my_writer`, from `actor(config)`/
    `my_writer(config)`) to treat as already mine: not another actor's work, and not a DIFFERENT,
    still-live process of my OWN actor (the exact race that let a routine's fresh invocation
    blindly re-attach to another session's in-flight worktree — #374). `_next()` (loop.py) and
    `work.py start()` both call this, so the decision is made exactly once, never reimplemented
    per call site.

    Three cases:
      - a different actor entirely -> False, always (unchanged from pre-#374 behavior).
      - my own CURRENT process's writer -> True, trivially (resuming my own crash/restart).
      - a DIFFERENT writer of the SAME actor: a legacy (pre-#337) claim has no pid to check
        (`writer_pid` is None) and is, degenerately, always "mine" — there was only ever one writer
        file per actor then, so no OTHER writer this claim could belong to -> True, matching
        pre-#374 behavior for the transitional legacy case exactly, no regression. A post-#337,
        pid-bearing writer is liveness-checked: a confirmed-dead pid is safe to reclaim (True); a
        live OR unresolvable pid is NOT (False) — fail toward "not mine," since guessing wrong here
        reintroduces the exact bug this exists to fix. The existing lease TTL (already applied
        upstream, inside `open_claims_detailed`'s own `ttl_seconds` filtering) is the real backstop
        for anything a liveness check alone can't resolve."""
    if holder_actor != me:
        return False
    if holder_writer == my_writer:
        return True
    pid = writer_pid(holder_writer)
    if pid is None:
        return True                          # legacy 2-part claim: degenerately always "mine"
    return not pid_alive(pid)                # dead sibling -> reclaim; live/unresolvable -> not mine


def _held(entries, now=None, ttl_seconds=None):
    """{goal: (actor, writer, ts)} for goals someone has `claimed` and not yet released with a
    terminal outcome (done/parked/failed) — the shared computation behind `open_claims()` (actor
    only, the original, still-used-elsewhere contract) and `open_claims_detailed()` (F10.5/#374,
    adds writer so a caller can tell two of the SAME actor's own concurrent processes apart, not
    just two different actors). One computation, two views — never duplicate the state machine
    itself, or the two would silently drift the way F10's review found `insight/`'s independent
    reimplementation already had.

    The latest lifecycle entry per goal wins, so a re-claim after a failure re-opens the lease under
    whoever took it last. With `ttl_seconds` set, a claim older than that is treated as EXPIRED — a
    crashed claimer must never lock a goal for the whole team forever; `now` defaults to wall-clock
    and is injectable for tests. `entries` must be oldest-first (read_all guarantees it)."""
    held = {}                                    # goal -> (actor, writer, ts)
    for entry in entries:
        goal = entry.get("goal")
        if not goal:
            continue
        kind = entry.get("kind")
        if kind == "claimed":
            held[goal] = (entry.get("actor"), _writer(entry), entry.get("ts", ""))
        elif kind in ("done", "parked", "failed"):
            held.pop(goal, None)                 # the claimer finished (or gave up) — lease released
    if ttl_seconds:
        cutoff = (now if now is not None else time.time()) - ttl_seconds
        held = {g: v for g, v in held.items() if _epoch(v[2]) >= cutoff}
    return held


def open_claims(entries, now=None, ttl_seconds=None):
    """{goal: actor} for goals someone has `claimed` and not yet released with a terminal outcome
    (done/parked/failed). This is the light lease the loop reads so two people running against one
    board don't start the same goal — the ledger's answer to "who has this right now?". See
    `_held()` for the shared state machine; see `open_claims_detailed()` for the writer-aware
    sibling a caller needs to tell two of ONE actor's own concurrent processes apart."""
    return {goal: actor for goal, (actor, _writer, _ts) in _held(entries, now, ttl_seconds).items()}


def open_claims_detailed(entries, now=None, ttl_seconds=None):
    """{goal: (actor, writer)} — `open_claims()`'s sibling, writer-aware (F10.5/#374). `writer` is
    `actor:pid` for a claim written post-#337, or bare `actor` for a legacy claim — see `_writer()`.
    Exists because a claim held by "my own actor" is not always safe to treat as "mine to resume":
    a DIFFERENT, still-live process of that same actor (two concurrent loops sharing one gh login)
    holding the SAME goal is exactly the race that made a routine blindly resume another session's
    in-flight worktree. `_next()`/`work.py start()` compare against `my_writer(config)`, not just
    `actor(config)`, to tell the three cases apart: my own current process (resume), a different
    but still-alive writer of mine (skip, treat like another actor), or a dead/unreachable one
    (reclaim — the existing TTL fallback above already covers the case liveness can't resolve)."""
    return {goal: (actor, writer) for goal, (actor, writer, _ts) in _held(entries, now, ttl_seconds).items()}


# --------------------------------------------------------------------------- render


def render(entries, recent=25):
    """The human view a lead reads instead of opening five files. Regenerated, never
    hand-edited — so a merge conflict on it is resolved by re-running, not by hand."""
    lines = [
        "# Team ledger",
        "",
        "_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_",
        "_`ledger.py render <sdlc-dir> --write`._",
        "",
    ]
    open_ones = outstanding(entries)
    states = handoff_states(entries)
    lines += ["## Waiting on someone", ""]
    if open_ones:
        lines += ["| when | from | to | priority | issue | state | what |",
                  "|---|---|---|---|---|---|---|"]
        for entry in open_ones:
            # F19: EVERY interpolated field goes through `_cell()`, not just `why`. `to`/
            # `priority`/`issue` arrive as free text from a sanctioned CLI call
            # (`handoff.py open ... --to "rae | INJECT ## header"`) with no enum to constrain
            # them the way `kind`/an ack's `state` have -- a stray `|` splits the row into extra
            # columns and a stray newline opens a new markdown line inside a file the whole team
            # reads and nobody hand-edits. `actor`/`ts` are construction-safe today (`_safe_name`,
            # `_stamp`) and `state` here is always a validated STATES value, but render() reads
            # entries straight off disk and is the last line of defense before this lands in
            # committed TEAM.md -- it must not depend on every upstream caller staying
            # disciplined forever, so escape uniformly rather than case-by-case.
            lines.append(
                "| {ts} | {actor} | {to} | {priority} | {issue} | {state} | {why} |".format(
                    ts=_cell(entry.get("ts", "")),
                    actor=_cell(entry.get("actor", "")),
                    to=_cell(entry.get("to", "")),
                    priority=_cell(entry.get("priority", "-")),
                    issue=_cell(entry.get("issue", "-")),
                    # `open` = nobody has replied. Shown per row because a count of "outstanding"
                    # cannot distinguish a stuck hand-off from one someone is already working.
                    state=_cell(states.get(handoff_key(entry), "**open — no reply**")),
                    why=_cell(entry.get("why") or entry.get("goal", "")),
                )
            )
    else:
        lines.append("_Nothing is blocked on another person._")
    lines += ["", "## Recent activity", ""]
    shared = team(entries)
    if shared:
        lines += ["| when | who | did | goal | detail |", "|---|---|---|---|---|"]
        for entry in shared[-recent:][::-1]:
            # F19: same reasoning as the table above -- every field through `_cell()`, not just
            # `goal`/`why`.
            lines.append(
                "| {ts} | {actor} | {kind} | {goal} | {detail} |".format(
                    ts=_cell(entry.get("ts", "")),
                    actor=_cell(entry.get("actor", "")),
                    kind=_cell(entry.get("kind", "")),
                    goal=_cell(entry.get("goal", "")),
                    detail=_cell(entry.get("why") or entry.get("state") or ""),
                )
            )
    else:
        lines.append("_No entries yet._")
    return "\n".join(lines) + "\n"


def _cell(text):
    """Keep a free-text value from breaking the markdown table it lands in. Splits on
    `str.splitlines()` rather than replacing a literal `"\\n"` -- a bare `\\r` (or `\\r\\n`/`\\v`/
    `\\f`/the rest of CommonMark's line-terminator set) sailed through a `\\n`-only replace
    untouched and reopened the exact F19/#346 injected-heading symptom (#454), the identical gap
    watch_classify.py's own independent `_cell()` copy had and fixed in #427/#449 -- mirrored here
    verbatim so the two copies do not silently diverge on what they guarantee."""
    return " ".join(str(text).replace("|", "\\|").splitlines()).strip()


# --------------------------------------------------------------------------- CLI


def _flags(argv):
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            name = token[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]
                i += 2
                continue
            out[name] = "true"
        i += 1
    return out


def main(argv):
    if len(argv) >= 5 and argv[1] == "append":
        sdlc_dir, kind, goal = argv[2], argv[3], argv[4]
        flags = _flags(argv[5:])
        if flags.get("issue", "").isdigit():
            flags["issue"] = int(flags["issue"])
        try:
            entry = append(sdlc_dir, _config(sdlc_dir), kind, goal, **flags)
        except ValueError as exc:
            print(f"ledger: {exc}", file=sys.stderr)
            return 2
        print(entry["id"] if entry else "OFF (config: \"ledger\": {\"enabled\": true})")
        return 0
    if len(argv) >= 3 and argv[1] == "render":
        sdlc_dir = argv[2]
        text = render(read_all(sdlc_dir))
        if "write" in _flags(argv[3:]):
            target = ledger_dir(sdlc_dir) / "TEAM.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target}")
        else:
            print(text, end="")
        return 0
    if len(argv) >= 3 and argv[1] == "mine":
        sdlc_dir = argv[2]
        flags = _flags(argv[3:])
        who = flags.get("actor") or actor(_config(sdlc_dir))
        mine = addressed_to(read_all(sdlc_dir), who)
        for entry in mine:
            print(f"{entry.get('ts', '')} {entry.get('kind', '')} from {entry.get('actor', '')} "
                  f"issue={entry.get('issue', '-')} priority={entry.get('priority', '-')} "
                  f"{entry.get('why', '')}".rstrip())
        if not mine:
            print(f"nothing addressed to {who}")
        return 0
    if len(argv) >= 3 and argv[1] == "summary":
        entries = read_all(argv[2])
        tally = counts(entries)
        still_open, no_reply = outstanding(entries), unanswered(entries)
        print(f"ledger: {len(entries)} entries | "
              + ", ".join(f"{k} {v}" for k, v in tally.items() if v)
              + f" | outstanding hand-offs: {len(still_open)}"
              + (f" ({len(no_reply)} with NO reply)" if no_reply else " (all answered)"
                 if still_open else ""))
        return 0
    print("usage: ledger.py append <dir> <kind> <goal> [--to X --issue N --priority P "
          "--why TEXT --state S --area A --ref R] | render <dir> [--write] | "
          "mine <dir> [--actor X] | summary <dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
