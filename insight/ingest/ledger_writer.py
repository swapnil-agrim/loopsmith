# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Ledger writer (issue #105, E1.S7): the persistence half of the ledger reader #101 built --
`ledger_reader.read_all` exists, is well-tested, and until this story was called by nothing.
This module writes what it finds into fact_event/fact_handoff, and stores an incremental resume
cursor so a second `insight ingest` run neither re-ingests a ledger record it already stored nor
loses one, and a run interrupted midway resumes without gaps or duplicates.

THREE SETTLED SCOPE DECISIONS, carried over verbatim from the issue (parked six times on
exactly these three points -- restated here so the next reader does not re-litigate them):

  1. fact_collector_pack is untouched. It is append-only BY DESIGN (each pack is a
     point-in-time snapshot carrying its own window; the accumulated history is the debt-trend
     metric's and the gap engine's --compare recurrence signal's own data) -- nothing here
     writes to it, imports packs.write_pack, or changes its shape.
  2. "Idempotence" means identity-keyed, not row-count-stable: the same ledger record (its own
     `id`, `<actor>:<seq>`, already unique per author) is never ingested twice. It does NOT mean
     two ingest runs produce identical row counts anywhere -- fact_collector_pack in particular
     is EXPECTED to grow every run; that is a different table with a different contract.
  3. The resume cursor is keyed on (project_id, actor_id) only, never (..., stream). `stream` is
     inert: ledger.py has no stream parameter yet (that's #136, E6.S1), so it is always exactly
     one value today. Widen the cursor's key when #136 ships a real second stream, not before.

WHERE THE ROWS GO, per spec §B.3's own column list (verified against insight/ingest/store.py,
NOT invented here -- fact_event and fact_handoff are written with EXACTLY the columns store.py
already declares, no new ones added by this story):

  * `handoff` and `ack` kind records -> fact_handoff. It is a separate table (not a view over
    fact_event) because the questions asked of it are graph-shaped (spec §B.3) -- a handoff row
    is a MERGE of its own opening `handoff` record and whichever `ack` record(s) later answer
    it, mirroring ledger.py's own handoff_states()/outstanding() semantics: the LATEST ack wins
    for display (ack_ts/ack_state, last-write-wins, matching handoff_states()'s own
    `latest[key] = entry["state"]` loop), and settled_ts is set exactly when that latest ack's
    state is in ('declined', 'resolved') (matching outstanding()'s own settled-state set).
  * every other kind (claimed, done, parked, failed, release, note, merged -- ledger.py's own
    KINDS minus handoff/ack) -> fact_event, one row each, kind stored VERBATIM. This settles the
    open question metrics/7.sql's and metrics/10.sql's own guardrail comments flagged
    ("ASSUMPTION, NOT A CONFIRMED FACT ... #180 ... must confirm or revise which table/
    vocabulary this replay actually reads"): fact_event.kind IS the ledger's own entries-stream
    string, exactly what those two views already `WHERE kind IN (...)` against.
  * reliability_class (fact_event only -- fact_handoff's spec-given columns carry no such
    field) is carried straight from ledger_reader.read_all_with_reliability's own per-record
    tag: 1 for a record that came from ledger/entries/ (Python-controlled, an actor cannot
    forget to write a lifecycle line), 2 for ledger/events/ or sdlc_dir/events/ (agent-emitted,
    best-effort) -- spec §3's reliability boundary, so a query can never mix them without
    saying so.

WHAT TODAY'S LEDGER.PY ACTUALLY CARRIES, mapped onto fact_event's own columns: only
project_id, goal_id, ts, actor_id, kind, reliability_class. ledger.py's OPTIONAL_FIELDS
(area, to, issue, priority, why, state, ref, pr) have no matching fact_event column for the
lifecycle kinds that land there (phase/gate/verdict/cycle/ms/tokens_in/tokens_out/cost_cents/
reason_class/ok/exit_code are the FUTURE agent-emitted events-stream vocabulary, spec §A.3 -- a
`phase`/`gate`/`verify`/`slice`/`spend`/`retro`/`park`/`scan` kind, not one of ledger.py's own
KINDS) -- so every fact_event row this module writes today leaves those columns NULL. This is an
honest reflection of what ledger.py's real kind vocabulary carries, not a shortfall: it is the
same "match the columns, do not invent one" instruction that keeps fact_handoff schema-faithful,
applied to fact_event too.

FACT_HANDOFF'S OWN RESIDUE, stated once, clearly, rather than engineered around: ledger.py's own
handoff_key() matches a handoff to its answering ack(s) by `issue`, falling back to `goal` only
when no issue exists ("a local backlog has no issue numbers but still needs the two halves to
find each other"). fact_handoff's spec-given schema (store.py's own fact_handoff DDL) has NO
goal/goal_id column -- only `issue INTEGER` -- so a goal-only handoff (no issue) cannot be
durably matched to its own ack ACROSS separate ingest runs; each such record gets its own
standalone row instead of risking a match against the WRONG one. Adding a goal column would fix
this but would be inventing a column the spec does not list, which the issue explicitly forbids
("match them, do not invent columns"). Matching by `(project_id, issue)` is exact and complete
for every handoff that carries a real issue number, which is this dogfooding repo's own dominant
shape (every hand-off task in docs/insight-backlog.json is filed against a real GitHub issue).

NEVER RAISES. Each ledger record's write (fact_event or fact_handoff) plus its own cursor
advance is one explicit DuckDB transaction (BEGIN/COMMIT, ROLLBACK on exception) -- same idiom as
artifact_reader.ingest_artifacts' per-goal transaction (.sdlc/plans/102.md Design decision L) --
so a record is either fully landed (row committed AND cursor advanced together) or not landed at
all (both rolled back), never landed-but-uncursored (which would duplicate on the next run) or
cursored-but-unlanded (which would silently lose it). One record's write failing for an
unforeseen reason (a well-typed record can still surprise DuckDB at INSERT, same class of risk
every other reader in this package already guards) must not abort every other record's ingest --
guarded per-record, matching packs.ingest_collectors'/git_reader.ingest_merge_lead_time's own
established per-row-not-per-batch posture.

THE CURSOR'S SKIP DECISION is taken against a SNAPSHOT of the cursor loaded once at the start of
this call, never against a value this same call has already advanced -- so a batch that
(pathologically) presents one actor's records out of seq order within a single run cannot skip
one it has not actually written yet. Real ledger data does not do this (ts and seq both advance
together, since one actor's own append() calls happen at increasing wall-clock time), but the
extra robustness costs nothing and removes an assumption this module would otherwise silently
depend on. See _load_cursor / ingest_ledger below.

A record whose identity this module cannot pin down -- `id` fails to parse a usable seq
(ledger_reader.seq_of degrades to 0, matching _seq's own tolerance), or `actor` itself is
missing -- is written AT MOST ONCE: the cursor key (a plain string, "" standing in for a missing
actor, mirroring ledger_reader._sort_str's own missing-value convention) starts absent from the
snapshot, so the FIRST such record this project ever sees is still ingested, but the act of
ingesting it plants a cursor row that makes every LATER record collapsing to the SAME degraded
key (seq 0, actor "") read as "already seen" and skip -- never silently lost on the first
encounter, never duplicated on every run after. Identity-keyed idempotence cannot be honoured
precisely for a record whose identity is unknowable; converging to "ingested once, not forever"
is the safe side of that tradeoff, the same "guard the computation, degrade the record" shape
ledger_reader.py itself uses throughout.
"""
import pathlib

from insight.ingest.git_reader import to_utc_naive
from insight.ingest.ledger_reader import read_all_with_reliability, seq_of
from insight.ingest.packs import project_id_for

#: ledger.py's own KINDS, split by destination table. Hand-offs are the two kinds whose
#: questions are graph-shaped (spec §B.3); everything else ledger.py can currently emit
#: (claimed, done, parked, failed, release, note, merged) is lifecycle/other -> fact_event.
_HANDOFF_KINDS = ("handoff", "ack")

#: Matches ledger.py's own outstanding(): a hand-off is settled exactly when an ack for it
#: reaches one of these two states, never earlier and never merely because SOME ack arrived.
_SETTLED_ACK_STATES = ("declined", "resolved")


def _blank_to_none(value):
    """None/""/missing -> None; anything else -> itself, unchanged. The DB-column-value
    counterpart of ledger_reader._sort_str's own tolerance (missing/None/"" all collapse to one
    falsy case) -- that helper coerces for SORTING, this one for the value actually stored."""
    return None if value in (None, "") else value


def _actor_key(record):
    """A stable, always-hashable cursor-bucket key for one record's actor -- a real actor name
    (str) unchanged, or "" when `actor` is missing/blank. Deliberately NOT the same thing as
    _blank_to_none(record.get("actor")): fact_event.actor_id should store NULL for a genuinely
    absent actor (honest about what is unknown), but a resume cursor keyed on Python's `None`
    would still work as a dict key today -- the "" sentinel exists so this module's ONE
    identity-tracking mechanism (see the module docstring's closing paragraph) is a plain,
    always-non-NULL string, matching ingest_ledger_cursor.actor_id's own VARCHAR PRIMARY KEY
    column, which -- like every SQL primary key -- cannot accept NULL."""
    actor = record.get("actor")
    return str(actor) if actor not in (None, "") else ""


def _issue_of(record):
    """The integer GitHub issue number a handoff/ack record names, or None -- covers both "no
    issue field at all" (a goal-only backlog item, ledger.py's own handoff_key() fallback to
    `goal`) and "issue present but not the int ledger.py's own CLI coerces it to" (a hand-edited
    or malformed ledger line: never raise on it, just treat it the same as absent). `bool` is
    explicitly excluded even though it subclasses `int` in Python -- a stray JSON `true`/`false`
    must never read as issue 1/0."""
    issue = record.get("issue")
    return issue if isinstance(issue, int) and not isinstance(issue, bool) else None


# --------------------------------------------------------------------------- resume cursor


def _load_cursor(conn, project_id):
    """{actor_key: last_seq} already ingested for this project, from ingest_ledger_cursor. A
    fresh project (no rows yet) returns {} -- every actor starts at "nothing seen". Loaded ONCE
    per ingest_ledger call and never mutated afterward -- see the module docstring's own
    "cursor's skip decision" paragraph for why."""
    rows = conn.execute(
        "SELECT actor_id, last_seq FROM ingest_ledger_cursor WHERE project_id = ?",
        [project_id],
    ).fetchall()
    return {actor_id: last_seq for actor_id, last_seq in rows}


_CURSOR_UPSERT_SQL = """
    INSERT INTO ingest_ledger_cursor (project_id, actor_id, last_seq)
    VALUES (?, ?, ?)
    ON CONFLICT (project_id, actor_id) DO UPDATE SET
      last_seq = excluded.last_seq
"""


def _advance_cursor(conn, project_id, actor_key, seq):
    conn.execute(_CURSOR_UPSERT_SQL, [project_id, actor_key, seq])


# --------------------------------------------------------------------------- fact_event


_EVENT_INSERT_SQL = """
    INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class)
    VALUES (?, ?, ?, ?, ?, ?)
"""


def _write_event(conn, project_id, record):
    """One ledger record (any kind other than handoff/ack) -> one fact_event row. Only the six
    columns ledger.py's own record shape actually has a value for are populated -- see the
    module docstring's "what today's ledger.py actually carries" section; every other fact_event
    column (phase, gate, verdict, cycle, ms, tokens_in, tokens_out, cost_cents, reason_class,
    ok, exit_code) is spec vocabulary for a stream ledger.py does not populate yet and stays
    NULL, honestly, rather than guessed."""
    conn.execute(_EVENT_INSERT_SQL, [
        project_id,
        _blank_to_none(record.get("goal")),
        to_utc_naive(record.get("ts")),
        _blank_to_none(record.get("actor")),
        record.get("kind"),
        record.get("reliability_class"),
    ])


# --------------------------------------------------------------------------- fact_handoff


def _apply_handoff(conn, project_id, record):
    """A `handoff` kind record -> fact_handoff. Matched/merged by (project_id, issue) when a
    real issue number is present (see the module docstring's own "fact_handoff's own residue"
    section for why issue-less handoffs cannot be durably matched instead): a new handoff record
    for an issue already on file REFRESHES that row's own from_actor/to_actor/area/priority/
    opened_ts in place -- last-write-wins, the same posture metrics 7.sql/10.sql already use for
    ledger replay -- rather than opening a second, competing row an ack could ambiguously attach
    to. ack_ts/ack_state/settled_ts are left untouched by a handoff record; only _apply_ack ever
    sets them."""
    issue = _issue_of(record)
    from_actor = _blank_to_none(record.get("actor"))
    to_actor = _blank_to_none(record.get("to"))
    area = _blank_to_none(record.get("area"))
    priority = _blank_to_none(record.get("priority"))
    opened_ts = to_utc_naive(record.get("ts"))
    if issue is not None:
        existing = conn.execute(
            "SELECT count(*) FROM fact_handoff WHERE project_id = ? AND issue = ?",
            [project_id, issue],
        ).fetchone()[0]
        if existing:
            conn.execute(
                "UPDATE fact_handoff SET from_actor = ?, to_actor = ?, area = ?, priority = ?, "
                "opened_ts = ? WHERE project_id = ? AND issue = ?",
                [from_actor, to_actor, area, priority, opened_ts, project_id, issue],
            )
            return
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [project_id, from_actor, to_actor, area, issue, priority, opened_ts],
    )


def _apply_ack(conn, project_id, record):
    """An `ack` kind record -> fact_handoff's ack_ts/ack_state/settled_ts, matched by
    (project_id, issue) exactly like _apply_handoff. Mirrors ledger.py's own
    handoff_states()/outstanding(): the LATEST ack always wins (last-write-wins, since
    read_all_with_reliability delivers records oldest-first and ingest_ledger processes them in
    that order) and settled_ts is set precisely when THIS ack's own state is one of
    _SETTLED_ACK_STATES, cleared otherwise -- a single scalar column can only hold one answer, so
    it holds the latest one, consistently, rather than "ever settled, ever since" (which
    outstanding() computes as a set-membership test over the WHOLE history, a different question
    fact_handoff's own columns cannot represent).

    An ack naming an issue nothing here has ever seen a handoff for (a different ingest run's
    project, a handoff predating this ledger, or simply data this repo does not have) is still
    recorded -- a standalone row, handoff-side columns NULL -- rather than discarded, matching
    this codebase's own "record what you know, NULL what you don't, never drop it" rule
    (gh_reader.write_pr_review_row's identical posture for an orphaned review)."""
    issue = _issue_of(record)
    ack_ts = to_utc_naive(record.get("ts"))
    state = _blank_to_none(record.get("state"))
    settled_ts = ack_ts if state in _SETTLED_ACK_STATES else None
    if issue is not None:
        existing = conn.execute(
            "SELECT count(*) FROM fact_handoff WHERE project_id = ? AND issue = ?",
            [project_id, issue],
        ).fetchone()[0]
        if existing:
            conn.execute(
                "UPDATE fact_handoff SET ack_ts = ?, ack_state = ?, settled_ts = ? "
                "WHERE project_id = ? AND issue = ?",
                [ack_ts, state, settled_ts, project_id, issue],
            )
            return
    conn.execute(
        "INSERT INTO fact_handoff (project_id, issue, ack_ts, ack_state, settled_ts) "
        "VALUES (?, ?, ?, ?, ?)",
        [project_id, issue, ack_ts, state, settled_ts],
    )


# --------------------------------------------------------------------------- orchestration


def ingest_ledger(conn, project_root, sdlc_dir=None):
    """The write orchestration `insight ingest` calls (issue #105). Reads every ledger record
    via read_all_with_reliability (oldest-first), skips any this project has already ingested
    (per-actor resume cursor), writes the rest into fact_event/fact_handoff, and advances the
    cursor -- one record, one transaction, at a time (see the module docstring for why). Never
    raises. Returns {'events', 'handoffs', 'skipped'} counts for CLI printing.

    ONE ACTOR'S RECORDS ARE PROCESSED IN ORDER, AND A FAILURE STOPS THAT ACTOR FOR THE REST OF
    THIS RUN -- found live while proving the "interrupt mid-run, resume" contract, not merely
    assumed safe: an EARLIER version advanced the cursor per-record independently, so a record
    that failed (rolled back, never written) followed by a LATER record for the SAME actor that
    SUCCEEDED left the cursor sitting past the failed one's seq -- the failed record then read as
    "already ingested" forever, a silent, permanent GAP, exactly what this story's own done_when
    forbids. `blocked` closes it: the moment one of this actor's records fails, every later
    record for that SAME actor in THIS run is deferred without being attempted, so the cursor can
    never advance past a hole for that actor. The accepted trade: a record that fails for a
    reason THIS run cannot recover from (not a transient lock, a genuinely bad value) stalls
    every later record from that one actor until fixed -- preferred over the alternative, which
    is silently losing a record forever. A DIFFERENT actor's records are entirely unaffected
    (blocked is keyed per actor, not global) -- one actor's bad record must not stall the whole
    ingest either, same "guard the computation, degrade the record" shape as everywhere else in
    this package."""
    project_root = pathlib.Path(project_root)
    sdlc_dir = pathlib.Path(sdlc_dir) if sdlc_dir is not None else project_root / ".sdlc"
    project_id = project_id_for(project_root)

    try:
        records = read_all_with_reliability(sdlc_dir)
    except Exception:
        # Defense in depth, mirroring git_reader.ingest_merge_lead_time's own outer guard
        # around find_merge_events: read_all_with_reliability reuses read_all's own
        # never-raises helpers, but a future edit to either could reintroduce a crash here the
        # way #103's own history shows for a different reader -- guarded regardless.
        records = []

    start_cursor = _load_cursor(conn, project_id)
    blocked = set()  # actor_keys with a write failure THIS run -- see this function's own
                      # docstring for the gap this closes
    events, handoffs, skipped = 0, 0, 0
    for record in records:
        try:
            actor_key = _actor_key(record)
            seq = seq_of(record)
            if actor_key in blocked:
                continue  # an earlier record for this SAME actor already failed this run
            if actor_key in start_cursor and seq <= start_cursor[actor_key]:
                continue  # already ingested in a prior run -- identity-keyed skip, see docstring
            kind = record.get("kind")
            conn.execute("BEGIN TRANSACTION")
            try:
                if kind in _HANDOFF_KINDS:
                    if kind == "handoff":
                        _apply_handoff(conn, project_id, record)
                    else:
                        _apply_ack(conn, project_id, record)
                    handoffs += 1
                else:
                    _write_event(conn, project_id, record)
                    events += 1
                _advance_cursor(conn, project_id, actor_key, seq)
                conn.execute("COMMIT")
            except Exception:
                # A well-typed record can still surprise DuckDB at INSERT/UPDATE time (an
                # unforeseen value shape) -- ROLLBACK undoes the row write too, so this record
                # is cleanly excluded (never landed-but-uncursored) rather than half-applied.
                # Same reasoning as artifact_reader.ingest_artifacts' per-goal transaction.
                conn.execute("ROLLBACK")
                blocked.add(actor_key)
                skipped += 1
                continue
        except Exception:
            # One record's OWN shape breaking this loop's bookkeeping (actor/seq/kind
            # extraction, not the SQL -- that's the inner guard above) must not abort every
            # other record's ingest either. actor_key may not even be bound yet here, so this
            # record's own actor cannot be added to `blocked` -- it is simply retried next run,
            # same as any other skip.
            skipped += 1
            continue
    return {"events": events, "handoffs": handoffs, "skipped": skipped}
