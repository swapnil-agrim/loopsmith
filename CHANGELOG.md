# Changelog

## Unreleased

### fix(loop): `watch_classify.classify()` no longer drops a deliberate self-addressed ledger note (#477)
`classify()`'s own-write filter (`if actor == me or entry.get("to") != me: continue`) fired on ANY
entry with `actor == me`, not just an un-addressed self-write, so it silently dropped a DELIBERATE
self-addressed note (`to == me`, written by `me`) before it ever reached the signature/delivery
logic -- exactly the shape a normal solo/single-watcher deployment produces, not an edge case. That
broke two already-shipped features outright:
- `handoff.py`'s same-area hand-off note (`handoff.py:230`, `to=ledger.actor(config, run)`),
  documented as self-addressed so a LATER session by the same actor is reminded the tracked issue
  exists -- that documented behavior never actually delivered.
- `agent_watch.py`'s dead-agent ledger fallback (`agent_watch.py:124`, `to=<the claim holder>`,
  usually yourself in a solo deployment) -- only the separately-configured, much rarer email path
  ever surfaced a dead-agent notification; the ledger fallback silently reached nobody in the
  common no-email-configured case.

Also blocked #385 (comment-to-ledger claimant notification) from landing: #385's design assumed
this delivery path already worked ("reusing the existing `to`-addressed note + inbox mechanism...
no new channel"), and #385's own plan-review is what traced far enough to prove it did not, for the
exact deployment shape (solo, self-claimed, self-watched) #385 is mainly for. Filed and fixed here
first since the fix's blast radius (shared `classify()` infrastructure, two other existing callers)
is broader than #385's own scope.

Fix: split the single condition into two, so "not addressed to me at all" and "my own un-addressed
write" are checked independently:
```python
if entry.get("to") != me:                    # not for me at all
    continue
if actor == me and not entry.get("to"):       # my own un-addressed write -- don't wake myself
    continue
```
Checked every `to=`-setting call site in the plugin (`handoff.py:225`, `handoff.py:230`,
`agent_watch.py:124`): all three are deliberate addressed notes, and no un-addressed self-write
(`claimed`/`done`/`parked`/`failed`/`ack`/`merge-armed`/`release`) ever sets `to`, so the second
clause is provably redundant with the first for every existing caller -- cheap insurance, not
guesswork.

Tests in `tests/test_watch.py`: a self-addressed note (`actor=me, to=me`) is confirmed dropped on
pre-fix code and confirmed to surface after the fix (`test_a_self_addressed_note_still_wakes_me`);
an un-addressed self-write (`actor=me`, no `to`) is confirmed still suppressed both before and
after -- the filter's actual original purpose, so the existing test covering it was renamed from
`test_my_own_entries_never_wake_me` to `test_my_own_unaddressed_writes_never_wake_me` now that "my
own entries" is no longer uniformly true; a note addressed to someone else, including one written
by `me`, is confirmed still excluded both before and after
(`test_only_entries_addressed_to_me_surface`). Plus an end-to-end test through agent_watch.py's
real `_notify()` ledger-fallback write (not a hand-built ledger entry) proving the dead-agent
fallback now actually reaches the solo watcher's own inbox via `watch.tick()`
(`test_agent_watch_dead_agent_ledger_fallback_note_reaches_the_solo_watchers_inbox`).

### feat(loop): bounded comment-reading fallback + doctor check for dependency markers (#389)
#376 shipped the PREVENTION half of dependency-marker handling: `handoff.hand_off()` now writes a
machine-readable `**Blocked by:** #N` marker to the issue's BODY, not just a comment. #389 is that
issue's deferred, secondary-safety-net half: a human commenting a dependency directly via the
GitHub UI (bypassing `hand_off()` entirely) previously left an invisible marker -- `mirror.py`'s own
corpus fetch is title+body only, by design (cost + secret-surface reasons), so `backlog_check`'s
auto-skip never saw a comment-only marker, however clearly a human would read it on the issue page.

**New shared helper**, `sources.fetch_comments(config, goal, run=None, limit=20)`: ONE `gh issue
view --json comments` call, returning `[{"id", "author", "body", "created_at"}, ...]` sorted
oldest-first (`created_at`, since `gh`'s own comment `id` is an opaque GraphQL node id, not a
sortable integer), `limit` keeping the most recent. Fail-open on any error (not `gh`, no auth, bad
ref, network blip, malformed JSON, a non-dict payload) -> `[]`, never raises. This is the ONE new
piece of shared comment-reading infrastructure in the codebase; it is deliberately generic enough
that a later claimant-notification feature (#385, not built here -- its own delivery mechanism has a
separate, unresolved blocker) can consume it unchanged, without a helper-contract change.

**`backlog_check.py`** gains an optional `extra_text` parameter on `_explicit_blockers()`, and a new
`_goal_comment_text()` that fetches + scrubs comments for the ONE goal actually being considered
(never corpus-wide) right before `cross_check()` would spend a real token -- reusing the exact same
`scrub()` call and the exact same open-ref precision guard the existing body path already applies,
not a second, looser copy. A no-op in local-goals mode (comments aren't a concept for local goal
files); fails open independently of `cross_check()`'s own outer catch, so a comment-fetch failure
degrades only the comment evidence, never the whole precheck. `precheck()` already threaded `run`
into `cross_check()` -- no signature change was needed on either.

**New `/sdlc-doctor` check**: an issue with a comment matching `backlog_check._BLOCK_RE` but no
matching marker in its own body is flagged as likely-intended-but-silently-ignored. Advisory only
(nothing auto-parks from it), github-mode only, and deliberately NOT gated on
`backlog_check.enabled` -- the blind spot exists (and arguably matters more) whether or not auto-skip
is currently turned on. Cross-loads `sources.fetch_comments` and `backlog_check._BLOCK_RE` verbatim
(a narrow, documented exception to `doctor.py`'s usual no-cross-skill-import convention) rather than
a doctor-local reimplementation of either, which would itself be the hardened-sibling-divergence bug
class this plugin already tracks elsewhere.

Cost-bounded, and the bound is ALWAYS visibly reported in the check's own `name` string (e.g.
"5/50 open goal(s)"), pass or fail, never silently applied. Default
`backlog_check.doctor_scan.max_issues` is **10**, tuned down from a 30 considered during design:
`gh issue view --json comments` measured ~0.62s/call against a real repo, and since candidates are
issues WITHOUT a body marker -- nearly every open goal issue in practice -- the cap is hit on
essentially any real backlog, making it the TYPICAL added cost, not a rare worst case. A default of
30 would have added ~18.5s to a routine `/sdlc-doctor` run (a 4-7x regression in a "one-command setup
check-up"); 10 keeps the typical added cost to ~6s, while `backlog_check.doctor_scan.max_issues` /
`max_comments` (default 20, matching `sources.DEFAULT_COMMENT_LIMIT`) stay configurable for a repo
that wants a wider scan and is willing to pay the extra time for it. Config is documentation-only --
no `sdlc_init.py` template change, matching every other opt-in feature's absent-until-set convention.

New tests: `tests/test_sources.py` (`fetch_comments`'s field mapping, sort/limit behavior, three
fail-open sub-cases, the `--repo` flag, and an id-less-comment edge case for #385's future benefit),
`tests/test_backlog_check.py` (comment-only marker auto-skips non-vacuously, the closed-ref precision
guard, the local-mode no-op, fail-open on a `gh` error, and the comment-text scrub boundary itself --
not an unrelated JSON dump, which would never have been able to fail either way), and
`tests/test_doctor.py` (the check flags/stays silent correctly, caps at `max_issues` while visibly
reporting the bound, is skipped in local mode, and survives a malformed `doctor_scan` config block).

### fix(ledger): `_cell()` now escapes `\r` too, matching #427's fix in `watch_classify.py` (#454)
Independent review of PR #449 (#427, `watch_classify.py`'s sibling `_cell()`) found that
`ledger.py`'s ORIGINAL `_cell()` -- the one #449's copy was duplicated from -- was never actually
fixed for the bare-`\r` gap: it still only escaped `|` and replaced a literal `\n`
(`.replace("\n", " ")`), so a `to`/`priority`/`issue`/`why`/`goal` value carrying a bare `\r` (or
`\r\n`/`\v`/`\f`/the rest of CommonMark's line-terminator set) sailed through untouched and reopened
the exact F19/#346 table-row-splitting / fake-heading-injection symptom via a one-character
delimiter swap in the payload -- CommonMark (and Python's own `str.splitlines()`) treats a bare CR
as a line terminator identical to LF. Filed separately from #427 rather than folded into that PR:
`ledger.py`'s `_cell()` is a different function, in already-merged F19/#346's territory, protecting
a different (lower-severity, human-facing TEAM.md rather than agent-facing LEDGER INBOX) surface --
but flagged there as a "hardened-sibling-divergence" risk regardless, since two independent copies
of the same-named, same-purpose helper should carry the same guarantee. Fix: the identical,
already-proven one-liner from #449 -- `" ".join(str(text).replace("|",
"\\|").splitlines()).strip()` -- dropped in verbatim, no design work needed. New test in
`tests/test_ledger.py`, mirroring both the existing F19 `\n`-injection test in this file and
`test_watch.py`'s own #427 `\r` regression test: a hand-off's `to` field carrying a bare `\r` plus a
fake `## INJECTED HEADER`, confirmed to render it as its own line (the exact injected-line symptom)
against the pre-fix code, then confirmed flattened into inert cell content after the fix.

### fix(loop): config.json containing literal `null` crashes with raw AttributeError, not a clear message (#453)
`config.json` containing the valid JSON value `null` (or any valid JSON non-dict: a list, string,
or number) parses successfully but produces a non-dict type. The loop verbs (`next`, `start`,
`next-batch`, etc.) then crash with a raw `AttributeError: 'NoneType' object has no attribute 'get'`
the first time they call `.get()` on the config they assumed was a dict. Regression: the original
fix for #403 (guarding absent config.json and surfacing a clear "run /sdlc-init" message) never
checked whether the parsed JSON was a dict. The contract for config.json is that it must be a JSON
object — anything else is as unusable as an absent file.

Fix: `state.load_config()` now checks `isinstance(parsed, dict)` after `json.loads()` succeeds. If
the parsed value is not a dict, it raises `ConfigMissing` with a clear message that includes what
type was found instead (e.g. "NoneType", "list", "str"), reusing the same catch point in `loop.py`'s
`main()` (introduced for #403) that already turns `ConfigMissing` into a graceful one-liner stderr
message. The fix covers all verbs uniformly without needing separate guards — every verb imports
`loop.py::main()`, which is the single entry point for all CLI verbs and catches the exception once
for all.

Verified with a non-vacuous regression test that reproduces the bug (confirm it crashes with a raw
AttributeError before the fix), then confirms the same test now raises `ConfigMissing` after the
fix, plus an extended test that verifies the fix also handles other valid-JSON non-dicts (lists,
strings, numbers). Full test suite (1483 tests, 93% coverage) passes; no existing behavior changed.

### docs: README's merge-gate status claim now reflects live PRs (#460)
The "Status (honest)" section claimed the autonomous merge gate "has not yet merged a live pull
request." This is now stale — the gate fires reliably in production on this repo: `work.auto_merge:
"always"` + `work.require_review: "approval"` are active, and 9+ live merged PRs each show an
independent post-PR `loopsmith:approve` review comment from the loop's own review phase, followed by
a same-account merge minutes later. Updated the claim with evidence and date (2026-08-06).

## 1.0.3 — the observability release

### feat(loop): local-only action log + `sdlc-log` status command (#463)
The ledger is shared, git-tracked, and meant for team-visible coordination events — the wrong place
for a full local trace of what LoopSmith is doing right now (every file touched, model/effort
choice, subagent dispatch, and every mechanically-guaranteed loop action). New, fully separate
mechanism: `skills/sdlc-loop/scripts/actionlog.py` writes one JSONL file per goal under
`.sdlc/state/log/<goal-stem>.jsonl` (gitignored via the existing `RUNTIME_IGNORES` — no new
gitignore code needed), config-gated (`action_log.enabled`, default `false`, matching this repo's
universal opt-in convention). Two write paths, closed and separate vocabularies: six
mechanically-guaranteed Python-layer call sites (`loop.py::_next/_record/verify_goal`,
`work.py::start/merge/post_review`) emit `INTERNAL_KINDS` directly, never reachable from the CLI;
a new `loop.py log <dir> <goal> <kind> [--thread T] [--k v ...]` verb lets an agent emit only the
five `AGENT_KINDS` (file/model_choice/agent_dispatch/agent_done/note) — the CLI's own
kind_allowlist check keeps it from ever forging an internal kind, mirroring how `_EMIT_KINDS`
already fences `loop.py emit` off from the ledger's own Class-1 kinds. Millisecond-precision
timestamps (not the ledger's whole-second `_stamp()`) because this log has a genuine same-file
concurrent-write case the ledger's per-writer-pid files never need to handle (two slice subagents
in one wave). Zero coupling to `ledger.py` by construction — `actionlog.py` never imports it — and
zero coupling the other direction too: the new read-side `sdlc-log` skill
(`skills/sdlc-log/`, `status`/`goal` subcommands, matching `sdlc-status`'s shape) reads the same
JSONL format independently, format-only coupling, importing neither `actionlog.py` nor `ledger.py`.
`sdlc-loop/SKILL.md` gained five small prose insertions at verified anchor points so the loop logs
its own dispatch/file/model-choice activity during a run. Verified non-vacuously: a byte-identical
ledger proof (run the identical scripted sequence twice, ledger *on* both times, `action_log` off
then on, assert the ledger ends up byte-for-byte identical either way — proving actionlog can never
leak into it) and a real two-process concurrency test (matching this repo's own #387 bar: genuine
OS subprocesses, not threads) that 2 processes appending 40 lines each to the same goal's log file
produce exactly 80 valid, uncorrupted JSON lines.

### fix(loop): generalize `hand_off()`'s labeling/assignee/dependency discipline to every LoopSmith-created issue (#462)
The one real `gh issue create` call site (`GitHubSource.create_dependency`) was only ever reached
through `hand_off()`, so a formal cross-area hand-off always got a label and an assignee — but the
majority-real-world case, a same-area follow-up finding (a review comment, a mid-goal discovery) had
no disciplined path at all: it got filed by hand via a bare `gh issue create`, orphaned, easy to lose
in a long session. Fix: `hand_off()`'s own machinery is generalized into
`handoff.create_tracked_issue(sdlc_dir, config, goal, area, why, *, same_area, immediately_actionable,
blocks_goal, ...)` — three required, keyword-only booleans (no default, so a caller can never
silently get the wrong routing) — and `hand_off()` becomes a thin wrapper around it
(`same_area=False, immediately_actionable=True, blocks_goal=True`), reproducing its exact existing
behavior plus one new `area:<area>` label. `blocks_goal` is a third axis the original draft didn't
name: without it, a non-blocking follow-up finding would incorrectly write the `**Blocked by:** #N`
body marker and get `backlog_check._explicit_blockers()` to auto-park the *current* goal behind an
issue that was never meant to gate it — a real correctness bug, not a style choice, proven by a
non-vacuous test on both sides of the axis. Independent PR review then found a SECOND, independent
route to the same false-blocking bug: `backlog_check._ledger_signals()` treats any
`ledger.outstanding()` `kind="handoff"` entry as a confident block, and `ledger.handoff_key()` falls
back to the FILING goal's own ref whenever no real issue number was recorded (the default outcome
for any source without `create_dependency`, e.g. `LocalSource`) — so a `same_area=False,
blocks_goal=False` call (a sanctioned "cross-area FYI, not a blocker") still wrote `kind="handoff"`,
letting a degraded/local source's unresolved entry confident-block the filing goal against itself.
Fixed by gating the ledger `kind` on `blocks_goal` too: only `(not same_area) and blocks_goal` (a
genuine cross-area blocking dependency, `hand_off()`'s own always-pinned case) writes
`kind="handoff"`; everything else writes `kind="note"` (to the resolved owner for the cross-area
case, to self otherwise) — structurally outside `outstanding()`'s reach either way. New non-vacuous
test reproduces the bug end-to-end with the real ledger and real `backlog_check` (not mocked).
`GitHubSource.create_dependency` gains a `goal_label=`
parameter (default `True`, backward-compatible) so a queued (not immediately-actionable) issue can
omit the goal label instead of being auto-picked. New `handoff.py track` CLI verb (`--queue
actionable|queued --assignee same-area|cross-area --blocks yes|no`, every axis a required value
flag) plus `SKILL.md` prose telling the agent to use it instead of a bare `gh issue create` for any
mid-goal finding. New `tests/test_issue_creation_boundary.py` (AST-based, mirroring
`test_import_boundary.py`/`test_vocabulary_coverage.py`'s own guard style) pins
`GitHubSource.create_dependency` as the one and only real issue-creation call site under `skills/`
and `hooks/`. `tests/test_backlog_check.py` gained a non-vacuous "dependency actually honored" test:
a `create_tracked_issue`-produced body marker is fed into a realistic `backlog_check.cross_check()`
run, which parks the goal while the tracked issue is open and releases it once closed — proven in
both directions, not just that the marker was written. All 12 pre-existing `hand_off()`-specific
tests in `tests/test_handoff.py` pass unmodified (`FakeSource.create_dependency` gained the new
`goal_label=` parameter, the one sanctioned test-double fix).

### feat(loop): background-agent-death watcher + email/ledger notification (#465)
If a background/subagent collapses mid-run during an unattended drain, nothing previously
noticed — the goal it was working silently never progressed, wasting the rest of the run's budget
watching a dead agent that would never finish. Generalizes the existing `session_start`/
`session_active`/`session_end` marker mechanism (`loop.py`, `.sdlc/state/session.active`, built for
the since-abandoned Routines feature but not dependent on it) from one whole-`.sdlc`-dir pid marker
to one marker per `(goal, thread)`: `.sdlc/state/agents/<goal-stem>/<thread>.active`, same
bare-pid-text format, same two-signal liveness check (`ledger.pid_alive()` + the existing lease
TTL). New `loop.py agent-start <dir> <goal> --pid PID [--thread T]` / `agent-end <dir> <goal>` CLI
verbs, emitted by `/sdlc-loop` at the exact points a goal (and, for slice parallelism, each
dispatched slice) starts driving; cleanup is automatic and gate-free from `loop.py`'s own
`_record()`, so a cleanly-finished goal never lingers in the candidate set regardless of how it
ended (done/parked/failed). New `agent_watch.py` module wires one more step into `watch.sh`'s
existing periodic tick — mirroring `watch.py`'s own thin shape — checking every goal with an open
ledger claim for a registered marker whose pid has genuinely died. Notification is off by default
(`agent_watch.enabled`) and, even when on, only ever runs because `watch.sh` itself already
requires `ledger.enabled`. Email (`agent_watch.notify.email`, its own nested `enabled` flag) is
stdlib `smtplib`/`email.message` only — zero new dependency — and deliberately never accepts a
literal password from `config.json` (which is git-committed): `pass_env` names an environment
variable instead (default `LOOPSMITH_SMTP_PASS`), so a credential can structurally never land in
version control. A misconfigured or failing email send is loud on stderr and always falls back to a
ledger note (reusing #385's existing `to`-addressed note+inbox mechanism) addressed to the goal's
claimant — never silently drops a notification, and never sends both. Exactly-once via a
signature-based cursor (`.sdlc/state/agent-watch-cursor.json`, `goal:thread:pid`), so the same dead
pid on the same (goal, thread) is suppressed after the first notification until a fresh
`agent-start` registers a new pid. New `tests/test_agent_watch.py` includes a real
`SIGKILL`-on-a-real-subprocess test (spawn a genuine child, register its real pid, kill it for
real, confirm detection within one tick and exactly one notification across two ticks) matching
`tests/test_watch.py`'s own `N_RACERS` non-vacuous-concurrency precedent, plus DI-mocked SMTP tests
proving the email/ledger-fallback contract and the env-var-only credential path — never a real mail
server. A follow-up fix in the same PR closes an independent-review finding: `thread` (an
LLM-authored slice id from `.sdlc/plans/<goal>.slices.json`, validated by `slices.py` with only
`.strip()`) was spliced unvalidated into `_agent_marker_path`'s path join — a `--thread` value
containing `../` sequences could write a marker file outside `.sdlc/state/agents/` entirely, since
pathlib's own `/` join re-parses a string argument for separator characters. Fixed with a shared
`_unsafe_thread_reason()` check (rejects `/`, `\`, `:`, `..`) enforced at both the CLI layer
(`agent-start`, matching `--pid`'s existing exit-2 rigor) and the `_agent_marker_path` chokepoint
itself, so every caller is protected regardless of whether it wraps the call in its own
try/except. Proven non-vacuously: the regression test was written and run against the pre-fix code
first, failed with the exact symptom (a real file created outside `.sdlc/`), then passed after the
fix.

## 1.0.2 — the second-pass release

### fix(loop): `next_pending` no longer trusts a single empty/failed backlog read as "nothing pending" (#447)
Two prior investigations reproduced but did not root-cause a freshly-created, correctly
`sdlc:goal`-labelled, non-parked issue being invisible to `next-batch`/`next` — repeated calls
returned a bare `DONE` with zero claims, not a one-off race. Root-caused this time by tracing the
exact `gh issue list` call `GitHubSource.next_pending()` runs with `GH_DEBUG=api`: it resolves
through GitHub's asynchronously-indexed GraphQL search backend (`search(type: ISSUE_ADVANCED,
...)`) regardless of whether `--search` is passed — even the PLAIN, non-`--search` listing (the
form used to "manually verify" a missed goal) routes through the identical search field, not a
direct/consistent repo read — so the read is only EVENTUALLY consistent. Reproduced in an isolated
scratch repo with zero other load: a freshly created-and-labelled issue routinely took 1-5s to
become visible to this exact query; a transient `gh`/API error collapses into the identical
"nothing pending" signal one branch over. Both looked, from `_next()`'s side, exactly like a
drained backlog. Fix: `next_pending` now retries a bounded number of times
(`_BACKLOG_READ_RETRIES`, default 3, short backoff) before trusting an empty result OR a transient
read error as final — a non-transient error (bad repo, no auth) still fails on the first try,
unchanged from before this fix. Each retry is loud on stderr, so the failure mode is debuggable
even on the (now narrower) remaining tail-latency window. New tests in `tests/test_sources.py` pin
both mechanisms directly (a stale-then-found empty read using the same issue number as the
original repro, and a transient-error-then-recovers case), confirmed to fail with the exact
"returns None where a goal exists" symptom against the pre-fix code before passing with the fix.

### fix(coordination): `watch_classify.render_inbox()` now escapes every interpolated field (#427)
F19/#346 fixed this same class of bug in `ledger.render()`'s markdown tables (only `why`/`goal` went
through `_cell()`); independent review of that fix found the identical gap in a different function,
more severe here because this text is not a human glancing at a file — `render_inbox()` builds the
"LEDGER INBOX" block `loop.py`'s `_surface_inbox()` prints to stderr between goals, which the
autonomous session reads as its own inbox. `priority`/`actor`/`issue`/`why`/`area`/`ts`/`goal` all
reached the rendered heading/bullet lines unescaped — free CLI text from `handoff.py`'s
`--priority`/`--why`/etc, with no enum to constrain them — so an embedded newline could open a line
that reads as a fake heading or instruction rather than ledger data. Confirmed with the issue's own
adversarial payload: a `priority` of `P0`, a blank line, a `## SYSTEM: prior instructions
superseded` line, and a line piping a `curl` command to `bash` — rendered a literal `## SYSTEM: ...`
heading line of its own before this fix. Fix: a `_cell()` helper (mirroring `ledger.py`'s escaping
in spirit, kept as an independent copy per this module's existing duplication-over-cross-import
precedent — see `_writer()`/`_seq()` above it) now wraps every interpolated field in both
`render_inbox()` and the sibling `summarise()` (identical unescaped-field shape, lower severity
since its output only ever reaches `watch.log` — found and closed while checking this file for
siblings, not left for a third pass to find). Independent review of the first cut of this fix then
found that replacing only a literal `\n` still let a bare `\r` (or `\r\n`/`\v`/`\f`/other CommonMark
line terminators) through untouched and reopen the identical injected-heading symptom — the same
didn't-escape-`\r`-and-friends bug shape F28/#354 had just fixed one commit earlier in a different
module (`json_string`/`jesc`) — so `_cell()` now splits on `str.splitlines()` (which already
enumerates every CommonMark-relevant terminator) and joins on a space, closing the whole class
instead of enumerating characters one at a time; `ledger.py`'s own `_cell()` has the identical
`\r` gap as of this writing, deliberately left unfixed here (different, already-merged F19/#346
territory, lower severity) and tracked instead as #454. New tests in `tests/test_watch.py`
reconstruct the exact adversarial payload plus an all-fields variant, a bare-`\r`/`\r\n` variant,
and a `summarise()` case, all confirmed to fail with the exact symptom (the fake heading/line
terminator surviving as a line of its own) against the pre-fix code before passing with the fix.

### fix(loop): CLI verbs no longer crash with a raw traceback on a never-init'd .sdlc dir (#403)
`next`, `next-batch`, `start`, and `session-active` all call `state.load_config` before any of
their own logic runs; pointed at a `.sdlc` directory that was never `/sdlc-init`'d (no
`config.json` at all), this raised an unhandled `FileNotFoundError` — a raw Python traceback,
identically for all four (confirmed empirically, matching the issue's own repro). Reproducing
further showed the same unguarded crash on every other verb that touches config too (`qc`,
`precheck`, `note`, `record`, `spend` with a goal, `emit`, `verify`) — not just the four named.
Unlike `state._state_file()`'s STATE.md (gitignored per-run state, safe to scaffold on first read
— see its own docstring), `config.json` is never safe to default: it carries the actual project
choices (discovery source, ledger, verify command...), and silently inventing one could even
misreport a never-set-up repo as an empty-but-configured backlog instead of surfacing the real
problem. `state.load_config` now raises a distinctly-typed `state.ConfigMissing` with a clear,
actionable message ("no config.json under `<dir>`... Run /sdlc-init..."), and `loop.py`'s `main()`
is now a thin wrapper around the renamed `_dispatch()` with a single `except state.ConfigMissing`
catch — one shared guard at the one function every verb already funnels through, rather than a
separate try/except at each call site, so the fix covers every current and future verb uniformly.
A `config.json` that exists but fails to parse is left as a plain `json.JSONDecodeError` — a real
corruption bug, not a setup problem, so it deliberately keeps its own (different) failure mode.
New tests: `test_state.py` pins `load_config` raising `ConfigMissing` (and, by contrast, NOT
swallowing a malformed-JSON config into the same message); `test_loop.py` drives the CLI boundary
for all four issue-named verbs and asserts a clean exit 2 with an actionable one-liner and no
"Traceback" on stderr — confirmed to fail with the exact original symptom (an unhandled
`FileNotFoundError` propagating out of `lp.main()`) against the pre-fix code before passing with
the fix restored.

### fix(coordination): `work.py start()`'s "branch outlived its record" fallback now also guards against a live sibling (#388)
`start()` has two resume-like paths. The primary `already started` return (a local state record is
present and its worktree still exists) has been guarded by `_resume_blocked_by_a_live_sibling` since
#374. A second, narrower fallback a few lines later was not: it fires when the record is MISSING but
the branch and worktree already exist on disk (a partial `.sdlc/state` cleanup, a corrupted or
truncated JSON file, a machine migration) — it catches `git worktree add -b`'s failure (the branch
already exists) and silently reattaches via a plain `git worktree add <path> <branch>`, with no
liveness check at all. A still-live sibling process holding the ledger's claim on that goal could be
double-worked through this second path exactly as #374 closed for the first, just reached via a
lost-record precondition instead of a normal resume. The fix calls the same
`_resume_blocked_by_a_live_sibling` guard inside the `except` branch, before the reattach, and returns
its `REFUSED` string instead of running `git worktree add` when the claim belongs to a different,
still-live writer.

New test `test_start_reattach_refuses_to_resume_when_a_live_sibling_process_holds_the_claim` mirrors
#374's own `test_start_refuses_to_resume_when_a_live_sibling_process_holds_the_claim`, but via the
missing-record precondition instead of the existing-record one; confirmed to fail with the exact
symptom (a silent `worktree ... on ...` success instead of `REFUSED`) against the pre-fix code before
passing with the fix. `test_start_reattaches_to_a_branch_that_outlived_its_record` is extended to also
exercise a ledger-enabled config with no rival claim present, proving the guard narrows an unsafe
resume without newly blocking a legitimate one.

## 1.0.1 — the hardening release

### fix(doctor): north-star "filled" check now clears every tier, not just Vision (F33/#358)
`doctor.py`'s north-star "filled" probe tested the file for only the Vision-tier placeholder
(`"<the change you want"`), so a north-star with Vision written up but Strategy/Design/Architecture
still on the scaffolded placeholder text read as "filled" anyway. A new `_NORTH_STAR_TIERS` table
holds one distinctive placeholder prefix per tier (Vision/Strategy/Design/Architecture, mirroring
`sdlc_init.py`'s `_NORTH_STAR` template); the check now walks all four and reports the first tier
still on its placeholder text in the fix message instead of a generic "fill the tiers" line. New
tests in `test_doctor.py` cover a Vision-only north-star (the issue's own acceptance case), a
fully-filled one, and one where only Architecture remains a placeholder — confirmed to fail with the
exact symptom (the check reading `ok: True`) against the pre-fix code before passing with the fix.

### fix(loop): `discovery.next_pending` no longer treats a blank `status:` as runnable (F32/#357)
`next_pending`'s pending-check was `status is not None and status not in _SKIP` — a goal file with
`status:` present but empty (or whitespace-only, which `frontmatter.parse` already collapses to `""`
via its own `.strip()`) satisfies both `"" is not None` and `"" not in _SKIP`, so it was picked up as
the next runnable goal instead of being skipped like a missing or terminal status. The check now reads
`status and status not in _SKIP`, so `None` and `""` are both excluded via the same falsy branch, and
any genuine non-terminal status still runs exactly as before. A new test writes goals with an empty and
a whitespace-only `status:` ahead of a real `pending` one and asserts the `pending` goal is returned,
confirmed to fail against the pre-fix code with the exact symptom described (the blank-status goal
returned instead).

### fix(coordination): `frontmatter` parser now tolerates `\r\n` line endings (F31/#356)
`frontmatter.py`'s `_FENCE` regex anchored the fence delimiters on a bare `\n`
(`^---\n(.*?)\n---\n?`), so any caller passing raw `\r\n`-terminated text (e.g. subprocess
output or a hand-built string, as opposed to `Path.read_text()` which normalizes newlines)
never matched the fence at all — `parse()` silently returned `{}` as if no frontmatter were
present. The regex now allows an optional `\r` before each `\n`
(`^---\r?\n(.*?)\r?\n---\r?\n?`); `str.splitlines()` on the captured body already handles
`\r\n` per line, so no further change was needed. A new test parses a `\r\n`-delimited goal
string and confirms the fields come back correctly, proven to fail with a `KeyError` (the
empty-dict symptom described in the issue) against the pre-fix regex before passing with
the fix.

### fix(coordination): `owners._matches` no longer lets a single `*` cross a `/` (F30/#355)
`_matches` matched CODEOWNERS patterns with plain `fnmatch`, whose `*` matches anything including
`/` — so `engine/*` (meant to own only direct children of `engine/`) also matched deep descendants
like `engine/a/b/c.py`, over-assigning ownership beyond real CODEOWNERS/gitignore semantics, where a
single `*` never crosses a path separator. A new `_glob_match` helper translates a pattern to a regex
by hand, turning `*` into `[^/]*` and `**` into `.*`, and replaces the four `fnmatch.fnmatch` call
sites inside `_matches`; the already-correct `**`-crosses-`/` behavior (used by directory rules like
`/engine/` internally) is preserved. A new test in `tests/test_handoff.py` pins `engine/*` matching
`engine/a.py` but not `engine/a/b/c.py`, confirmed to fail with the exact over-match symptom against
the pre-fix code before passing with the fix.

### fix(collectors): `json_string`/`jesc` now escape `\r` and the rest of the C0 control-byte range (F28/#354)
`json_string` (duplicated verbatim across `discovery-scan.sh`, `risk-detect.sh`,
`alignment-collect.sh`, and `completion_gate.sh`) escaped only `\ " \n \t`; its awk-side counterpart
`jesc` (duplicated across `risk-detect.sh` and `alignment-collect.sh`, used inside the diff-body
scanners) escaped even less — only `\` and `"`. A value carrying any other C0 control byte (`\r`,
backspace, form feed, ...) went out raw, producing invalid JSON per RFC 8259, which requires every
byte U+0000-U+001F to be escaped. Latent in practice (git C-quotes control bytes in the `file` field
most call sites carry) but genuinely reachable — e.g. alignment-collect.sh's `subject` field is a raw
commit message. All six copies now escape `\r` (plus `\b`/`\f` for `json_string`) via dedicated
short-form escapes and fall back to a generic `\uXXXX` for any other byte in the 1-31 range, kept in
lockstep across every call site (parity-tested). `jesc` (awk) does the fallback with `gsub`/`sprintf`,
in-process, no forking. `json_string` (bash) does it as ~30 static `${s//$'\NNN'/\uXXXX}` replacements
resolved at parse time — NOT a runtime loop over `$(printf ...)`, which was tried first and forked up
to 52 subshells per call regardless of whether the input needed escaping at all (caught in independent
review: ~700x slower in isolation, ~4-9x slower on real `alignment-collect.sh`/`discovery-scan.sh`
runs over this repo's own history — both back to within ~2x of pre-fix baseline after the rewrite, with
byte-identical output proven old vs. new). New `tests/test_json_string_escaping.py` feeds each copy a
probe covering `\r`, backspace, form feed, a generic C0 byte, and the pre-existing `\ " \n \t`; proven
to fail with the exact "Invalid control character" symptom against the pre-fix code and pass after.

### fix(collectors): `churn_hotspots` no longer collapses internal whitespace in a path (F27/#353)
`alignment-collect.sh`'s `HOTSPOTS_JSON` step stripped the `uniq -c` count via awk's `$1=""`, which
rebuilds `$0` using a single-space `OFS` — so a path with consecutive spaces or tabs (e.g. `"a  b.py"`)
collapsed to a single space (`"a b.py"`) in the emitted `churn_hotspots[].file`, and two distinct such
paths could collapse to the same key. The sibling `json_file_array`/`OUTSIDE_JSON` steps use `p=$0`
(the whole record) and were never affected. The awk step now locates the `uniq -c` count prefix
directly (`match($0,/^ *[0-9]+ /)`) and takes everything after it as the path verbatim
(`substr($0,RLENGTH+1)`), parsing the count from the matched prefix instead of `$1`. A new test commits
a path with a double space and confirms it survives verbatim in `churn_hotspots`, proven to fail
against the pre-fix awk with the exact collapsed-path symptom before passing with the fix.

### fix(init): `sdlc-setup configure()` no longer clobbers an explicit `assignee` on re-run (F24/#352)
`configure()`'s github-discovery block set `gh["assignee"] = "@me"` unconditionally, while every other
default in the same function uses `setdefault` — so re-running `/sdlc-setup configure` on an adopter's
`.sdlc/config.json` silently overwrote a deliberately-scoped `assignee: "specific-user"` back to `@me`,
breaking idempotency. `gh.setdefault("assignee", "@me")` now defaults only when the key is unset, and
the discovery note interpolates the resolved value instead of a hardcoded `@me`, matching the pattern
`work.auto_merge`'s note already uses a few lines down. A new test pins a pre-set assignee surviving a
`configure()` re-run, confirmed to fail with the exact clobber symptom against the pre-fix code.

### fix(model): bare "story" no longer mis-tiers agile goals to the fable (creative) tier (F16/#350)
`predict.py`'s fable pattern included a bare `story` alternative alongside `storytell`, so any goal
merely containing the word — "Story: add pagination", "Implement the user story for checkout", "Add a
story point field" — matched `\bstory` and routed to the fable (creative-writing) tier, even though
none of it is creative writing. Genuine storytelling goals ("write the storytelling for the launch")
still match via the separate `storytell` alternative, which was already in the pattern and untouched.
Dropped the bare `story` alternative from `_PATTERNS`; agile "story" phrasing now falls through to the
`sonnet` default like any other ordinary implementation goal. New test cases in
`tests/test_model_predict.py` cover all three phrasings from the issue and confirm they fail against
the pre-fix pattern with the exact symptom described.

### fix(loop): `max_iterations` now follows the header's own "absent/zero enforces nothing" rule (F18/#349)
`_budget_spent` special-cased `max_iterations` with `budget.get("max_iterations", 20)` and a plain
`>=` — an absent key silently capped a run at 20 goals, and an explicit `max_iterations: 0` halted it
immediately (0 goals) — while the file's own header and this same function's docstring both already
promised "an absent/zero key enforces nothing." `max_minutes`/`max_tokens` (and `next_batch`'s own
remaining-budget cap, a few functions down in the same file) already honored that rule; only
`max_iterations` didn't. `max_iterations` now uses the identical falsy-guard as the other two budgets,
and the header/`SKILL.md`/`README.md` prose describing "iterations always enforces" is corrected to
match. A new test pins the fixed semantics directly against `_budget_spent`, proven to fail with the
exact pre-fix symptom (absent key tripping at 20) before the fix and pass after; two existing tests
that relied on `max_iterations: 0` as an "already spent" sentinel now use a genuinely-reached nonzero
cap instead.

### fix(doctor): worktree-dep check now flags `./`/`../`-prefixed relative paths (F23/#351)
The `verify.command resolves in the goal worktree` check's `_WORKTREE_DEP` regex used a lookbehind
that excluded any preceding `/` or `.` so an absolute path like `/x/.venv/…` reads as exempt — but
that same exclusion also swallowed an explicit relative prefix: `./node_modules/.bin/eslint` and
`../venv/bin/python` were never flagged, only a bare leading `node_modules/…` was, even though both
are exactly the worktree footgun this check exists to catch (a fresh goal worktree has none of the
caller's installed deps, so the command fails exit=127 on the first real run). The regex now consumes
an optional, repeatable `./` or `../` prefix (`(?:\.\.?/)*`) before the dep name while keeping the
same lookbehind, so `./node_modules/…`, `../venv/…`, and `../../node_modules/…` are all flagged, and
a true absolute path is still exempt exactly as before.

### fix(handoff): a local/issue-less hand-off can now actually be acknowledged (F22/#347)
`handoff.py`'s `ack` CLI unconditionally required `--issue <n>`, with no `--goal` flag at all — but
`hand_off()` writes `issue=None` whenever its source can't open issues (no `gh`, or a local backlog),
and `ledger.handoff_key()` has always fallen back to the goal string in exactly that case. A hand-off
raised that way could never be settled: the CLI demanded an issue number that was never assigned, so
it sat `outstanding`/`unanswered` forever, and the inbox's own instructions told the reader to run
`ack --issue <n>` for an `<n>` that didn't exist. `main()`'s `ack` branch now accepts `--goal` as an
alternative identifier — either satisfies the "which hand-off is this" requirement, and `--issue`
still wins when both are given, matching `handoff_key()`'s own precedence — and threads it through to
`acknowledge()`, which already keyed correctly the moment it actually received a goal.

### fix(work): `max_review_cycles: 0` (or negative) no longer silently disables the hard cap
`post_review()`'s cap check computed `cap = int(... or 0)` then gated on `cap and cycles >= cap` —
`cap and ...` short-circuits false the instant `cap` is exactly 0, so a configured
`max_review_cycles: 0` read as "no cap": six-plus consecutive blocks all posted with nothing ever
parking, the exact review→fix→re-review runaway this gate exists to prevent, and `0`-as-unlimited
was undocumented anywhere — the docstring says "HARD CAP", full stop. A negative value failed the
opposite way: `cap and cycles >= cap` goes true the moment `cycles` first reaches 1, parking on the
very first block with zero fix attempts. Neither is a real "no cap" sentinel, just `int()` coercing
a bad config value, so any cap below 1 now normalizes to the documented default (3) instead. Tests
cover both `0` and a negative value, each confirmed to fail against the pre-fix code with the exact
symptom the issue described (F20/#343).

### fix(handoff): a rejected assignee no longer takes the whole dependency issue down with it
`create_dependency()` built its `gh issue create` call with `--assignee <owner>` whenever CODEOWNERS
resolved one, and let a failing call raise straight out uncaught. GitHub issues can only be assigned
to individual collaborators, never a team — so a CODEOWNERS entry naming `@org/team` (a common,
supported pattern) made `gh` reject the call every time, `hand_off()`'s existing try/except caught the
RuntimeError, and the whole hand-off degraded to a ledger-only entry: no issue opened, nobody's loop
ever routed the dependency, purely because the owner was a team. Any other assignee `gh` rejects (not
a collaborator, a typo, a removed account) failed the identical way.

`create_dependency()` now creates every issue unassigned first (one `gh issue create` call, never
combined with `--assignee`), then assigns as a separate `gh issue edit --add-assignee` step against
the now-known issue number. Two rounds of independent review shaped this: round 1 flagged that the
first fix's fallback note embedded `str(exc)` wholesale, which for a real `gh` failure is the entire
reconstructed command line (`--body <the whole issue body>` included) rather than just the reason —
`_run_gh` now exposes the short reason alone via `exc.hint`, and the note uses that. Round 2 found a
more serious problem with the fallback itself: `gh issue create --assignee` is not atomic (`gh` runs
`createIssue` then a separate `replaceActorsForAssignable` mutation), so when only the assignment
half fails, the issue it already created is not rolled back and its number is never printed — a
combined call has no way to learn that orphan exists, so retrying unassigned created a SECOND,
genuinely duplicate, permanently untracked issue every time (confirmed against real `gh`, not
assumed). Creating unassigned first and assigning as an independently retriable second step makes a
duplicate structurally impossible: a rejected assignee just leaves the one issue that already exists
unassigned, with a comment on it naming why. `GitHubSource.last_assignee_applied` records whether the
assignment actually took, and `hand_off()`'s own narrative comment checks it before claiming
"assigned to @owner" — a resolved CODEOWNERS owner is no longer treated as proof the assignment
happened. A genuine, non-assignee-specific failure on the issue-create call itself (auth broken,
network down) still raises uncaught, exactly as before.

### fix(work): comment-marker parsing is now line-anchored, not a bare substring test (F9/#340)
`_comment_directive`'s `"loopsmith:approve" in body.lower()` matched ANYWHERE in a PR comment, so
`"do NOT loopsmith:approve"` (a negation), `"loopsmith:approved"` (a different word — no `\b`), and a
marker quoted back or shown as a code sample (inside a fenced ``` block or a `>` blockquote) all
registered as a real directive; symmetrically, a comment merely DISCUSSING `loopsmith:block` could
wrongly PARK a clean PR. This is the merge-approval signal `work.require_review` reads, so a false
positive silently skips the review gate and a false negative silently blocks a good merge. New
`_line_directive` scans a comment body line by line against an anchored
`^\s*loopsmith:(approve|block|unblock)\b`, skipping fenced and `>`-quoted lines — a marker must be the
leading token of its own line (optional indent) to count; trailing prose after it on the same line is
still fine. `_comment_directive` now calls it per comment, keeping the same "latest marker wins" rule
across comments.

### fix(loop): `verify.enforce` no longer silently disables the done-gate on `1` / `"true"`
Both `record done`'s machine-checked gate and the `loop.py start` config warning tested
`verify.enforce` with a strict `... is True` — the same idiom `ledger.enabled` deliberately uses so
a stray truthy value can't quietly switch a team surface ON. `enforce` inverts which direction is
safe: it gates `record done` itself, so `enforce: 1` or `enforce: "true"` (easy JSON typos for the
literal bool) silently failed the strict check and let an unverified `done` through with no warning
at all — the unsafe direction, the opposite of `ledger.enabled`'s. Adds `_enforce_enabled()`, used at
both call sites: a real bool passes through unchanged, a string reads as off only when it spells out
false/no/off/empty, and anything else falls back to plain truthiness — so a common misspelling now
either enables the gate outright or (with an empty `verify.command`) still surfaces the existing
"EVERY `done` will be refused" warning, never both silently off and silent about it (F17/#342).

Independent review found `skills/sdlc-doctor/scripts/doctor.py` reads the SAME `verify.enforce`
value, via the same fragile `is True`, at its own "permanent-refusal trap" check and its
feature-dashboard status row — built together with loop.py's gate as a matched pair, not an
unrelated check. Left alone, this PR would have turned a latent inconsistency (both sides silently
NOT enforcing a non-bool truthy value) into an actively misleading one: loop.py now genuinely
refuses every `done` for `enforce: 1`, while doctor stayed silent and its status row claimed "off".
`doctor.py` gets its own copy of the same `_enforce_enabled` logic (intentionally duplicated, not
imported — doctor.py has no cross-skill dependency on anything else, by design), with a parity test
pinning both copies to the same truth table so they can't silently drift apart again.

### fix(loop): close the whole-second staleness hole in the verify done-gate (F11)
`done_refusal` (state.py) and its textual duplicate `_done_refusal` (loop.py) compare the verify
evidence's `at` timestamp against `run_started_at` — both used to be stamped via `int(time.time())`,
floored to a whole second. A verify from a PRIOR run landing a fraction of a second before this run
started could floor to the SAME integer second as `run_started_at`, so `at < run_started_at` read
false and a stale green cleared `record done` under `verify.enforce` as if it were fresh — a narrow
but real sub-second acceptance window that only ever accepted stale evidence, never over-rejected.
A naive `at <= run_started_at` refusal was tried first and rejected: it deterministically broke the
feature's own happy path (verify immediately followed by record-done — two fast subprocess calls
that routinely land in the same wall-clock second) across 19 tests, since same-second-but-
genuinely-later evidence would then read as stale too. The actual fix keeps sub-second precision
instead of narrowing the comparison: `state.start_run` and `loop.py verify_goal` now stamp the raw
`time.time()` float, and `state.load_cursor` gained a dedicated `_read_float` reader (`_read_int`'s
`\d+` regex would otherwise silently truncate the fractional part back off on read). `done_refusal`'s
`<` and its "fresh = at/after this run's start" contract are unchanged; the real sub-second ordering
just survives the round trip now. New boundary tests in `tests/test_state.py` and
`tests/test_pipeline.py` reproduce the issue's repro scenario with concrete numbers and prove both
directions: genuinely-earlier evidence in the same floored second is now refused, and
genuinely-later (or exactly-simultaneous) evidence in the same floored second still accepted.

### fix(watch): a priority-escalated re-raise of the same hand-off no longer vanishes (F13/#345)
`watch_classify.signature()` keyed suppression on `kind:issue:state` alone, but `hand_off()` always
writes `state="open"` (it never varies it — see handoff.py) — so a later re-raise of the same issue,
say escalating P1 -> P0, or re-opening after a decline, kept the IDENTICAL signature as the first
raise even though it carried a new id/seq and a strictly more urgent priority. Once that first raise
had already been surfaced and its signature recorded, the escalation matched it and was silently
dropped from the inbox — a missed escalation, which is worse than a duplicate. `signature()` now
folds `priority` into the tuple, so an escalating re-raise reads as news while a re-raise that
repeats the same priority (truly nothing new) stays suppressed exactly as before.

### fix(watch): the ledger watcher's pid guard is now race-safe under a genuine concurrent start
`watch.sh`'s idempotency guard was a `kill -0` check followed by a SEPARATE pidfile write — a TOCTOU
gap letting two truly simultaneous starts both pass the check before either writes, both enter the
tick loop, and both contend on the ledger worktree's git index lock (F21/#339). More likely now that
a single session can dispatch several `loop.py` calls close together (F10.5-3/#375, already shipped
in 1.0.0), each of which triggers its own watcher launch attempt.

Two schemes were tried and independently broken during this fix's own development before landing on
the shipped design, proven each time with real concurrent `bash` processes, not mocks: an `mv`-based
stale-pidfile eviction (mirroring the shape loop.py's flock-based claim lock, F10.5-2/#387, needed in
Python) let a delayed racer's `mv` clobber an already-fresh winner's pidfile; a follow-up mutex-based
redesign's `stat`-failure fallback made "the mutex was already legitimately released" indistinguishable
from "infinitely stale", letting multiple losers reclaim an already-free mutex at once. Both were
real, reproduced double-wins, not theoretical concerns.

The shipped fix serializes the whole check-evict-create decision behind a short-lived `mkdir`-based
mutex (POSIX-atomic, held only for a handful of near-instant local filesystem calls, not the
watcher's lifetime) instead of trying to make a multi-step sequence itself safe to interleave. The
exit trap only removes the pidfile if it still names the process removing it, defense in depth
against the exact symptom F21 named (a fast-exiting winner's cleanup orphaning a slower survivor).

Independent review found the mutex's own staleness-recovery path had the identical shape one level
up: a plain, unguarded `stat` + age-check + `rmdir` + `mkdir` reclaim sequence let several racers who
all read the SAME stale mtime race that four-step sequence against each other — reproduced
empirically (70-90% double-win rate at 15/40/80 racers against a deliberately orphaned mutex, up to 7
processes simultaneously alive). Unlike the two bugs above, this one reproduces reliably through this
file's own `pytest`/`Popen`-launched suite (0/10 anomalies after the fix, same harness both
directions) because its trigger window is a deliberately wide one (tens of seconds), not a razor-thin
natural race. Fixed with the same discipline applied one level deeper: the reclaim decision is now
gated behind its own atomic `mkdir` (`watch.decide.lock.reclaim`), so at most one racer can ever be
inside the stat-evict-create sequence at a time. That inner gate deliberately gets no staleness
recovery of its own — its critical section is shorter still, and an orphaned gate fails SAFE (every
future racer backs off and prints "a sibling is deciding" forever, an inert watcher) rather than
unsafe (silent double-execution), so a human clearing a stuck `.reclaim` directory by hand is an
acceptable, self-announcing residual.

A third independent review, adversarial toward the shipped design specifically (300-racer bursts,
fault injection comparing directly against the prior design, both-gates-stale total-hang
confirmation), approved the race-safety itself but caught that "self-announcing" was aspirational:
`loop.py`'s real invocation redirects `watch.sh`'s stdout/stderr to `/dev/null`, and the two backoff
messages were plain `echo`, never written to `watch.log` the way tick-loop messages are — so the
"immediately visible" half of the residual's risk acceptance was actually silent in the one context
this script really runs in. Both backoff messages are now `tee`'d to the log too.

The race-safety design above was correct all along, but shipping it revealed a SEPARATE, genuine
portability bug real Linux CI caught and pure local (macOS) testing never could: the reclaim path's
staleness check used `stat -f %m "$MUTEX" 2>/dev/null || stat -c %Y "$MUTEX" 2>/dev/null || true`,
assuming `-f %m` (BSD/macOS format-flag syntax) fails cleanly on GNU/Linux so the `||` falls through
to `-c %Y`. It does not: GNU coreutils' `-f`/`--file-system` means something else entirely
(filesystem status, not a format flag) and does not take a `FORMAT` argument, so the call instead
prints GNU stat's own default filesystem-info block (starting `  File: ...`) to stdout while still
exiting nonzero — and that stray text lands in `mtime` before the fallback ever runs. The word
"File" inside `mtime`'s value then hits bash's own arithmetic-expansion quirk: `$((now - mtime))`
recursively treats bareword tokens inside a variable's value as further variable names, so the
literal text "File" is dereferenced as `$File` — unset, and under `set -u` that is an immediate,
whole-script `unbound variable` crash. 100% reproducible on Linux, every time, never on macOS; not a
race, not CI flakiness. Fixed by no longer asking the shell's `stat` to be portable at all: the mtime
read now shells out to `python3 -c '...os.stat(...).st_mtime...'`, exactly like this same file
already does a few lines down for the config-driven tick INTERVAL — one call, correct identically on
every platform Python runs on.

### fix(loop): `next_pending` no longer starves the true-oldest goal past the 200-issue cap (F12/#348)
`GitHubSource.next_pending()` fetched `gh issue list --limit 200` with no explicit sort, sorted the
result ascending by issue number, and took `[0]` — reading that local ascending sort as "oldest
first." But plain `gh issue list` defaults to created-DESC (newest first) with no ASC option of its
own, so on a backlog of more than 200 open goals the `--limit 200` cap fetched the 200 NEWEST issues,
not the 200 oldest; sorting THOSE ascending and taking the lowest returned the oldest-of-the-
newest-200 (e.g. issue 51 out of 250) rather than the genuinely oldest (issue 1) — exactly the goals
"oldest-first" priority is supposed to favor starved until enough newer issues drained the backlog
back below 200 for them to even appear in the fetched page.

Fixes by asking GitHub's search API for the order directly: the call now carries `--search
"sort:created-asc"` alongside the existing `--label`/`--state`/`--assignee` filters, which combine
with `--search` unchanged (confirmed against the real API) — so the fetched 200-issue page already IS
the 200 oldest open goals, and the existing local `.sort()` + `[0]` then only breaks ties within that
page instead of silently reading from the wrong end of the queue. New tests assert the `--search` flag
is sent and, per the issue's own verification method, that a mocked 250-issue backlog (beyond the
200 cap) picks the true oldest rather than the oldest-of-the-newest-200 — confirmed to fail against
the pre-fix code with exactly that symptom (`'51'` picked instead of `'1'`).

### fix(ledger): auto-merge arm no longer logs a false `merged` entry (F26/#344)
`work.py merge()` called `gh pr merge --auto` — which only ARMS GitHub's auto-merge, it does not
confirm the PR landed; a later-failing check or a cancelled auto-merge can still mean it never does —
then immediately appended a `merged` kind to the team ledger. TEAM.md's "Recent activity" table
renders `kind` verbatim, so a PR that never actually merged still showed as landed, and nothing else
in this codebase later corrects the record: there is no watcher that observes the real GitHub merge
event.

Adds `merge-armed` to the entries-stream vocabulary (`ledger.KINDS`/`SHARED_KINDS`, shared in
TEAM.md same as `merged`) and switches the arm-time write in `work.py merge()` to it; `merged` stays
declared for when a real merge-confirmation write site exists. Renamed
`test_merge_logs_a_merged_entry_to_the_ledger` to `test_merge_logs_a_merge_armed_entry_to_the_ledger`
and extended it to assert the arm path logs `merge-armed` and never `merged`; updated
`test_vocabulary_constants_match_spec_table`'s pin to match.

Known, disclosed gap left for `insight/`'s own owner: `insight/contract/vocabulary.json`'s
`entries_kinds` and the `PRs landed` panel (`insight/dash/panel.py`, `WHERE kind='merged'`) still key
on the literal `merged` string. The two sides are documented as kept in sync "by hand, not enforced"
(`insight/contract/README.md`), and no engine write site has ever produced a `merged` entry insight
could safely have counted as an actual landing, so this is not a regression of a working count.

### fix(ledger): `render()` now escapes every table cell, not just `why`/`goal` (F19/#346)
`_cell()` (pipe- and newline-escaping for the markdown tables in TEAM.md) was applied to `why` and
`goal` only — `to`/`priority`/`issue`/`actor`/`kind`/`ts`/an ack's `state` were interpolated raw.
`to`/`priority`/`issue` in particular reach `render()` as free text from a sanctioned CLI call
(`handoff.py open ... --to "rae | INJECT ## header"`) with no enum to constrain them the way
`kind`/an ack's `state` do: an embedded `|` split a row into extra columns instead of staying
inside one cell, and an embedded newline landed as a literal line break, letting a hand-off value
inject its own markdown line (e.g. a fake `##` heading) into a file the whole team reads and nobody
hand-edits. Both table-row `.format()` calls in `render()` now wrap every interpolated field in
`_cell()` — including fields that are construction-safe today (`actor` via `_safe_name`, `ts` via
`_stamp`, enum-checked `kind`/`state`) — because `render()` reads entries straight off disk and is
the last line of defense before a value lands in committed TEAM.md; it must not depend on every
upstream caller staying disciplined forever. New tests in `tests/test_ledger.py` cover the issue's
own repro (a `|` in `to`/`priority`/`issue`), the second table's `actor`/`kind`, and a newline-based
line-injection case — all confirmed to fail against the pre-fix code with the exact malformed-row /
injected-line symptom the issue described.

## 1.0.0 — the zero-touch release

The theme: one person on one machine can now point LoopSmith at a stack of their own assigned issues
and walk away — multiple goals drain concurrently in a single session (`parallel.goals`, off by
default), a routine or cron trigger can be configured once without ever double-launching a redundant
session, and `/sdlc-doctor` tells you when a newer LoopSmith has shipped instead of leaving you on a
silently stale install. Underneath, the local coordination primitives this all depends on went through
real adversarial review: two independent review cycles broke two successive hand-rolled file-locking
schemes before landing on a kernel-mediated one with no equivalent race window, and the same
writer-identity fix that makes a routine's fresh invocation stop blindly resuming another session's
in-flight worktree is what makes safe multi-goal dispatch possible at all one layer up.

This release also folds in everything merged from an earlier whole-repo adversarial code review pass
(5 independent author-blind reviewers, 33 findings) that hadn't yet been bundled into a dated release:
secret/client-string egress closed in the risk-detect and alignment-collect collectors and in the
ledger's own entries stream and PR-review comments, and a run of fail-open hardening across
`/sdlc-status`, `/sdlc-doctor`, `loop.py spend`, `pipeline.py`, and the auto-merge gate, so a
malformed config or a transient `gh` failure degrades honestly instead of crashing or reporting a
false pass. As before, everything new here ships opt-in and default-OFF — with no `parallel.goals`
block, no `--session-pid`, and doctor's version check the only always-on addition (a bounded,
read-only check that adds no output at all unless it can actually compare two real version strings) —
so an existing `config.json` behaves exactly as it did on 0.9.23.

### feat(loop): a liveness-safe marker so a routine/cron firing never double-launches a session
LoopSmith doesn't drive scheduling itself (F10.5-4/#377) — that's the host's recurring-trigger feature
(Claude Desktop's "Routines," a cron job calling `claude -p`, whatever fires an agent unattended). It
needs to be SAFE under repeated, possibly-overlapping firings, so a routine can be configured once and
never double-launch a redundant managing session on top of one still running.

`loop.py start` gains an opt-in `--session-pid <pid>` writing `.sdlc/state/session.active`;
`session-active` and `session-end` are new verbs reading and clearing it. `session_pid` must be the
CALLER's own long-lived process id — not any individual `loop.py` invocation's own, which exits within
moments of returning
(confirmed empirically: two separate shell-tool calls in one host session get a different `$$` each
time but the same `$PPID` — the same reason `--skip` exists for multi-slot refills, F10.5-3/#375).
Liveness combines the same two independent signals `ledger._held()` already combines for claim leases
(F10.5/#374), not a new pattern: `ledger.pid_alive()` on the recorded pid (a definitively dead pid
reads stale immediately, no timeout needed to detect a crash — the same reasoning the `flock`-based
claim lock relies on, F10.5-2/#387) AND the marker's own age against `ledger.lease_ttl_seconds` (the
same 12h-default knob the team ledger already exposes, applied unconditionally like `_held()`'s own
TTL cutoff, guarding against pid reuse over long spans rather than a legitimately-long-running run).

README documents the recommended routine/cron prompt template (Claude Desktop, local use): check
`session-active` first and no-op if `ACTIVE`; otherwise `start --session-pid`, run `/sdlc-loop`
exactly as documented (always calling `loop.py next`/`next-batch` for the next goal — the prompt must
never say "continue where you left off," since ambient conversational continuity must never substitute
for a real backlog pick, the exact failure mode this whole arc exists to close), then `session-end`
before exiting.

### feat(doctor): flag an out-of-date loopsmith install, since auto-update is off by default
Investigated what's actually possible before building anything (F10.5-5/#378): auto-update for a
non-Anthropic marketplace like this one is OFF by default (a user has to explicitly opt in via
`/plugin` → Marketplaces, or a team admin via managed settings), and the `version` field in
`plugin.json`/`marketplace.json` is the cache key that gates it even when it IS on — pushing commits
alone never updates anyone. So the real gap is awareness, not a missing update mechanism, exactly as
the plan doc's own hedge anticipated: a stale install can otherwise persist silently indefinitely,
with no signal to the user that anything shipped since they installed.

`/sdlc-doctor` gains a check comparing the installed version against the marketplace's current one.
The installed side reads `claude plugin list --json` (the CLI's own record of what's actually on
disk); the latest side reads the marketplace repo's OWN current `marketplace.json` on its default
branch — a plain, unauthenticated, read-only fetch of a public repo, since no documented Claude Code
API exposes "the latest available version" any other way (confirmed against the plugin-dependencies
and plugins-reference docs). Versions compare as numeric tuples, not strings — "0.9.23" needs to sort
after "0.9.7" numerically, which a lexicographic compare gets backwards. The check adds NO entry at
all — not a pass, not a fail — unless BOTH sides actually resolve, so an offline machine or a
`claude`/`curl` that isn't there degrades to silence, never a false alarm or a false all-clear.

A stronger, platform-native auto-update trigger isn't attempted: it isn't accessible from a plugin's
own code today (no environment variable, no hook input, no CLI subcommand exposes it), and forcing
one open (e.g. shelling out to toggle the user's own marketplace auto-update setting) would be a
correctness/consent question well beyond "ship what's feasible" — noted as a stretch item for later,
not a blocker for this issue or the 1.0.0 release.

### feat(loop): dispatch multiple backlog goals concurrently in one session, not just one goal's slices
The existing `parallel.*` block runs ONE goal's independent implementation slices concurrently — this
is a sibling capability one level up: running MULTIPLE BACKLOG GOALS concurrently in a single session,
each all the way through its own research-through-PR lifecycle in its own worktree+branch. Off by
default (`parallel.goals.enabled`), a stated-scope feature for one person on one machine running many
of their own assigned issues in parallel — not a team coordination mechanism (the ledger + the
writer-identity claim check below already own that).

`loop.py` gains a `next-batch` verb alongside the existing `next`: with goal-level parallelism off, or
only one goal available, it is byte-identical to `next` — exactly one line of output — so a caller can
always use it the same way regardless of configuration. With it on, it returns up to
`parallel.goals.max_concurrent` (default 3) goals in one call, internally accumulating each pick into
its own skip set so a single session's own multiple slots can never collide with each other or the
ledger a second time for the same pass. Also caps a single pass at whatever `budget.max_iterations`
has left, even when `max_concurrent` alone would ask for more — `_budget_spent`'s cursor only advances
when a goal actually COMPLETES, not on a mere pick, so without this a single `next-batch` call could
dispatch more goals in one pass than the run's own budget was ever meant to allow.

Refilling a slot as a subagent finishes needs more than the writer-identity claim check below can
give it: that check can only ever tell whether the SHORT-LIVED `loop.py` process that wrote a claim is
still literally running, and that process exits within moments of writing it, by construction —
regardless of whether the goal itself is still being actively worked by a long-running subagent
minutes later. A goal's `in_progress` status alone does not exclude it from being picked again either.
So `next`/`next-batch` both gain a `--skip <comma-separated goal ids>` flag: the caller (the
orchestrating session, which already knows exactly which goals are live in its other slots, since it
dispatched them) passes the other still-active goals on every refill call. Proved the gap this closes
is real, not hypothetical, before shipping the fix: without `--skip`, a `next` call reliably
re-dispatches a goal a sibling slot already holds.

### fix(loop): two genuinely simultaneous `_next()` calls can no longer pick the same goal
The claim-identity fix above closes correctly INTERPRETING a claim that already exists — it has no
answer for two readers looking at the same instant with nothing claimed yet. `_next()` is a
read-then-decide-then-write sequence: read the ledger, decide a goal looks free, then write a claim.
Two sessions sharing one `.sdlc` directory (two tabs on one machine) whose `_next()` calls land close
enough together — roughly one `gh` API round-trip, not a full minute — can both read the same
pre-claim state and both proceed to the same goal. Not a one-time startup risk: it recurs at every
iteration boundary, any time two sessions ask "what's next" close to the same moment.

`loop.py` gains a local, kernel-mediated exclusive lock (`fcntl.flock(fd, LOCK_EX|LOCK_NB)` on
`.sdlc/state/claims/<goal-stem>.lock`, already covered by the existing wholesale `.sdlc/state/`
gitignore) acquired in `_next()` immediately before committing to a goal and released immediately
after the durable ledger claim lands — the lock's only job is bridging that narrow gap; once a
durable claim exists, the existing writer-aware claim check takes over correctly for every later
call, local or not. Two ordinary-file-operation schemes were tried and each independently broken
across review: a plain exclusive-create-and-recreate let a second racer silently clobber the first's
freshly reclaimed lock; a rename-then-verify-then-restore refinement closed that gap but opened a
narrower one where a third caller could land in the restore step's own window and also win — both
findings came from deterministic, non-mocked reproductions, not reasoning. `flock` has no equivalent
TOCTOU gap: the kernel either grants exclusive access to the open file description or it doesn't,
atomically, with no create/rename/verify/restore sequence for a third party to land inside. It also
retires the staleness-timeout idea those schemes needed: a `flock` is released the instant the
holding process ends for any reason, crash included, so a lock orphaned by a mid-pick crash is never
ambiguous — the very next attempt against it succeeds immediately, no age to guess at. POSIX-only
(`fcntl`); a platform without it (Windows) fails open unconditionally, exactly as if this file didn't
exist, leaving the ledger claim check as the sole (as before this change) defense there. Deliberately
LOCAL-only otherwise: cross-machine claims stay on the existing, already-correct ledger mechanism,
which two different machines don't share a filesystem to race on anyway. Closes a second, downstream
symptom for free: since the losing session never wins the lock, it never returns the contested goal
from `_next()` at all, so it never reaches `work.py start()` for it either — no `git worktree add`
collision on the same deterministic path.

### fix(handoff): a blocked issue's dependency marker is now machine-readable, not comment-only
`hand_off()`'s "link it from the blocked issue" step posted its dependency marker as a GitHub
**comment** — invisible to `backlog_check.py`'s `_explicit_blockers()`, since `mirror.py`'s corpus
fetch is title+body ONLY (comments are never fetched, by design). A goal a hand-off had already,
correctly parked would never auto-skip on a later pick, because the one place that could have told
it "this is blocked" never saw the marker at all. A second, independent bug compounded it: the
comment's own wording said "Blocked **on**", which doesn't match `_BLOCK_RE` either way (it requires
"blocked **by**", among a few other phrasings) — so even reading comments wouldn't have helped
without also fixing the text.

`GitHubSource` gains `append_to_body()` (read-then-append, never overwrite, since `gh issue edit
--body` replaces the whole body) alongside the existing `note()` (comment). `hand_off()` now uses
both: `note()` for the human-visible narrative (now correctly worded "Blocked by"), `append_to_body()`
for a `**Blocked by:** #N` marker in the body — the machine-readable channel `_explicit_blockers()`
actually reads. Each channel is independent and fails open on its own; neither failing blocks the
park. A source without `append_to_body` (predates this fix, or a future non-GitHub source) degrades
to the comment-only behavior, unchanged.

Scope note: this closes the PREVENTION half (every dependency LoopSmith itself records now lands in
the body). It does not add a fallback for a human commenting a dependency directly via the GitHub UI
(bypassing `hand_off()` entirely) — tracked separately, not shipped here.

### fix(loop): two concurrent processes of the SAME actor no longer read each other's claim as "mine to resume"
Reproduces and fixes a real bug hit live: a routine firing a fresh session without an explicit
target resumed a goal a DIFFERENT, still-running session of the same `gh` login had already claimed
and was mid-way through — 18 modified files, silently at risk of corruption from a second, context-less
session touching the same worktree. Traced to a three-part gap: `sources.py`'s `next_pending()` doesn't
filter on the in-progress label; `ledger.py`'s `open_claims()` returned `{goal: actor}` with no
per-process granularity, even though every entry's `id` already carries a pid (`entries/<actor>-<pid>.jsonl`,
`<actor>:<pid>:<seq>`, since the concurrent-writer fix above); and `loop.py`'s `_next()` treated ANY
same-actor claim as automatically "mine, resume it" — two of one person's own concurrent sessions were
structurally indistinguishable.

`ledger.py` gains `open_claims_detailed()` (writer-aware: `{goal: (actor, writer)}`), `my_writer()`
(this process's own `actor:pid` identity), `writer_pid()`, `pid_alive()` (a same-machine liveness
probe, `os.kill(pid, 0)`), and `claim_belongs_to_me()` — the one function both `loop.py`'s `_next()`
and `work.py`'s `start()` now call to decide whether an open claim is safe to treat as mine: my own
current process (resume, unchanged), a legacy pre-fix claim with no pid to check (resume, degenerately
always mine — no regression for the transitional case), a DIFFERENT but still-live writer of mine
(skip — not mine to touch), or a confirmed-dead one (reclaim, faster than waiting out the lease TTL).
Liveness-checking fails toward "assume alive, defer to the existing TTL" on anything it cannot resolve
(a different machine, a platform where signal-0 behaves oddly) — guessing wrong the other way
reintroduces the exact bug this closes. `work.py start()`'s own idempotent resume (correct for a
single crashed session relaunching) now refuses instead of silently reusing a live sibling's worktree.

### fix(ledger): concurrent same-actor loops no longer collide on `id` or lose a hand-off
Several parallel loops resolving to the SAME actor (a shared `gh` login) used to write the same
`entries/<actor>.jsonl` file — two concurrent appends both read `_line_count` as 0 and both minted
`<actor>:1`, corrupting the monotonic-per-author sequence the watcher-resume cursor and
`open_claims`'s lease rely on. `entry_file()` now mints one file per WRITING PROCESS
(`entries/<actor>-<pid>.jsonl`), and `id` itself now carries the pid too (`<actor>:<pid>:<seq>`) —
every existing consumer takes the id's last `:`-segment or prints it whole, so this is
backward-compatible by construction.

The filename fix alone was not enough: `watch_classify.py`'s cursor tracked one high-water seq PER
ACTOR, so two writers sharing a login would merge their independent per-file counters into one
baseline — a second, slower-counting writer's still-new entries could be silently swallowed forever
by a faster writer's higher seq, not just delayed. `classify()` now keys its cursor by a new
`_writer()` (actor+pid, falling back to bare actor for pre-fix 2-part ids) instead of by actor alone.

Neither fix alone was complete: `sync.py`'s `publish()`/`bootstrap()` hardcoded the OLD
single-file-per-actor path directly, bypassing `entry_file()` — so the pid-suffixed rename by itself
would have made `publish()` find nothing to stage and silently stop publishing every actor's ledger
entries to the shared branch. Both now go through a new `ledger.files_for()`, which finds every file
(any pid) an actor has written, including a legacy bare `<actor>.jsonl` left over from before this fix.

Blast radius beyond this plugin: `insight/ingest/ledger_writer.py` (a separate BUSL-1.1 product in
this repo that reimplements ledger reading rather than importing it) has its own resume cursor keyed
`(project_id, actor_id)`, which assumed one monotonic seq space per actor — no longer true once one
actor's records can come from multiple independent-pid writers. `_CURSOR_UPSERT_SQL` is now
`GREATEST`-based instead of a plain overwrite, closing the worse failure mode (the cursor regressing
backward and causing an already-landed record to be silently re-inserted as a duplicate — `fact_event`
has no dedup constraint). This is a mitigation, not the full fix: a genuinely new record from a
still-active writer whose own counter hasn't caught up to another writer's peak can still be silently
skipped. The full fix needs the cursor keyed on `(project_id, actor_id, writer_id)`, which needs an
actual schema migration (`store.py`'s additive-only `ALTER TABLE ... ADD COLUMN` mechanism can't widen
a primary key) — tracked separately as loopsmith#380; see `ledger_writer.py`'s own "KNOWN LIMITATION,
TRACKED" docstring section for the full accounting.

### fix(slices): the same file spelled two ways now correctly conflicts
`slices.conflicts` compared declared file paths purely lexically (`fnmatch` + a literal-prefix
check), so `engine/graph.py` and `./engine/graph.py` (or a `\`-separated spelling, or a redundant
`//`) read as disjoint. Two slices declaring the same file under different spellings then land in
the same wave and get dispatched as concurrent worktree subagents editing the same real file — a
merge conflict at best, a silently dropped edit at worst, exactly what the declared-files manifest
exists to prevent. Normalizes every path (`posixpath.normpath` after unifying separators) before the
overlap/prefix comparison in `_overlap`, the one function both directions of `conflicts()` already
funnel through.

### fix(loop): the overnight drain no longer aborts (and re-picks the goal) on a transient `gh` error
`sources.py`'s `GitHubSource` had four unguarded `gh` calls on the source-op path — `next_pending`'s
`issue list` (called FIRST, every iteration), `mark_in_progress`'s `--add-label`, `complete`'s
`issue close`, and `_offboard`'s `issue comment` (posted BEFORE the goal-label removal that actually
de-lists a parked/failed goal). A transient 502/rate-limit on any of these propagated uncaught through
`_record`/`_next` and crashed `run_loop`'s whole `while` loop — `run_loop([a,b,c])` with `b` erroring
processed only `a`, and since `_offboard` never reached the label-removal line, `b`'s `sdlc:goal` label
was never dropped, so the NEXT run re-served the same goal. Fixes, in order of what they protect:
- `_offboard` now removes the goal label FIRST, unconditionally attempted, before the (still
  best-effort) parked-label add and comment — de-listing no longer depends on anything after it
  succeeding.
- `mark_in_progress` and `next_pending`'s `gh` calls are now individually guarded — a best-effort
  visibility label failing doesn't stop a goal being picked, and an unreadable backlog degrades to
  "nothing pending" (loud on stderr) instead of crashing before a single goal is even read.
- `run_loop` wraps `_record` itself: if `complete()`'s `issue close` still raises (deliberately left
  unguarded at the source, since silently swallowing THIS one risks claiming "done" while the remote
  issue stays open and re-pickable), the goal is downgraded to a recorded PARK — never a silent "done"
  — and the drain continues to the next goal instead of aborting.
- POST-REVIEW FIX: if BOTH the primary record AND the fallback park-record fail for the same goal,
  `run_loop` didn't crash and didn't lose the goal — it spun on it forever (an independent review
  reproduced this as an unbounded, ~100%-CPU hang), silently defeating `max_iterations`. Such a goal
  is now poisoned for the rest of THIS run only (reusing `_next`'s existing lease-skip mechanism), so
  the drain still makes bounded progress on the rest of the backlog; a fresh run may retry it.

### fix(loop): `spend` refuses a non-integer token count with a message, not a traceback
`loop.py spend <dir> <n>` — the CLI verb hosts report token usage through — did `int(n)` unguarded
inside `state.add_tokens`. A float, empty, comma-grouped, or garbage `n` raised `ValueError`, exiting
with a raw traceback (exit 1) instead of the usable, exit-2 refusal every other invalid-input path in
this command already gives (the sibling `--flag` validation a few lines below it, for one). Validates
`argv[3]` as an integer at the CLI dispatch site — the one and only caller of `add_tokens` — before
ever touching state, so a rejected call never partially mutates `run_tokens` either.

### fix(pipeline): a malformed `pipeline.json` is treated as absent, never a traceback
`load_pipeline()` did `json.loads(...)` unguarded and then `spec.get("stages")` with no dict check —
an invalid-JSON `pipeline.json` raised `JSONDecodeError`, and a top-level array/scalar raised
`AttributeError` on the `.get()`, both propagating out of the `card`/`propose` CLI verbs as a raw
traceback. The sibling `discover` verb already catches broadly and `slices.load` raises a named
error for the same class of input — `load_pipeline` was the one reader in this family that didn't
degrade. Now wraps the parse in `try/except ValueError` (`JSONDecodeError` is a `ValueError`) and
requires `isinstance(spec, dict)` before ever calling `.get()` on it, returning the same `None` "no
pipeline" sentinel a missing file already uses — so `card`/`propose` fall into their existing
`NO-PIPELINE` (exit 3) path instead of crashing.

### fix(work): `gate()` fails closed instead of crashing on a raising `gh pr view`
Unlike `merge_rights` (fails closed) and `review_gate` (fails open), a transient `gh pr view` error —
a 403, a rate-limit, a network blip — inside `gate()`'s mergeability read propagated straight out of
`merge()` uncaught: exit 1, no gate ledger event, instead of parking with a reason like every other
gate() verdict. Folds the read into the existing UNKNOWN-retry loop (a raising read gets the same
retry budget a lazy-UNKNOWN read already gets — GitHub's own transient errors are exactly the kind of
blip a second attempt often clears) and, only if every attempt still raises, returns a park verdict
(`could not read PR state (…)`) instead of propagating.

### fix(doctor): a shared `_block()` helper so a malformed config block never crashes check/features
A config with a shape typo — e.g. `{"verify": "pytest"}` where a `{"command": ..., "enforce": ...}`
block was meant — made both `doctor.check()` and `doctor.features()` raise `AttributeError` on the
next `.get()` call, aborting the ENTIRE run. Only `telemetry` and `backlog_check` had the
`isinstance(..., dict)` guard; every other block reader (`verify`/`work`/`ledger`/`discovery`/`gates`
and its nested `hard_plan_gate`/`stop_gate`/`decision_gate`/`review`/`budget`/`parallel`/
`session_start`/`knowledge_graph`, plus the github/project board helpers) used the bare
`(cfg.get("x") or {}).get(...)` idiom a non-empty non-dict value slips past. Adds a shared `_block(cfg,
name)` helper — degrades a non-dict block (or a non-dict `cfg` itself, so it composes safely at any
nesting depth) to `{}`, used by every reader in the file — plus guards `_cfg()` against a non-dict
top-level `config.json` (a bare JSON array/string/number). This is the one tool an adopter runs
*because* their config is wrong, so it has to survive exactly the malformed input that brought them
there. Parametrized regression tests cover every declared block × 4 malformed shapes (string, list,
int, bool), plus the issue's exact repro and two nested-block cases.

### fix(status): survive a truncated / invalid-UTF-8 goal, state, or ledger file
`/sdlc-status`'s readers called `read_text()` without `errors=`, and the two that had a `try/except`
caught only `OSError` — but a process killed mid-append truncates a multi-byte UTF-8 sequence, and the
resulting `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so it sailed past the catch and took
the whole dashboard down. `doctor.py`'s `_count_jsonl_lines` already documents + defends this with
`errors="replace"`; status's five readers (goal frontmatter, `STATE.md`, `review-queue.md`, the last
alignment report, and ledger entries) hadn't been hardened the same way. Mirrors that fix across all
five, so `summary()` now produces counts instead of crashing on a truncated file anywhere in its
sources — proven with a case that plants invalid UTF-8 in all five sources at once.

### fix(collectors): unify the two bash collectors' secret-pattern sets (F29)
`risk-detect.sh` and `alignment-collect.sh` each hard-stop on a secret-shaped line, but the two pattern
sets had drifted apart: `alignment-collect.sh` alone caught Slack tokens (`xox…`) and `AWS_SECRET_ACCESS_KEY`
as a key:value trigger; `risk-detect.sh` alone caught `access_token` as a trigger; both missed GitLab
(`glpat-…`) and Google (`AIza…`) token shapes. Both scripts scan diff bodies to CLASSIFY (emit a location,
never the value), so the drift was a detection gap, not a leak — but the two location-only collectors
should carry the same set. Unified both to the union of every shape either one had, plus the two missing
ones, and added a parity test (`tests/test_risk_detect.py`) that extracts and compares the literal pattern
alternation from both `.sh` files' source, so the two can't silently diverge again — the same pattern the
repo already uses for `scrub.py` ↔ `research_capture.py`.

### fix(work): scrub the post-PR review reason before it reaches the public PR comment
`work.py post_review`'s block/park comment embedded the free-text `--reason` verbatim into the `gh pr
comment` body with no scrubbing, while the *same* reason IS scrubbed on the ledger-event path — an
oversight, not a decision. A review note quoting a secret- or client-shaped string from the diff was
published to a public PR comment. Now runs `reason` through the shared `scrub.py` (same module the board
mirror and backlog cross-check already use) before building the comment body, for both the normal-block and
over-cap-park messages.

### fix(ledger): scrub + flatten + cap the ENTRIES-stream `why`, not just EVENTS
#141 scoped cap+scrub to the EVENTS stream only, on the theory the ENTRIES stream's own `why`
(hand-offs/notes a lead reads in TEAM.md) was out of that story's scope. It wasn't safe to leave
unscrubbed: `why` is committed byte-for-byte to the shared `sdlc-ledger` branch AND rendered into
TEAM.md, so a sanctioned `handoff.py open ... --why "<secret>"` landed a secret/client string in
version control. `append()` now runs the ENTRIES stream's `why` through the same `_sanitize_free_text`
(flatten→scrub→cap) helper EVENTS' free-text fields already use — the one prose field in
`OPTIONAL_FIELDS` (`area`/`to`/`issue`/`priority`/`state`/`ref`/`pr` are all short enums/ids). `render()`
needed no change: it already displays whatever `why` was stored, so a scrubbed write is a scrubbed
TEAM.md row for free.

### fix(risk-detect): close the secret-leak twin of the alignment-collect `+++`-misparse bug
`risk-detect.sh`'s content-scan awk had no hunk-state (`inhunk`) tracking, so a committed/working-tree
line whose source began `++ ` rendered `+++ ` in the diff and was misparsed as a file header — capturing
the secret text itself into the emitted `file` field (also swallowing the line as a false negative, and
poisoning `file`/line numbers for later hits in the same file). This is the exact bug already fixed in
`alignment-collect.sh` (`scan_hardstops`, slice #89) but never back-ported to `risk-detect.sh`. Ports the
same `inhunk` state machine (reset on `diff --git`, set on `@@`, header rule gated on `!inhunk`); the
synthesized untracked-file diff block now also carries a `diff --git` line so the reset fires on that path
too. New regression tests cover both the tracked-diff and untracked-file shapes, and are proven non-vacuous
(fail against the pre-fix code, pass against the fix).

### backlog cross-check: an opt-in embedding layer for paraphrases (0.9.23)
Fourth and final slice. The lexical TF-IDF pass (0.9.21) misses a duplicate that shares no *words* — a
goal described in entirely different vocabulary. This adds an **opt-in dense/embedding channel** fused
with the lexical score, plus a **BM25** lexical option, in `backlog_check.py`. **Both off by default: with
`embed.enabled:false` and `similarity:"tfidf"` the engine is byte-identical to 0.9.22** (the default path
reduces line-for-line to the old lexical scoring).
- **Hybrid retrieval.** `embed.enabled:true` + `embed.command` (a provider-agnostic embedder that reads
  text on stdin and prints a JSON number array — no hardcoded vendor) adds a dense channel; the fused
  score is `max(lexical, embed.weight · dense_cosine)`, so a zero-lexical-overlap paraphrase still surfaces
  via the dense channel while lexical matches are unaffected.
- **Token-free at query time.** Vectors are cached in gitignored `.sdlc/state/embeddings.json`, keyed by a
  content hash and computed **incrementally** — only new/changed texts are ever embedded — so a run
  re-embeds only deltas and the query itself is plain cosine over cached vectors.
- **`similarity: "bm25"`** (opt-in) swaps TF-IDF for BM25-weighted cosine (saturation + length
  normalization — the stronger bug-dedup baseline); `"tfidf"` stays the default.
- **Fail-open + secret-safe, unchanged:** no `embed.command` / an embedder error / a read-only `.sdlc` →
  the dense layer silently falls back to lexical-only (`degraded:['no_embedder']`), never a raise. The
  embedder is injectable, so tests are hermetic (a stub vector map, no network). `/sdlc-doctor` flags
  `embed.enabled` with no `embed.command`. Config documents `similarity` + `embed`.

### backlog cross-check: wire it into the loop, off by default (0.9.22)
Third slice — the pre-work cross-check (0.9.20 mirror + 0.9.21 engine) now actually runs in `/sdlc-loop`,
**opt-in and off by default**. New `loop.py precheck <sdlc_dir> <goal>` verb + a step at the very top of
the loop body (before any token spend): when `backlog_check.enabled` is true it refreshes the board
mirror and cross-checks the just-picked goal, then per `backlog_check.action`:
- **`park` (default):** a CONFIDENT duplicate / obsoleted-by / blocked-by finding is **parked-with-proof**
  — a comment carrying the evidence (refs + scores + scrubbed shared terms), then the loop advances to the
  next goal. It **never stalls** on a broken goal and **never closes** an issue (a human does that).
- **weak finding:** annotate-and-proceed (a `flag`-mode config forces this for every finding).
- Prints `OFF` / `PARKED <reason>` / `PROCEED[ (advisory)]` — the SKILL reads the first word. **Fail-open:**
  any error → `PROCEED` (the cross-check can never block or crash the loop). A parked goal advances the
  per-run iteration cursor like any other outcome.
- `backlog_check` block added to the scaffolded config (default `enabled:false`, documenting every knob);
  `/sdlc-doctor` reports the feature state and flags `park_threshold < dup_threshold` (which would park
  every candidate). README "What you get" + "Feature flags" rows. Installing changes nothing until opted in.
- The decision is a pure, tested `backlog_check.decide(pack, config)`; the loop hook only executes it.

### backlog cross-check engine: catch a redundant goal before the loop works it (0.9.21)
Second slice of the pre-work backlog cross-check (the mirror was 0.9.20). `backlog_check.py` (+
`pipeline.py crosscheck <sdlc_dir> <goal>`) takes a just-picked goal and surfaces likely
**duplicates / blockers / obsolete-by-completed-work** against the rest of the backlog + in-flight team
work — so the loop doesn't spend a full Research/Plan cycle on work that's redundant, blocked, or already
done. It EMITS EVIDENCE and renders no verdict; a later slice's hook decides what to do with it.
- **Zero LLM tokens.** A stdlib TF-IDF cosine (title weighted 3×) over a candidate set of issues that
  share ≥1 term with the goal (rarity then drives the score via idf) — exact at a few-hundred-issue
  scale, so no MinHash/LSH and no numpy. Plus an
  explicit `blocked by … #N` graph (high precision: only when N is a real OPEN issue) and the team
  ledger (`open_claims` → a paraphrase a teammate is already working; an outstanding hand-off → a
  recorded blocker). The lexical index is cheap to rebuild each run, so nothing is persisted; the vector
  cache that needs content-hash incrementality arrives with the embedding layer.
- **Velocity-scaled obsolescence window** (`backlog_check.closed_window_days: "auto"`): "recently closed"
  tracks the repo's real merge pace via `velocity.py`, falling back to 90d on a fresh/non-git repo.
- **Two corpora, one engine:** github mode reads the gitignored board mirror; local mode reads the goal
  files (`status: done` = completed work that can obsolete a goal).
- **Secret-safe:** findings carry issue refs + shared index TERMS (scrubbed, capped) — never a
  title/body/secret; source text is scrubbed before tokenizing so a lowercased secret can't reach
  `evidence`. **Fail-open:** no corpus / no git / any error → an empty `backlog-check/v1` pack with a
  machine `degraded[]`, never a raise (a later slice calls this on the pick hot-path). Deterministic.

### board mirror: a token-free local snapshot of the backlog (0.9.20)
First slice of the **pre-work backlog cross-check** — the loop today picks a goal blind to the rest of
the backlog and to what's already done, so it can spend a full Research/Plan cycle on a logical
duplicate, an unreferenced-blocked goal, or one already obsoleted by completed work. This lays the
corpus the check needs. `skills/sdlc-loop/scripts/mirror.py` (+ `pipeline.py mirror <sdlc_dir>`) makes
**one** batched `gh issue list` for OPEN `sdlc:goal` issues and **one** for recently-closed issues, and
writes a normalized NDJSON snapshot to `.sdlc/state/board-mirror.ndjson`.
- **Zero LLM tokens.** A plain REST snapshot (works where cloud sessions block GraphQL), TTL-cached
  (`backlog_check.mirror.ttl_minutes`, default 60) so the one API call is amortized across picks.
- **Secret-safe + gitignored.** `.sdlc/state/` is already gitignored, so the mirror never rides a PR;
  body excerpts are additionally scrubbed of secret-shaped substrings (shared `scrub.py`, the same rule
  the research-capture hook follows, with a parity test) — defense in depth against client strings.
- **Fail-open + hermetic.** Not github mode / no `gh` / offline / bad config / unwritable `.sdlc` / any
  error => no mirror written, returns None (the cross-check degrades to the ledger + local goal files).
  Reaches GitHub only through an injectable runner, so it is fully unit-tested without the network.
  Closed issues are NOT goal-filtered (prior completed work can obsolete a goal without ever carrying
  the label).

### telemetry: the loop records what its own gates caught (0.9.19, opt-in)
Epic #135, seven stories. Everything the SDLC spine already computed and then discarded is now an
optional, append-only event stream — `work.py` counted review cycles to enforce `max_review_cycles`
and threw the count away; `verify_goal` persisted evidence the next run overwrote; decision-gate
denials happened and vanished.

`ledger.py` gains a second **stream** (`ledger/events/<actor>.jsonl`) on the transport the team
ledger already uses — same per-author files, same fail-open append, same publish/watch path — and
`render()` still reads `entries/` only, so `TEAM.md` cannot be drowned by telemetry **by
construction**. Eight deterministic emitters (verify, park, the merge/review gates, decision-gate
denials, slice dispatch, discovery-scan) plus a `loop.py emit` verb for the phase/gate/retro/spend
events only the agent can observe — deliberately NOT `verify`, so an agent cannot emit a passing
verify record it did not run.

Prose is scrubbed and capped **once**, in `append`, where all nine call sites already pass through:
`why` truncates at 200 chars, secret-shaped substrings never survive, and a newline cannot forge a
second record. Events carry identifiers, counts, durations and verdicts — never code, never diffs.

`"telemetry": {"enabled": false}` by default: installing changes nothing, and the flag's own comment
says so rather than leaving an adopter to wonder why no files appear.

### work: refuse to merge a PR head that is not what was reviewed (0.9.18)
`work.py commit` is LOCAL; only `work.py pr` pushes. After a `loopsmith:block`, a fix committed and
re-reviewed in the worktree could leave the PR head at the PRE-FIX commit — and because every GitHub
answer is about the REMOTE head, `mergeable`, `mergeStateStatus` and all required checks then passed
**correctly** about code nobody approved, letting an armed auto-merge squash it. A green check on a
head you did not review is indistinguishable from a real pass.

Observed **three times in one autonomous run**; two shipped defects to a protected `main` past four
green checks (one of them five wrong metric views, whose recovery PR then sat conflicting for 15
hours). `gate()` now reads `headRefOid` and compares it to the worktree's own tip **first** — before
any GitHub verdict is believed, since with a stale head `CONFLICTING`/`BEHIND`/failing-check are all
answers about the wrong tree — and **fails closed** when either head is unreadable. `/sdlc-loop`'s
fix cycle now says to push, which it never did.

### alignment-collect: the cumulative-drift audit runs on evidence, not recall (0.9.17)
`sdlc-align` (the window-level drift check) was judgment-only prose — the model re-derived the facts from
the git log each time. `skills/sdlc-align/scripts/alignment-collect.sh` grounds it: a read-only, jq-free,
deterministic collector that gathers FACTS over an N-day window (`--since-days N`) into one evidence pack
(`schema:"alignment-collect/v1"`) — `window`, `commits[]`, `degraded[]`, and seven dimensions (per-commit
source/test/doc classification, plan-adherence + churn hotspots, whether the project documents how it
verifies, review-artifact presence, decision-registry changes, and hard-stop flags). It **renders no
verdict** — `sdlc-align` still judges; it just reasons from measured facts now.
- **Secret-safe:** the hard-stop scan reads diff bodies but emits ONLY `{commit,file,line,pattern_id}` —
  never the matched substring (a committed `password = "…"` surfaces as a location, never the value).
- **Fail-open:** missing dep / non-git / unparseable → a valid **minimal** pack with a machine-readable
  `degraded[]` code (`no_git` / `no_recognized_source` / `no_test_command`), exit 0.
- Retargeted to LoopSmith conventions: `.sdlc/plans/`, `verify.command`, `.sdlc/reviews/`,
  `.sdlc/decisions.json`; excludes `.sdlc/knowledge/**` (machine-accumulated) from the commit walk.

### The decision gate was invisible to the people it shipped for (0.9.16)
`gates.decision_gate` was read by the hook, reported by `/sdlc-doctor`, and documented in
`/sdlc-decide` — and absent from `.sdlc/config.json`, the one file every adopter opens to learn what
this kit does. A feature you have to already know about in order to find is a feature that didn't ship.
- The template now carries a `_decision_gate` entry, and it explains the thing that would otherwise
  read as broken: **there is no `enabled: true` to set.** Authoring `.sdlc/decisions.json` *is* the
  opt-in. Sitting between two gates that do take that flag, silence invites the natural guess — add
  `enabled: true`, watch nothing happen, conclude the gate doesn't work. It also documents the **off**
  switch, because "how do I turn this off" is what you search for while it's blocking you, and the
  config file is where you'll be looking.
- **A guard for the whole bug family, not just this instance.** Every `gates.*` key read anywhere under
  `skills/*/scripts/` or `hooks/` must appear in the scaffolded template. This is the third time the
  two halves of a feature were built by different changes and nothing checked they met: `lane: auto`
  (scaffolded, nothing read it), the plan file (`hard_plan_gate` gated on a path the Plan phase was
  never told to write), and now this. The guard also fails if its own detection idiom goes stale, so it
  can't quietly pass forever after a refactor changes how gates are read.
### discovery-scan: propose backlog goals from the debt already in the repo (0.9.15)
The radar surfaces what's new *outside*; this surfaces the debt already *inside*. `discovery-scan.sh` is a
read-only, jq-free, deterministic collector that greps tracked source for two mechanical signals —
`TODO/FIXME/HACK/XXX` clusters (tech-debt) and skipped/xfail tests (test-gap) — aggregated per file.
- **`pipeline.py discover <sdlc_dir> [repo_root]`** turns each candidate into a **`proposed`** goal file
  (`status: proposed`, `source: discovery`), the same inert contract as `pipeline propose`: the loop
  **never runs a `proposed` goal** until a human promotes it to `pending`. The dedup id is per
  (category, file) — a changed marker count never spawns a duplicate. Surfaced from `/sdlc-radar` as its
  internal-supply step.
- **Secret-safe:** a candidate carries the marker **location + count**, never the comment text (a `TODO`
  can contain a secret). **Fail-open:** no git / non-repo → no candidates, exit 0. Excludes `.sdlc/**`,
  `docs/**`, and common vendor dirs.

### Skill-selection guidance vs platform built-ins (0.9.14)
Documents, honestly, how LoopSmith's skills win selection against platform built-ins — and what a plugin
*can't* do. A spike against the current Claude Code docs confirmed: **a marketplace plugin cannot disable
or de-prioritize another skill** (no manifest field), `skillOverrides` explicitly does **not** affect
plugin skills, and there is **no runtime API** to detect which skills are active. So there's no mechanism
to build — the deliverable is guidance:
- **`/sdlc-doctor`** gains a `skill selection vs platform built-ins` advisory row: LoopSmith prefers its
  own skills via sharp descriptions + per-skill resolution headers, and if a built-in shadows one, the fix
  is user-side — `skillOverrides` for a standalone built-in, `/plugin disable` for a plugin one. (It's an
  advisory, not a detected conflict — no API exists to detect one.)
- The dashboard also now lists the **Stop gate** and **SessionStart** opt-ins added in 0.9.12–0.9.13.
- README gains a "Skill selection vs platform built-ins" section with the same guidance.

### SessionStart policy brief (0.9.13, opt-in)
An OPT-IN SessionStart hook (`hooks/session_start.sh`) that injects a short SDLC policy brief — the loop's
phases, `/sdlc-loop` vs `/sdlc-goal`, ground-in-the-north-star, plan-before-source, reviewer-≠-author — as
`additionalContext` at the start of a session, so the conventions are in context before the first prompt
(the UserPromptSubmit gate only fires once the user types). It also runs a **doctor-lite install
self-check** that warns (never blocks) on a half-set-up adoption — e.g. a missing north-star points at
`/sdlc-vision`. **Off by default:** silent unless `.sdlc/` exists AND `session_start.enabled: true`; a repo
that never adopted LoopSmith is completely untouched. Fail-open on no python3 / no config; strict
`enabled is true` (a string `"true"` does not enable). jq-free.

### Interactive Stop gate — don't end a session with unplanned source changes (0.9.12, opt-in)
An OPT-IN Stop hook (`hooks/completion_gate.sh`), the Stop-time counterpart to the PreToolUse
`hard_plan_gate`. When enabled, it refuses to let the agent stop while SOURCE files changed in the working
tree but no fresh plan sits under `.sdlc/plans/` — so an interactive `/sdlc-goal` session can't quietly end
with unplanned work (the loop's own record step is already guarded by `state.done_refusal()`; a hand-driven
session was not). **Off by default** — absent/omitted config = allow, so installing it changes nothing until:
`.sdlc/config.json → {"gates": {"stop_gate": {"enabled": true, "plan_freshness_hours": 24}}}`.
- **Fail-open** on no python3 / no git / unreadable input / missing config → allow (exit 0). Escape hatch:
  `touch .sdlc/.allow-direct-edits`. Excludes `.sdlc/**` and `docs/**` (harness, not source).
- **Loop-safe:** the guard honors both the classic `stop_hook_active` flag and the newer `recursive_state`
  shape, so a block can never fire twice in a row regardless of host runtime. jq-free (config via python3).

### risk-detect: auto-surface the right risk review from the diff (0.9.11)
The 0.9.10 risk skills only help if someone remembers to run the right one. `risk-detect.sh` closes that
gap — a read-only, jq-free, deterministic collector that scans the current change (working tree + staged +
untracked) and names which conditional-risk categories it touches: `migration` / `contract` / `sensitive`,
each mapping to a Slice-2 skill.
- **Wired into the loop's Review phase** (`sdlc-review` SKILL prose): a matched category auto-surfaces "run
  `/sdlc-<risk>-check`". **Anticipated at Research** (`sdlc-research`): if the blast radius touches a risk
  surface, the dossier flags it so the Plan budgets for the review. It's a bash trigger (zero LLM cost); it
  names the risk, the skill judges it.
- **Secret-safe by construction:** the content scan reads diff bodies to classify lines but emits ONLY
  `{category,file,line,pattern_id}` — never the matched substring. **Fail-open:** no git / non-repo →
  `{"schema":"risk-detect/v1","matched":[],"hits":[]}`, exit 0. Excludes `.sdlc/**` and `docs/**` (the
  harness, not the engineer's source). Optional glob overrides via `.sdlc/risk-detect.conf`.

### Conditional-risk review skills (0.9.10)
The 7-phase spine reviews code *quality* (`sdlc-review`), but never asks "does this touch auth/PII, break
a public contract, need a migration rollback, is it safe to ship." Five new skills fill that orthogonal
gap — each LoopSmith's own (no platform companion), invoked only when a change trips its risk, so there's
zero cost until one runs:
- **`/sdlc-security-review`** — threat-model a change to auth / user data / external inputs / a public
  endpoint (eight-point checklist, severities + remediations).
- **`/sdlc-contract-check`** — detect breaking changes to public APIs / event shapes / exported types
  before consumers hit them (classify + name every affected consumer by grep).
- **`/sdlc-migration-check`** — forward path, backfill, rollback, and canary for any DB schema/data change.
- **`/sdlc-release-check`** — the eleven-point pre-flight checklist + go/no-go (the loop still never ships;
  this produces the artifact a human release captain reads).
- **`/sdlc-debug`** — hypothesis-first, reproduce-as-a-test-first bug diagnosis before any fix.

Each renders a structured report and persists to `.sdlc/reviews/` — deliberately NOT under the
`.sdlc/knowledge/` tree gitignored in 0.9.8, so a review stays visible to its PR. (Auto-surfacing these
from a diff-risk detector lands next.)

### Research-capture scrub hardening — follow-ups from the 0.9.8 review (0.9.9)
Two refinements to the 0.9.8 secret scrub, from the independent review of that PR:
- **The subject is scrubbed too, not just the excerpt.** A credential in a URL query param (a pre-signed
  S3 URL's `?X-Amz-Credential=AKIA…`) or pasted into a WebSearch query used to land verbatim in the
  breadcrumb's `subject:` frontmatter, its `# heading`, and the slugified filename. The subject now runs
  through the same redactor before any of those are built.
- **`token` no longer over-redacts prose.** It was in the auth-keyword rule, so ordinary phrases in the
  captured research ("token management", "token economics") collapsed to `[REDACTED:auth]`. Bare `token`
  moved to the `key: value` rule, so a real `token: <value>` assignment is still redacted while the word
  itself survives.

### Research capture no longer dumps raw web bodies to a git-tracked dir (0.9.8)
A security fix in the opt-in knowledge-graph capture path. The `research_capture` PostToolUse hook wrote
the **raw** WebSearch/WebFetch response — the first 4000 verbatim chars — into
`.sdlc/knowledge/research/web/`, a directory nothing gitignored. A page carrying an API key, token, or
PII therefore landed on disk and could ride into a commit. Two defenses, both additive (the feature is
off unless `knowledge_graph.enabled`):
- **The breadcrumb is now a scrubbed summary, not the page.** The hook keeps source + subject + a SHORT
  excerpt (`_EXCERPT_CHARS`, 400) that is first run through a secret-shaped-substring redactor modelled on
  the risk-detect collector's rule — *never write the matched substring*. AWS keys, `gh*_` tokens, JWTs,
  PEM private-key blocks, `Bearer` tokens, and `key: value` secret assignments become typed `[REDACTED:*]`
  placeholders (quote-insensitive, so they fire inside JSON responses too). Best-effort by design, which
  is why —
- **`.sdlc/knowledge/` is now a runtime-ignored dir.** `/sdlc-setup` (and `setup.py ignore`) add it to the
  same never-clobber ignore set as `state/`, `ledger/`, `work/`, so even a scrubbed breadcrumb stays local
  unless the adopter deliberately commits it. `/sdlc-doctor` and `setup.py ignore-status` report it.

### Board-adoption safety + portability: four silent traps made loud (0.9.7)
The theme of the whole 0.9.x adoption series — *an adopter's incomplete config met with a loud warning,
not silence* — applied to four more real gaps found running loopsmith on a live board.
- **A misconfigured board no longer spawns a silent DUPLICATE.** `_ensure_board` used to `project create`
  a new `"<repo> — SDLC"` board whenever it couldn't resolve one — so `project.enabled` on, no
  `project.number` pinned, and a real board whose title differs from that default meant the loop quietly
  created a *second* board and managed the wrong one (no error, everything after it "succeeds"). Now it
  **only auto-creates when the owner has no board at all** (an unambiguous fresh setup); otherwise it warns
  loudly and leaves mirroring off (fail-open — issues + labels still work). `/sdlc-doctor` flags the risk at
  setup — **not gated on `number` already being set**, so it catches exactly the manual-config path that hits it.
- **A missing `project` scope is no longer fail-*silent*.** Board writes still fail open, but the first time
  one fails for a missing `project` token scope the loop prints a loud one-time note (`gh auth refresh -s
  project`) instead of silently not moving cards — a transient blip stays silent as before.
- **`/sdlc-status` counts the real backlog in github mode.** It read the (empty-in-github-mode) `.sdlc/goals`
  dir, so a github-backed loop reported `0 parked` while N issues sat parked. It now counts open issues by
  label (`sdlc:parked` / `sdlc:in-progress` / `sdlc:goal`), scoped to the loop's own `assignee`. Fail-open to
  zeros when `gh` is unreachable; local mode is byte-identical.
- **Windows / non-UTF-8 consoles stop garbling output.** The plugin's own non-ASCII output (arrows,
  em-dashes) was mangled to `?` or crashed on a cp1252 console. The main output scripts (`loop`, `work`,
  `doctor`, `ledger`, `sync`) now force UTF-8 stdout/stderr at import — idempotent, fail-safe, a stream
  without `reconfigure` is left as-is.

### Loop-created issues stop being silently blank on a board's custom fields (0.9.6)
- **The gap:** the only path that autonomously creates an issue mid-run — `handoff.py` opening a
  cross-area dependency — set labels, an assignee, and the built-in `Status` field, and *nothing else*.
  On a board with any custom Projects-v2 single-select field (a `Priority`, a `Section`, an effort
  estimate), a loop-created issue came out **quietly inconsistent with every human-made issue** — no
  error, no park, just wrong data a team eventually stops trusting the board over. (And a `priority:<n>`
  *label* is a different mechanism from a Projects-v2 `Priority` *field* — easy to think it's covered.)
- **`discovery.github.project.custom_fields`** (default `{}`) maps a custom single-select field name to
  an option name, e.g. `{"Priority": "Medium", "Section": "Task"}`; `create_dependency` stamps them on
  the issue it opens (mirrors how `project.columns` maps `Status`). A field the board lacks or a value
  that isn't one of its options is skipped, never guessed. Fail-open; empty `{}` = the prior behavior.
- **`/sdlc-doctor` enumerates the board's real fields** and flags any single-select field beyond `Status`
  that isn't mapped — turning a silent, discovered-months-later data-quality bug into a one-time setup
  warning (the pattern the ledger/verify-trap checks already use). Reads the board fields live; a
  can't-read (no `project` scope, no board yet) reports nothing rather than a false all-clear.

### The maker is never the checker — independent, project-informed review across every gate (0.9.5)
- **Every review gate now runs as a fresh, author-blind subagent.** Plan-review ran *inline in the
  context that wrote the plan* (zero separation, the highest-leverage gate); code review only got a
  fresh reviewer on the companion path; the post-PR review was "best run as a subagent" (a suggestion);
  and `model_selection: auto` **buried the whole goal in one subagent**, so every phase shared one mind.
  A maker reviewing its own work rationalizes rather than refutes — on a lower tier that amplifies
  hallucination exactly where the review should catch it.
- **`review_context.py` assembles the reviewer's brief** (`brief .sdlc <goal> --for
  plan-review|code-review|pr-review`): the north-star + conventions + contracts + the goal + a pointer
  to the artifact — the PROJECT, never the maker's transcript. The maker context is excluded *by
  construction*, so the reviewer re-derives blast radius from the whole repo and can disagree. A
  diff-only reviewer can't see what a small change breaks two files away; the whole-repo grounding is
  the point. Fail-open, ASCII-only, zero-dep.
- **`config.review.independent` (default on).** `/sdlc-loop` dispatches one fresh subagent **per phase**
  (not one per goal) so a reviewer is a *sibling* of the maker it checks — plan-review, code review, the
  post-PR review (which MUST run author-blind), and the retrospective grade; `sdlc-model` and `sdlc-goal`
  reconciled from "whole goal in one subagent" to per-phase. Where the host has no subagents it degrades
  honestly to an inline reviewer that still loads the brief fresh. `/sdlc-doctor` reports whether
  independent review is on or the maker is reviewing its own work (INLINE). `plan-review`, `sdlc-review`,
  and `sdlc-retro` skills now open with the independent-reviewer stance + whole-repo blast-radius rule.

### The loop's review→fix loop is hard-capped (0.9.4)
- **`work.max_review_cycles` (default 3) caps the autonomous review cycle.** When the loop reviews its own
  PR and requests changes, it fixes + re-reviews — and if the review kept finding new problems, that could
  churn until the whole run's budget ran out (the per-goal loop isn't bounded by `max_iterations`, which
  counts goals, not review passes). Now `work.py post-review` **counts the block cycles in the goal's work
  record and, at the cap, returns a `PARK:` line** instead of asking for another fix — the review didn't
  converge, so a human takes it. Enforced in code, not left to the SKILL prose. `0` disables the cap.

### The loop reviews its own PR and posts the verdict — no human in the loop (0.9.3)
- **`require_review` had only a READ side.** It parked a merge until a PR was approved, but *nothing in
  the kit posted the approval* — so with `require_review: "approval"` the loop would open a PR and park
  forever waiting for a `loopsmith:approve` that never came (the same permanent-refusal shape). The gate
  was built for an external reviewer; there was no autonomous author.
- **`work.py post-review` is the WRITE side.** After it opens the PR, the loop runs a **fresh, adversarial
  review of the real mergeable diff** (a review *after* the PR — distinct from the pre-PR self-review) and
  posts the verdict itself: `--verdict approve` → `loopsmith:approve` (the gate merges); `--verdict block
  --reason …` → `loopsmith:block`, and the loop **fixes the issues in the worktree, re-verifies, and
  re-reviews** until clean (bounded to a couple of cycles, then parks as a backstop). Fully autonomous —
  the loop is the reviewer, and a plain comment sidesteps GitHub's block on approving your own PR. A human
  can still use the same markers on a loop PR. `/sdlc-loop`'s SKILL drives the cycle; docs/config/doctor
  reframed from "a human approves" to "the loop reviews its own PR."

### Docs caught up with the 0.9.x features (0.9.2)
- The **"What you get"** table now lists the two 0.9.x headliners it was missing: **one-command adoption**
  (`/sdlc-setup`) and the **PR review gate** (`work.require_review`, incl. the `loopsmith:approve`/`:block`
  comment fallback). The **Feature-flags** table already carried `work.require_review`. `/sdlc-doctor`
  surfaces every one of these live (ledger setup, ignore mechanism, auto-merge policy, the review gate,
  the verify-worktree footgun) — verified against its actual output. Docs-only; version → **0.9.2**.

### The review gate works on a solo account — comment-based approve/block (0.9.1)
- **`require_review` no longer has a signal that can never fire.** GitHub structurally forbids approving
  or requesting-changes on your *own* PR — so on a repo where one identity both opens and reviews (a solo
  maintainer, or an org that pins all automation to one account), the formal `APPROVE`/`CHANGES_REQUESTED`
  signals are permanently dead, and `approval` mode would refuse *every* merge forever (the same shape as
  the old empty-`verify.command` trap).
- **Plain-comment markers are the self-usable channel** (comments have no self-authorship rule): a
  **`loopsmith:block`** comment is honored as a change-request, a **`loopsmith:approve`** comment satisfies
  approval, and **`loopsmith:unblock`** clears a block — latest marker wins, and a block overrides even a
  formal approval. Formal reviews and unresolved threads still count whenever a second real identity is
  around. `/sdlc-doctor` and the README now spell out the asymmetry. First patch release: **0.9.1**.

### `/sdlc-doctor` catches the worktree interpreter-path footgun
- **A relative `.venv`/`node_modules` path in `verify.command` now gets flagged before it bites.** Once
  `work.enabled` is on, verify runs in a *fresh* per-goal worktree that has none of your installed
  dependencies — so a natural `cd backend && .venv/bin/python3 -m pytest` fails `exit=127` on the first
  real run, even though it worked with `work.enabled: false` (which ran against the main checkout). It's
  a direct consequence of the 0.9.0 fix that correctly runs verify *in* the worktree. `/sdlc-doctor` now
  flags a bare relative `.venv`/`venv`/`node_modules` interpreter path (an absolute path is fine), and
  the README documents the requirement: the command must resolve independent of the working directory.

### A real PR review gate, not just self-review — `work.require_review`
- **Auto-merge can now wait for an actual review, independent of branch protection.** Before this, the
  merge gate's "safe" (`mergeStateStatus`) only folded in reviews the *base branch's protection*
  required — so a human's ad-hoc **"Request changes" on an unprotected base** (the common `staging`/`dev`
  shape) was invisible to it, and an unattended `auto_merge` landed straight over it. The kit's "Review"
  phase is a *self-review* of the agent's own diff before the PR even exists; nothing read real review
  feedback after the PR. (Confirmed by grep: zero handling of `reviewDecision` / `reviewThreads` anywhere.)
- **`work.require_review`** adds the gate: `changes` parks on a `CHANGES_REQUESTED` review or an
  unresolved review thread; `approval` also requires an `APPROVED` `reviewDecision` before merging —
  parking until a human approves. It reads the PR's real review state (`gh pr view` + GraphQL threads)
  and parks with a clear reason; a human approves and re-queues. **Off by default**, so existing behavior
  is unchanged, and **fail-open** — an unreadable review state never blocks (the other gates still hold).
  `/sdlc-doctor` reports the gate's state. Costs one extra read per merge; the right default for anything
  unattended on a low-protection base.

### Hooks survive a broken `python3` on the PATH
- **A messy multi-python machine no longer breaks the session on first use.** The hooks shell out to a
  bare `python3` — whatever the user's PATH resolves — and on a machine with pyenv/conda that can be a
  *broken* interpreter (a shim pointing at an uninstalled version, a half-broken base), not just an
  absent one. Since the hooks run on every edit / web-fetch / prompt, that failed the very first tool
  call, and the only fix a real adopter found was removing a Python version.
- **Now it degrades to a no-op instead of erroring.** A new `hooks/_py.sh` preflights the interpreter
  (`python3 -c ''`) and, if it can't run, exits 0 (allow) rather than failing; when it works it execs
  straight through, so stdin/stdout/exit-code still pass and a hook can still deny. `decision_gate.py`
  and `research_capture.py` now run through it. `sdlc_gate.sh`'s preflight catches a *broken* (not just
  missing) `python3`, and its final emit is guarded so a failure there can't fail the prompt hook.

### Adoption-safety guards — the silent states are now loud
For repos configured by hand (not via `/sdlc-setup`), the states that surprised real adopters are now
surfaced instead of silent:
- **`work.enabled: false` no longer looks like success.** A goal used to close, the ledger say `done`,
  and *no branch/commit/PR* ever get created — with no signal; you'd only notice on a stray `git status`.
  Now `loop.py start` prints a heads-up, `record … done` prints a loud note ("changes only in your
  working tree, no PR"), and `/sdlc-doctor`'s dashboard says it plainly.
- **The verify permanent-refusal trap is caught before it bites.** `verify.enforce: true` with an empty
  `verify.command` refuses *every* `done` forever. `/sdlc-doctor` now flags it as a real gap (a per-goal
  `verify_command` also satisfies it), and `loop.py start` warns up front rather than only failing at the
  first `record done`.
- **`/sdlc-doctor` reports which mechanism ignores the runtime dirs** — the tracked `.gitignore`, the
  local `.git/info/exclude`, or neither — so an adopter catches a mismatch with their intent.
- README documents both: the no-PR-when-`work`-off behavior, and that a host repo's own `PreToolUse`
  edit-gate applies to LoopSmith's Implement edits too.

### `/sdlc-setup` — adopt LoopSmith into an existing repo in one pass
- **One command configures a real repo the way a team actually wants it**, instead of ten manual edits
  to `config.json`. It detects the repo from the git remote (ssh / https / host-alias forms), finds the
  board, scaffolds `.sdlc/` if missing, and writes a config with the **safe adoption defaults**: github
  discovery scoped to **`assignee: @me`**, **ledger on**, **work (a PR per goal) on** (`auto_merge: off`
  — a clean PR is left for a human). Then it bootstraps the ledger and runs doctor.
- **It refuses two traps real adoptions hit.** It never sets `verify.enforce: true` without a real
  `verify.command` — that combination refuses *every* `done` forever — and turns an existing
  enforce-without-command back off. And it never clobbers or narrows a git-ignore rule a human already
  set: a blanket `.sdlc/` exclude is left untouched, and a `--scope local` routes runtime-dir ignores to
  `.git/info/exclude` (nothing the team sees) for a local-only adoption. `/sdlc-ledger` and the
  `sdlc-init` tip now use the same safe helper instead of a blind `echo >> .gitignore`.
- The skill also flags the host-hook interaction: if the repo gates source edits behind its own
  `PreToolUse` hook, LoopSmith's Implement-phase edits go through it too.

### One command turns the ledger on — and a `/sdlc-ledger` slash command
- **`sync.py bootstrap` (and `/sdlc-ledger`) sets the whole ledger up in one shot.** Standing the
  ledger up used to be a multi-step dance whose failure mode was silent: `init` created the ops branch
  *locally* and `publish` only pushed once you owned an entries file — so the branch reached the remote
  only after your first goal wrote a claim, and a teammate who ran `init` found nothing to fetch.
  bootstrap does init **+ seeds your (empty) entries file + the `TEAM.md` rollup + pushes**, so the
  branch exists for the whole team the moment the ledger is switched on. Idempotent; each teammate runs
  it once per clone to join.
- **`/sdlc-doctor` sets it up for you.** It now reports `team ledger initialized` as a real setup gap
  (enabled in config but branch not created) and runs the one-command bootstrap — the ledger comes up
  the moment you flip the switch and run doctor.
- **New `/sdlc-ledger` skill** makes every ledger operation a plugin command instead of a raw
  `python3 <path>/ledger.py …` line — set up, read (`mine`/`summary`/`render`), leave a note, hand a
  blocker off, answer a hand-off. Sharing a slash command with the team beats sharing a file path.
  Claiming/recording stays automatic inside `/sdlc-loop`; this is for everything around it.

### The ledger is a claim lease — two loops stop starting the same goal
- **A `claimed` line is now a lock, not just a record.** The team ledger already recorded who started
  what, but nothing read it back, so two people running the loop against one board could both pick the
  same issue (the assignee filter was the *only* thing keeping work apart). `_next` now consults the
  ledger before it commits: a goal another actor holds an **open** claim on — `claimed` with no later
  `done`/`parked`/`failed` — is skipped, and the loop takes the next free one instead.
- **Your own work is never locked against you.** A claim *you* hold is still returned, so a resumed or
  restarted run continues its own goal rather than treating it as taken.
- **A crashed claimer can't freeze a goal forever.** A claim older than `ledger.lease.ttl_hours`
  (default 12; `0` = never expire) is treated as released — the lock self-heals instead of stranding a
  goal on a dead run.
- **Honest about its guarantee.** It's an *advisory* lease over eventually-consistent state (it sees
  claims already pulled to the ops branch, not a distributed mutex), so per-person assignee routing
  stays the first line of defence; the lease closes the double-start gap behind it. **Off by default**
  and **fail-open**: no ledger, or an unreadable one, and selection is byte-identical to before.

### A loop trigger keeps its own watcher alive
- **The loop now starts the ledger watcher itself.** Entries were only ever appended locally; nothing
  pushed them to the ops branch except the watcher, and a team that ran the loop but forgot the watcher
  saw a silent ledger — claims and hand-offs piled up on one laptop and reached nobody. Every loop
  trigger (`loop.py next` and the unattended drain) now ensures the watcher is running before it does
  anything else, so the ledger flows without a separate manual step.
- **Firing it repeatedly is safe.** `watch.sh` is idempotent — a live `watch.pid` makes a second copy
  no-op instead of stacking watchers, while a stale pid from a crashed watcher is ignored so the next
  trigger takes over. The launch is **fail-open**: a watcher that can't start never breaks the run.
  Off entirely unless the ledger is enabled and initialised (`sync.py init` has made the worktree) —
  nothing to publish otherwise.

### A guardrail that isn't a prompt — the decision gate
- **`/sdlc-decide` + a `PreToolUse` hook.** Every other gate in this kit is discipline a model is
  *asked* to follow, and a model can talk itself past discipline — especially unattended, on iteration
  forty. Record an architectural invariant in `.sdlc/decisions.json` and the edit that breaks it is
  **denied** by a script that doesn't negotiate. `invariant` denies; `recipe` asks; `caution_on_touch`
  asks on any edit inside a path that's dangerous to touch at all.
- **Authoring the registry is the opt-in.** No registry, no behavior — installing the plugin changes
  nothing. `gates.decision_gate.enabled: false` turns it off without deleting the file, for refactors
  that intentionally move an invariant.
- **JSON, not YAML** — this kit takes no dependencies, and the registry is not worth breaking that for.
- **Precision is the whole product here.** A gate that cries wolf gets clicked through, and then it
  protects nothing. So: params are scoped to their own decision's paths (a name as common as `timeout`
  can't trip everywhere); only literal assignments are judged, never expressions; comments are
  stripped; and a violating value *quoted inside prose* — `doc = "set timeout: 120 here"` — does not
  fire. That last one was a real false-deny caught in test, and false denies cost more than misses.
- **`decision_gate.py check`** scans code already on disk, because the hook only ever sees edits going
  forward and the first question after authoring a registry is "does my code even comply?"
  **`validate`** catches entries that can never fire — a registry's failure mode is being quietly
  unenforceable, not loudly broken.
- **Editing the registry always asks.** Changing a recorded invariant is a supersession the user makes
  deliberately, never a silent rewrite by the agent about to be bound by it.
- `/sdlc-doctor` counts **active** decisions, not entries — a registry whose decisions are all
  superseded enforces nothing, and reporting that as ON would be the false assurance this gate exists
  to remove. `/sdlc-retro` can now propose an invariant as a fourth learning store.
- **Stated limits, in the skill:** it reads literal assignments only (`timeout = CONFIG.default` is
  invisible), it sees edit text rather than the program, and it fails open. It's a seatbelt against the
  obvious mistake, not a proof of compliance.

### The lane is routed on, not just recorded
- **Both orchestrators now branch on the lane** Research measured. Sizing a goal and then ignoring the
  size is the same producer-without-consumer defect `lane: auto` had before it was implemented: the
  value existed, nothing read it, and a typo fix drew the same seven-phase pass as a schema migration.
  `discovery.py lane <goal>` resolves it in local mode; github mode reads it from Research's note on
  the issue timeline, because an issue number carries no frontmatter.
- **small** plans in a few lines and keeps the retro to one; **large** works the design out before
  planning and asks whether it should be several goals; **medium** is the full pass, unchanged.
- **Plan-Review runs in full at every lane.** Small goals are exactly where an unreviewed plan ships —
  nobody looks twice at a change that seemed obvious — so no lane may skip the gate.
- An unsized goal resolves to **`medium`**, never `small`: guessing low on an unknown goal skips
  ceremony it might need, which is the one direction where being wrong is expensive.

### The ledger records merges
- **A landed PR is now a ledger line.** `work.merge` armed GitHub's auto-merge but recorded nothing, so
  the team view showed goal outcomes (`done`/`parked`) yet never the merges themselves. It now appends a
  **`merged`** entry (a new shared kind) tagged with the PR number when it arms a merge — fail-open, so a
  ledger problem can never turn a successful merge into a failure. Off unless the ledger is enabled,
  exactly like every other entry.

### The Research phase gets an executor, and `lane: auto` starts meaning something
- **`/sdlc-research`** — Research was the only one of the seven phases with no executor behind it
  (the README said "agent practice; no dedicated skill"). It now maps a goal's blast radius, **records
  the exact queries** so Review can re-run them — coverage guaranteed by re-scan, not by trusting that
  one afternoon's list was complete — inventories the debt already in the radius, and sizes the goal
  into a lane from the footprint it *measured*, never from a guessed duration.
- **`lane: auto` was a promise nothing kept.** `sdlc-init` has been scaffolding it into every goal with
  a README saying "auto lets the engine size it"; no code sized anything. Research now writes the lane
  back, which is what lets small goals skip ceremony they never earned.
- The dossier lands in `.sdlc/research/`, deliberately **not** `.sdlc/plans/` — the hard plan-gate
  treats any recent file there as a fresh plan, and a research note is not a plan.

### Plan-review closes its own loop
- A FIX-FIRST verdict sent a plan back with **no record of what happened to each finding**. Every
  finding now gets an explicit disposition — accept / reject / partially accept, each with its own
  `file:line`. This matters most in `/sdlc-loop`, where no human adjudicates and nothing otherwise
  stopped the loop from faithfully implementing a wrong review finding.
- **The review is a hypothesis too.** A finding claiming a file or symbol doesn't exist is checked
  against the filesystem before it's accepted; a plan patched to satisfy a false finding is worse than
  the plan was. Plus a regen threshold: when most findings are substantive, the plan wants
  regenerating, not patching.

### Cumulative drift, and a trigger that can actually fire
- **`/sdlc-align`** — every existing alignment gate reads one unit of work: `sdlc-plan-review` §4 holds
  one plan to the north-star, `sdlc-retro` asks what one goal taught. Neither can see the shape of
  twenty goals, which is where strategy actually drifts. Two lenses only — dominant theme vs stated
  bets, and effort accumulating behind a bet nobody ever declared. No-op without a north-star.
- **`/sdlc-status` reports when it's due.** The old plan was to add this "only if drift proves to slip
  past the other two" — a trigger that cannot fire, since undetected drift is exactly what you can't
  observe without the audit. A count of goals shipped since the last report is something the loop can
  see. The report is its own bookkeeping; no extra state file.

### Both backlog modes reach the same place
- **The alignment counter was blind in github mode.** It tallied `.sdlc/goals/*.md` with `status: done`
  — but when goals are issues that directory stays empty, so the count sat at zero and the audit would
  never have come due for exactly the teams running a shared board. It now takes the larger of the
  local tally and the loop's `iteration` cursor, since neither signal covers both modes alone
  (`iteration` misses interactive `/sdlc-goal` runs; the file tally misses github entirely).
- **Research recorded the lane into frontmatter an issue doesn't have.** In github mode the lane, the
  site count, and the blocking questions now go on the issue timeline, where every other phase already
  records. A research pass that leaves no comment there is invisible to everyone but its author.
- **Plan-review writes down its rejections.** Accepted findings are visible in the revised plan; the
  reasoning for *overruling* a reviewer existed nowhere — and it's the first thing anyone asks when the
  same objection returns a month later.
- No new board columns: Research sits in the same **In Progress** state as Plan and Implement, and
  `/sdlc-align` deliberately files nothing — drift is a question about direction, not about any one
  issue.

### Standing docs stop only ever growing
- **`/sdlc-doctor` scans for rot** — cited paths and links in `.sdlc/project.md` and
  `.sdlc/context/*.md` that no longer resolve. A north-star pointing at a deleted file quietly teaches
  the wrong thing to every phase that reads it. Reported in its own section and kept out of the
  readiness score: "is my setup working?" and "are my docs rotting?" are different questions.
- It only reports references that provably don't resolve — globs and `<placeholders>` are skipped,
  because a check that cries wolf gets ignored along with its true positives.
- **`/sdlc-retro` proposes the retirements.** Adding a rule has an obvious moment; retiring one never
  does. Retro now asks what the goal made redundant — a rule CI now enforces mechanically, a rule whose
  premise moved, a plan whose work shipped — and parks each removal for approval alongside the
  additions, in the same table.

### Merge rights + a protection-aware policy
- **A fork PR or a read-only repo is never merge-attempted.** Permission is checked before anything
  it could gate on, and it is not a config question: on a project you lack write access to, the PR
  *is* the deliverable. The loop opens it, says why it stopped, and records **`done`** — not a park,
  because nothing about it wants a human. Unknown rights fail **closed**.
- **`auto_merge` is now `off` | `protected` | `always`** (default `off`; the old booleans still parse,
  `false`→off and `true`→always). `protected` merges only where the base branch genuinely REQUIRES
  checks or reviews — autonomy proportional to the guardrails that actually exist.
- **Fixes a truthfulness bug shipped in the previous entry.** The "no required checks" warning tested
  whether a check had *run*, not whether one was *required*. A repo can run CI on every PR and require
  none of it — which is exactly the state this repo was in — so the warning stayed silent in the one
  case it existed for. Protection is now read from
  `repos/{owner}/{repo}/branches/{base}/protection` (404 = nothing enforced) and is a real branch in
  the logic rather than a message.
- Outcome mapping is explicit in the loop prose: a failing required check records `failed` (needs a
  fix), a conflict records `parked` (needs a decision), and an opened-but-unmergeable PR records
  `done`. The review queue only fills with things that actually want attention.

### Per-goal worktree + the merge gate
- **One worktree, one branch, one PR per goal** (`work: {"enabled": true}`, default OFF): the loop
  stops sharing your working copy. An in-place `checkout -b` would move the tree out from under
  whatever you left open *and* rewrite `.sdlc/goals/` — which `sdlc-init` tells you to commit —
  underneath the loop that is reading it. Both failures are silent; a worktree removes both.
- **Cutting fresh from `<remote>/<base>` is the goal-start rebase**: nothing to replay, so it cannot
  conflict. The only real rebase is reactive (GitHub reported the PR `BEHIND`) and it `--abort`s on
  conflict rather than strand a half-applied tree that every later goal in the run would build on.
- **`verify_command` now runs in the goal's own worktree**, not the main checkout. It was resolved
  from `sdlc_dir` alone, which with a worktree would have proved the *unchanged* tree green and let
  `record done` accept it. `loop.py verify` resolves the root through `work.root()`, matching the
  injectable `repo_root` `pipeline.py` already had; with the feature off the root is unchanged.
- **A merge gate that is clean AND safe** (`work.auto_merge`, default OFF): needs this run's passing
  verify evidence *and* GitHub's `mergeable` + `mergeStateStatus CLEAN` (required checks and reviews
  folded in), then arms GitHub's own `--auto` so the last word is an atomic re-check at merge time
  instead of a stale read. It retries the lazy first-read `UNKNOWN` rather than treating it as an
  answer, and says so out loud when a repo has no required checks — `CLEAN` there means only that
  GitHub had nothing to object to. Anything else prints `PARK: <reason>` for the existing review
  queue; no new human-intervention path.
- **No general `git` tool for the loop**: it commits through `work.py commit`, which can only ever
  run against this goal's worktree — a broad `Bash(git *)` would hand the unattended loop the exact
  power the feature exists to remove.
- `evidence_path`/`done_refusal` moved from `loop.py` to `state.py` so the merge gate can require the
  same evidence without the two modules importing each other. `loop.py` keeps both names as aliases.

### Team ledger
- **`TEAM.md` now shows `claimed`**: the shared team view records WHO started a ticket and WHEN, not
  only outcomes — it pairs with `done` to read a ticket's start→finish at a glance. `note` stays
  personal (unless addressed `to` someone). Inbox/wake behavior is unchanged (that keys off `to`, not
  the shared-kinds set), so a claim never wakes a teammate.
- **`TEAM.md` is published to the ops branch**: `sync.py publish` now renders and commits the rolled-up
  view alongside your entries file, so a lead can read one file on `sdlc-ledger` instead of cloning and
  rendering locally. It stays conflict-free — TEAM.md is a pure function of the entries, so a push race
  is resolved by rebasing and re-rendering from the merged entries, never by hand.

## 0.7.0 — the coordination release

The theme: LoopSmith stops being a single-player autopilot, and stops running one thing at
a time. A team running it against one repo now leaves a shared, attributed record of what
each loop did; a blocker in someone else's code is handed to that person instead of parked
into silence; the hand-off actually reaches them; and a goal's independent slices run
together instead of queueing. Everything ships opt-in and default-OFF — with no `ledger`
and no `parallel` block in config.json the loop behaves exactly as 0.6.0 did.

### Coordination
- **Team ledger** (`ledger: {"enabled": true}`, default OFF): a committed, append-only record of what
  the loop did — `claimed` when it takes a goal, `done`/`parked`/`failed` when it finishes one — with
  a timestamp and an actor on every line. **One file per person** (`.sdlc/ledger/entries/<actor>.jsonl`):
  you only ever write your own, so concurrent appends can neither race on disk nor conflict in git;
  the team view is their union, computed on read. Kinds `claimed · done · parked · failed · handoff ·
  ack · release · note`; an entry with a `to` is addressed to that person. `ledger.py` renders
  `TEAM.md`, lists what is addressed to you, and summarises outstanding hand-offs; `/sdlc-status`
  reports the entry count and `/sdlc-doctor` reports the flag. Every write from the loop is fail-open
  — a ledger problem can never stop a run. Absent config = byte-compatible with 0.6.0.

- **Cross-area hand-off** — parking with a successor instead of parking into silence. A blocker in
  code someone else owns is not a decision for the user, but until now it parked like one: a
  gitignored queue entry, an unaddressed issue comment, and no code path in the kit had ever set an
  assignee. `handoff.py open` resolves the owner from the repo's own `.github/CODEOWNERS` (override
  per area with `ledger.owners`), opens an issue in their area **assigned to them and carrying the
  goal label** — so their own loop picks it up through the `assignee` filter, no new transport —
  records a `handoff` ledger entry addressed to them, and links it from the blocked issue. Then the
  goal parks as before. `handoff.py ack --state accepted|deferred|declined|resolved` is the answer;
  `deferred` deliberately does not settle it. Degrades honestly with no owner, no `gh`, or a local
  backlog: the ledger entry is still written.

- **Ledger transport + watcher** — a mention nobody reads is worth nothing, so the ledger now shares
  itself and tells you when it needs you. `sync.py init` makes `.sdlc/ledger/` a **git worktree** on a
  dedicated ops branch (default `sdlc-ledger`, created from the EMPTY tree so it carries no code):
  fetching and rebasing the ledger touches only that worktree, so a pull can never disturb your code
  checkout or drag you into a mid-task rebase, and the branch is never merged into the integration
  branch so it needs no review. `publish` fast-forwards your own entries file with a bounded
  fetch-rebase-retry — a race replays, never forces, and can't conflict because nobody shares a file.
  `watch.sh` ticks on `ledger.watch.interval_seconds` (default 900): pull → classify → write
  `.sdlc/state/inbox.md` → publish. **`loop.py next` surfaces that inbox on stderr before handing over
  the next goal** — the only honest delivery point, since nothing can inject a message into a running
  session and interrupting a goal mid-flight loses work; worst case is one goal of latency, and stdout
  stays exactly the goal the caller parses. Deduped twice: a per-author cursor, plus a
  `kind:issue:state` signature so a colleague's rebase can't replay old mentions (a state *change*
  still fires).

### Parallelism
- **Slice-level parallelism** (`parallel: {"enabled": false, "max_concurrent": 3}`, default OFF): a
  goal's independent slices now run concurrently instead of queueing behind each other. Declare them
  beside the plan in `.sdlc/plans/<goal-stem>.slices.json` — `{id, title, needs, files, size, status}`,
  everything but `id` optional — and `slices.py plan` renders a dispatch plan: the runnable frontier
  packed into **waves** of mutually non-conflicting slices, capped at `max_concurrent`, widest fan-out
  first so a wave is never spent on leaves while the critical path waits. Deterministic by
  construction, so the plan is reviewable before anything is dispatched; `slices.py check` reports
  unknown dependencies, duplicate ids, and **cycles by their members** (you cannot pick which edge to
  break without the names). **Conflict is decided from DECLARED files only** — `fnmatch` both
  directions plus a literal-prefix check, so `engine/**` and `engine/graph.py` are one blast radius —
  and a slice that declares **no** files conflicts with everything and runs alone: an unknown radius
  is not something you may parallelise, and one lost edit costs more than one extra wave. Each slice
  that declares files is dispatched with `isolation: worktree`, so concurrent siblings cannot stomp
  each other's half-finished edits.

  The boundary is drawn where the host's guarantees end. A python script cannot spawn a subagent, so
  `slices.py` **computes and advises** and `/sdlc-loop`'s prose dispatches — one subagent per slice,
  one wave at a time. The desktop app's "chips" mechanism is app-internal and **not a public API for
  plugins**, so nothing here is built on it: a slice marked `"size": "large"` (too big for one
  subagent's context) is dispatched as `session` and the plan **prints the exact
  `claude --worktree <goal-stem>-<slice-id>` line for a human to start**. The loop never shells out to
  an unattended `claude -p` — uncapped spend, and a second worker on one `.sdlc` breaks every state
  file in the kit. `/sdlc-doctor` reports the flag. Absent config, or no manifest, and every goal runs
  as one unit exactly as before.

### Fixed
- **A fresh clone of an adopted repo can run the loop** *(bug-fix class, applies with or without the
  ledger)*: `.sdlc/state/` is gitignored by design — it is per-machine runtime — so a teammate who
  clones a repo that has already adopted the spine gets the committed config and goals but **no state
  files at all**, and `loop.py start` / `next` / `record` died on a raw `FileNotFoundError` before
  their first goal. `STATE.md` and `review-queue.md` are now scaffolded on first use. Nothing changes
  for a repo whose state already exists.
- **The ledger worktree path is resolved before it reaches git.** Every git call runs with
  `-C <another directory>`, so a relative `.sdlc` made `git worktree add` create the worktree under
  the project root's own name and the next write landed nowhere.
- **`ledger.py summary` and `TEAM.md` distinguish an unanswered hand-off from one someone has taken.**
  Both are outstanding — the blocker is real until it is `resolved` — but only the first needs
  chasing, and a bare count could not say which was which.

### Backlog routing
- **Per-owner discovery scope**: `discovery.github.assignee` (e.g. `"@me"`) makes the loop pick only
  issues assigned to that user, so several people can run the loop against one shared board/Project
  without grabbing each other's work. Absent/empty = no filter (every open goal issue is in scope) —
  byte-compatible with prior behavior. Applies to discovery only; the board backlog sync still seeds
  the whole team's issues.

## 0.6.0 — the trust-and-feedback release

The theme: everything the loop CLAIMS is now checkable, everything it produces feeds
back into it, and an unattended night no longer ends at the first limit or crash.
Every new capability ships opt-in and default-OFF (one bug-fix exception, noted);
absent config keys behave exactly as 0.5.0.

### Guardrails made real
- **Prompt gate is repo-scoped** *(the one default-ON change — bug-fix class)*: the
  UserPromptSubmit directive now speaks only in repos that adopted the spine
  (`.sdlc/` exists); everywhere else it is a silent no-op, so a machine-wide install
  never injects policy into unrelated projects. `LOOPSMITH_GATE_GLOBAL=1` restores
  the old always-on behavior.
- **Budgets enforce**: `budget.max_minutes` (wall-clock) and `budget.max_tokens`
  (host-reported via the new `loop.py spend` verb) now actually halt the run;
  previously only `max_iterations` did. Absent keys enforce nothing.
- **Opt-in hard plan-gate**: `gates.hard_plan_gate.enabled` mechanically DENIES a
  source edit unless a fresh plan exists under `.sdlc/plans/`
  (`touch .sdlc/.allow-direct-edits` for a deliberate bypass; fail-open throughout).

### Truthful outcomes
- **`failed` is not `parked`**: a goal the loop could not resolve gets its own
  terminal status, review-queue tag ("needs: a fix" vs "needs: human review"),
  counters, and `record failed` verb — the morning queue separates decide-this
  from fix-this.
- **Machine-checked done**: `loop.py verify` runs the goal's proving command
  (frontmatter `verify_command`, else `verify.command`) and records evidence; with
  `verify.enforce` on, `record done` is REFUSED without a fresh passing verify from
  this run.

### The feedback circle
- **Bidirectional pipeline report card**: declare stages once in `.sdlc/pipeline.json`
  and `pipeline.py card` renders every stage in BOTH directions — forward
  (survivorship: nothing dropped) and reverse (provenance: nothing invented) — with
  uninstrumented lanes reading honest-ABSENT, a typed verdict, and `--compare` for
  the regressed / improved / still-failing (recurrence) delta between runs.
- **Findings become work**: `pipeline.py propose` turns failing card signals into
  `proposed` goal files with the failing check pre-wired as their `verify_command`;
  the loop never runs one until a human promotes it to `pending`.

### Smarter, cheaper, visible
- **Per-step model + effort selection** (under the existing `model_selection: "auto"`
  gate): `resolve-step` returns `model=<tier> effort=<low|medium|high>` so mechanical
  steps inside a hard goal (tests, watchers, lint) run below the goal's ceiling;
  `resolve` output stays backward-compatible.
- **Feature dashboard**: `doctor.py features` (also appended to `/sdlc-doctor`
  output) reports every optional capability's LIVE state with its one-line enable.

### Unattended for real
- **Overnight supervisor**: `scripts/supervise.sh` owns the loop's lifetime with
  zero polling — blocked while a session runs; on exit it classifies the output
  (via the pure `supervise_classify.py`): loop finished → stop · budget → relaunch ·
  usage-limit → **sleep until the stated reset time (+ jitter), then relaunch** ·
  unknown → capped backoff. Kill-file stop; per-run output capture; laptop-sleep
  caveat documented.

57 new tests across the arc (281 total, coverage >85% held, Tier-1 eval baseline
unchanged). No runtime dependencies added.
