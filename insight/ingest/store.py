# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""DuckDB store bootstrap (issue #99, E1.S1): open/create the store and ensure schema.

Scope is schema bootstrap plus a narrow, additive schema evolution — no collector
adapter, no ledger reading, no rows written, no `phase_trace_completeness`
computation. `ensure_schema` runs twelve `CREATE TABLE IF NOT EXISTS` statements,
then eleven idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements — two
added by issue #102, a third by issue #103, three more by issue #104, two
(dim_project.adopted/skip_reason) by issue #106, a ninth (fact_event.model) by
issue #146, and a final two (fact_event.why, fact_event.grade) by issue #147
(see .sdlc/plans/102.md §B, .sdlc/plans/103.md
§C, .sdlc/plans/104.md §A, .sdlc/plans/106.md Design decision D,
.sdlc/plans/146.md Design decision 4, and .sdlc/plans/147.md Design decision 1
for why a plain CREATE-only approach can't
add a column to a store file an earlier story already created). This ALTER set
is additive-only — no type changes, no drops, no `information_schema` diffing —
and is NOT a general migration framework: a future story that needs to change a
column's TYPE, or drop one, still needs to introspect `information_schema.columns`
and diff against the expected shape, exactly as this docstring said before #102;
that remains explicitly not built here.

A THIRD PHASE (issue #380) runs after both, and is the ONE recorded exception to the
paragraph above — still not a framework, just a single one-shot conversion that the
additive `ALTER` mechanism structurally cannot express, because it changes a PRIMARY
KEY. It widens `ingest_ledger_cursor`'s key from `(project_id, actor_id)` to
`(project_id, actor_id, writer_id, stream)`; see `_migrate_cursor_key` below for the
mechanics and `insight/ingest/ledger_writer.py`'s own "#380" docstring section for why
the old per-actor key was silently losing records. It is guarded on
`information_schema.columns` — exactly the introspection this docstring has always
said a non-additive change would need — so it fires at most once per store and is a
no-op on a fresh one. It does only the STRUCTURAL half (copy the old rows into a
carrier table, drop, recreate at the new shape) and is deliberately non-destructive of
every `fact_*` table: `ensure_schema` runs on EVERY `open_store()`, including the
read-intent commands (`insight gaps`, `dash`, `ic`, `manager`, `panel`, `leadership`,
`cross-functional`), so a destructive statement here would fire on a command that only
meant to render. The rebuild that consumes the carrier lives in `ingest_ledger`,
per-project and lazy, inside the very call that immediately refills the rows.

`ingest_ledger_cursor` (issue #105, E1.S7) is the eleventh table, a NEW CREATE
rather than an ALTER (same footing as fact_collector_pack/fact_slice/
fact_merge_lead_time/fact_pr_review/fact_pr_check before it) — the resume
watermark `insight/ingest/ledger_writer.py` reads/advances so a second `insight
ingest` run neither re-writes a ledger record it already landed in fact_event/
fact_handoff nor skips one a partial run never reached. `ingest_ledger_cursor_legacy`
(issue #380) is the twelfth, and carries the conversion above: it holds each
pre-migration `(project_id, actor_id, last_seq)` row verbatim, which is at once the
per-project "this project's ledger facts predate the new key, rebuild them" marker and
the audit trail of where every actor's watermark stood before the change. Neither is
named `fact_*`/`dim_*`: they are not facts about the project, they are this ingest
path's own bookkeeping, the same distinction that keeps them out of
docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md §B.3's
star schema entirely.

Column types are decisions, not spec-given (the spec at
docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md §B.3 lists
column names and PKs only) — see .sdlc/plans/99.md's design-decisions table for the
type-by-type rationale. Two columns carry real DuckDB CHECK constraints per issue
guidance: `fact_event.reliability_class IN (1, 2)` and
`fact_goal.phase_trace_completeness BETWEEN 0 AND 1`, both nullable because no
ingester writes them yet in this story.
"""
import pathlib

import duckdb

#: Default store location: .sdlc/ already holds config/goals/ledger/state for a
#: project, and the root .gitignore already ignores it wholesale (.gitignore:7), so
#: this file needs no new gitignore entry. Resolved relative to CWD at run time (via
#: resolve_db_path), not at import time, so a module-level Path never bakes in the
#: importing process's CWD.
DEFAULT_DB_PATH = pathlib.Path(".sdlc") / "insight.duckdb"

#: The tables ensure_schema creates. The first five are #99's schema bootstrap (spec §B.3);
#: fact_collector_pack (#100), fact_slice (#102), fact_merge_lead_time (#103), fact_pr_review
#: and fact_pr_check (#104), ingest_ledger_cursor (#105) and ingest_ledger_cursor_legacy (#380)
#: are design decisions of those stories, not part of spec §B.3 -- see .sdlc/plans/100.md §C,
#: .sdlc/plans/102.md §C, .sdlc/plans/103.md §C, .sdlc/plans/104.md §A.
TABLES = ("dim_project", "dim_actor", "fact_goal", "fact_event", "fact_handoff",
          "fact_collector_pack", "fact_slice", "fact_merge_lead_time",
          "fact_pr_review", "fact_pr_check", "ingest_ledger_cursor",
          "ingest_ledger_cursor_legacy")

#: Named, not inlined into _DDL, because `_migrate_cursor_key` recreates the table from this
#: EXACT text after dropping the old one -- a second, hand-copied CREATE would be free to drift
#: from the one a fresh store gets, and the whole point of the conversion is that a migrated
#: store and a fresh store end up indistinguishable.
#:
#: `writer_id` is `<actor>:<pid>` for a post-#337 `<actor>:<pid>:<seq>` id and the bare actor for
#: a legacy 2-part one; `stream` is the reader's own source glob ('entries'/'events'/
#: 'local-events'). Both are part of the key because ledger.py derives `seq` from the LINE COUNT
#: of the single file it appends to, and that file is per (actor, pid, stream) -- so one actor's
#: records arrive carrying several independent counters, each needing its own watermark. Neither
#: column may be NULL (no PRIMARY KEY column may), which is why ledger_writer's "" sentinel
#: convention for a missing actor extends to both.
_CURSOR_DDL = """
    CREATE TABLE IF NOT EXISTS ingest_ledger_cursor (
        project_id VARCHAR,
        actor_id VARCHAR,
        writer_id VARCHAR,
        stream VARCHAR,
        last_seq BIGINT,
        PRIMARY KEY (project_id, actor_id, writer_id, stream)
    )
"""

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS dim_project (
        project_id VARCHAR PRIMARY KEY,
        repo VARCHAR,
        remote_url_sha256 VARCHAR,
        north_star_present BOOLEAN,
        config_json VARCHAR,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_actor (
        actor_id VARCHAR PRIMARY KEY,
        handle VARCHAR,
        areas VARCHAR[]
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_goal (
        project_id VARCHAR,
        goal_id VARCHAR,
        title VARCHAR,
        lane VARCHAR,
        source VARCHAR,
        done_when_present BOOLEAN,
        plan_artifact_present BOOLEAN,
        created_ts TIMESTAMP,
        claimed_ts TIMESTAMP,
        first_done_ts TIMESTAMP,
        terminal_ts TIMESTAMP,
        outcome VARCHAR,
        pr INTEGER,
        issue INTEGER,
        retro_grade VARCHAR,
        verify_state VARCHAR,
        phase_trace_completeness DOUBLE CHECK (
            phase_trace_completeness IS NULL
            OR (phase_trace_completeness BETWEEN 0 AND 1)
        ),
        PRIMARY KEY (project_id, goal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_event (
        project_id VARCHAR,
        goal_id VARCHAR,
        ts TIMESTAMP,
        actor_id VARCHAR,
        kind VARCHAR,
        phase VARCHAR,
        gate VARCHAR,
        verdict VARCHAR,
        cycle INTEGER,
        ms BIGINT,
        tokens_in BIGINT,
        tokens_out BIGINT,
        cost_cents BIGINT,
        reason_class VARCHAR,
        ok BOOLEAN,
        exit_code INTEGER,
        reliability_class TINYINT CHECK (
            reliability_class IS NULL OR reliability_class IN (1, 2)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_handoff (
        project_id VARCHAR,
        from_actor VARCHAR,
        to_actor VARCHAR,
        area VARCHAR,
        issue INTEGER,
        priority VARCHAR,
        opened_ts TIMESTAMP,
        ack_ts TIMESTAMP,
        ack_state VARCHAR,
        settled_ts TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_collector_pack (
        project_id VARCHAR,
        schema VARCHAR,
        collected_ts TIMESTAMP,
        window_since_days INTEGER,
        window_oldest_sha VARCHAR,
        window_oldest_date VARCHAR,
        window_newest_sha VARCHAR,
        window_newest_date VARCHAR,
        window_commit_count INTEGER,
        degraded_collector VARCHAR[],
        degraded_adapter VARCHAR[],
        raw_payload VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_slice (
        project_id VARCHAR,
        goal_id VARCHAR,
        slice_id VARCHAR,
        title VARCHAR,
        size VARCHAR,
        status VARCHAR,
        needs VARCHAR[],
        files VARCHAR[],
        PRIMARY KEY (project_id, goal_id, slice_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_merge_lead_time (
        project_id VARCHAR,
        merge_sha VARCHAR,
        kind VARCHAR,
        pr_number INTEGER,
        merge_ts TIMESTAMP,
        first_commit_sha VARCHAR,
        first_commit_ts TIMESTAMP,
        lead_time_seconds BIGINT,
        degraded VARCHAR[],
        PRIMARY KEY (project_id, merge_sha)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_pr_review (
        project_id VARCHAR,
        pr_number INTEGER,
        source VARCHAR,
        event_id VARCHAR,
        actor VARCHAR,
        verdict VARCHAR,
        event_ts TIMESTAMP,
        pr_created_ts TIMESTAMP,
        pr_merged_ts TIMESTAMP,
        seconds_since_pr_created BIGINT,
        degraded VARCHAR[],
        PRIMARY KEY (project_id, source, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_pr_check (
        project_id VARCHAR,
        pr_number INTEGER,
        check_name VARCHAR,
        status VARCHAR,
        conclusion VARCHAR,
        started_ts TIMESTAMP,
        completed_ts TIMESTAMP,
        pr_created_ts TIMESTAMP,
        pr_merged_ts TIMESTAMP,
        degraded VARCHAR[],
        PRIMARY KEY (project_id, pr_number, check_name)
    )
    """,
    _CURSOR_DDL,
    """
    CREATE TABLE IF NOT EXISTS ingest_ledger_cursor_legacy (
        project_id VARCHAR,
        actor_id VARCHAR,
        last_seq BIGINT
    )
    """,
)

#: Narrow, additive-only schema evolution -- see .sdlc/plans/102.md Design decision B for the
#: mechanism's origin. Statements 3 is issue #103's; 4-6 are issue #104's (fact_collector_pack's
#: gh-facts/v1 summary columns) -- see .sdlc/plans/104.md §A. Statement 9 is issue #146's
#: (fact_event.model, needed by metric 17's "cost per landed goal, by lane and model") -- see
#: .sdlc/plans/146.md Design decision 4. Statements 10-11 are issue #147's own
#: (fact_event.why, fact_event.grade) -- `why` is metric 27's decision-gate-denial id extraction
#: source, `grade` is metric 29's retro-grade-mix trend source; see .sdlc/plans/147.md
#: Design decision 1.
_ALTER = (
    "ALTER TABLE fact_goal ADD COLUMN IF NOT EXISTS status VARCHAR",
    "ALTER TABLE fact_goal ADD COLUMN IF NOT EXISTS verify_command VARCHAR",
    "ALTER TABLE fact_collector_pack ADD COLUMN IF NOT EXISTS window_merge_count INTEGER",
    "ALTER TABLE fact_collector_pack ADD COLUMN IF NOT EXISTS window_pr_count INTEGER",
    "ALTER TABLE fact_collector_pack ADD COLUMN IF NOT EXISTS window_review_event_count INTEGER",
    "ALTER TABLE fact_collector_pack ADD COLUMN IF NOT EXISTS window_check_row_count INTEGER",
    "ALTER TABLE dim_project ADD COLUMN IF NOT EXISTS adopted BOOLEAN",
    "ALTER TABLE dim_project ADD COLUMN IF NOT EXISTS skip_reason VARCHAR",
    "ALTER TABLE fact_event ADD COLUMN IF NOT EXISTS model VARCHAR",
    "ALTER TABLE fact_event ADD COLUMN IF NOT EXISTS why VARCHAR",
    "ALTER TABLE fact_event ADD COLUMN IF NOT EXISTS grade VARCHAR",
)


def resolve_db_path(db_path=None):
    """Resolve the store path: `db_path` if given, else `DEFAULT_DB_PATH`. Returns a
    `pathlib.Path`, not yet created or opened."""
    return pathlib.Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def _cursor_needs_writer_key(conn):
    """True exactly while `ingest_ledger_cursor` is still at its pre-#380 shape. The probe is the
    presence of `writer_id`, not a version row or a marker file: it is derived from the store's
    own catalog, so it cannot disagree with reality, and it is self-extinguishing -- the moment
    the conversion runs, the column exists and this returns False forever after."""
    columns = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ingest_ledger_cursor'"
    ).fetchall()
    return "writer_id" not in {row[0] for row in columns}


def _migrate_cursor_key(conn):
    """issue #380, the STRUCTURAL half of the cursor key widening: move every pre-migration row
    into `ingest_ledger_cursor_legacy`, then drop and recreate the cursor at its new shape.

    The old rows are moved rather than converted because they CANNOT be converted. A row says
    "project P, actor alice, seq 5" and answers neither "which writer process" nor "which stream"
    -- and that information exists nowhere else, not even in the fact tables the rows describe
    (`fact_event` carries no record identity, and `fact_handoff` merges a hand-off and every later
    ack into one last-write-wins row). Applied as a floor for unknown writers/streams the old
    value would keep swallowing exactly the records this change exists to stop swallowing; dropped
    without a floor it would re-ingest already-landed records as duplicates, since neither fact
    table has a dedup constraint. So the derived rows are rebuilt from the ledger JSONL instead --
    `insight/ingest/ledger_writer.ingest_ledger` does that, per project and lazily, consuming the
    carrier row as it goes.

    ONE TRANSACTION. Verified against DuckDB directly, including a SIGKILL mid-transaction: after
    a hard kill the old cursor table is intact with all its rows and the carrier is empty, so
    there is no window in which a store is left with neither. A failure ROLLBACKs to exactly that
    same state and re-raises -- `ensure_schema` has never swallowed DDL errors, and a store whose
    conversion did not complete must not then be used as though it had."""
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "INSERT INTO ingest_ledger_cursor_legacy (project_id, actor_id, last_seq) "
            "SELECT project_id, actor_id, last_seq FROM ingest_ledger_cursor"
        )
        conn.execute("DROP TABLE ingest_ledger_cursor")
        conn.execute(_CURSOR_DDL)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            # A DuckDB statement error already aborts its transaction, so the explicit ROLLBACK
            # can itself report "no transaction is active" -- never let that mask the real cause.
            pass
        raise


def ensure_schema(conn):
    """Run the twelve idempotent `CREATE TABLE IF NOT EXISTS` statements, then the eleven
    idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements (issues
    #102/#103/#104/#106/#146/#147 -- see the module docstring and .sdlc/plans/102.md §B /
    .sdlc/plans/103.md §C / .sdlc/plans/104.md §A / .sdlc/plans/106.md Design decision D /
    .sdlc/plans/146.md Design decision 4 / .sdlc/plans/147.md Design decision 1), and finally
    issue #380's one-shot `ingest_ledger_cursor` key conversion, against an
    already-open DuckDB connection. Safe to
    call repeatedly against the same connection or file, including a file created by an
    earlier story before these columns existed.

    The third phase runs LAST because the first two are no-ops against the old cursor table (the
    CREATE is `IF NOT EXISTS` and the table already exists; no ALTER names it), so only the
    conversion can actually change its shape. It is structural only -- it never deletes a
    `fact_*` row -- because this function runs on every `open_store()`, read-intent commands
    included."""
    for ddl in _DDL:
        conn.execute(ddl)
    for ddl in _ALTER:
        conn.execute(ddl)
    if _cursor_needs_writer_key(conn):
        _migrate_cursor_key(conn)


def open_store(db_path=None):
    """Resolve `db_path`, create its parent directory if missing, open (or create) the
    DuckDB file, ensure the schema exists, and return the open connection."""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    ensure_schema(conn)
    return conn


def open_store_read_only(db_path=None):
    """issue #299 [E16.S1]: open an EXISTING store read-only, for a consumer (the FastAPI
    service) that must never be able to write. Resolves `db_path` via `resolve_db_path`, same
    as `open_store` -- reused, not re-derived (BR-3).

    Deliberately NOT a read-only-flagged call to `open_store`'s own body: `open_store` always
    calls `ensure_schema`, which runs `CREATE TABLE`/`ALTER TABLE` DDL -- both raise
    `duckdb.InvalidInputException` against a read-only connection (verified directly, see
    .sdlc/plans/299.md Task 1.3's scratch transcript), so schema-ensuring and read-only are
    mutually exclusive on the same call. This function also never creates the parent directory
    the way `open_store` does -- a read-only opener must have zero side effects on a path that
    doesn't exist yet; it raises instead.

    Raises `FileNotFoundError` if the resolved path is not an existing file (DuckDB's own
    `read_only=True` open against a missing file raises `duckdb.IOException`, a less specific,
    harder-to-catch-precisely signal for the caller -- `/health`'s cold-start "missing" state
    needs a exact, catchable type, so this function normalizes to the stdlib exception before
    ever calling into duckdb)."""
    path = resolve_db_path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"DuckDB store not found: {path}")
    return duckdb.connect(str(path), read_only=True)


def has_any_rows(conn):
    """issue #299 [E16.S1]: a structural presence check, not a metric -- shares no SQL with
    any of insight/metrics/*.sql's 34 files (Decision 3). Iterates the existing TABLES constant
    (the schema's own source of truth, not re-listed here) and short-circuits on the first table
    with at least one row. Table names come from the fixed, internal TABLES tuple, never from
    user input -- no injection surface, the same trust `ensure_schema` already places in these
    same names when interpolating them into DDL."""
    for table in TABLES:
        if conn.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]:
            return True
    return False
