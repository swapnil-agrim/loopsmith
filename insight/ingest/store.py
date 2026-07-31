# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""DuckDB store bootstrap (issue #99, E1.S1): open/create the store and ensure schema.

Scope is schema bootstrap plus one narrow, additive schema evolution — no collector
adapter, no ledger reading, no rows written, no `phase_trace_completeness`
computation. `ensure_schema` runs seven `CREATE TABLE IF NOT EXISTS` statements,
then two idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements added by
issue #102 (see .sdlc/plans/102.md §B for why a plain CREATE-only approach can't
add a column to a store file an earlier story already created). This ALTER pair is
additive-only — no type changes, no drops, no `information_schema` diffing — and is
NOT a general migration framework: a future story that needs to change a column's
TYPE, or drop one, still needs to introspect `information_schema.columns` and diff
against the expected shape, exactly as this docstring said before #102; that
remains explicitly not built here.

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
#: fact_collector_pack (#100) and fact_slice (#102) are design decisions of those stories, not
#: part of spec §B.3 -- see .sdlc/plans/100.md §C and .sdlc/plans/102.md §C.
TABLES = ("dim_project", "dim_actor", "fact_goal", "fact_event", "fact_handoff",
          "fact_collector_pack", "fact_slice")

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
)

#: Narrow, additive-only schema evolution (issue #102, the first of its kind in this file) --
#: ADD COLUMN IF NOT EXISTS only, never a type change or a drop, never information_schema
#: diffing. Exists specifically so a store file created by an EARLIER story (#99/#100/#101,
#: fact_goal with 17 columns) gets these two new columns too, not just a fresh file -- CREATE
#: TABLE IF NOT EXISTS alone is a no-op against an already-existing table. See
#: .sdlc/plans/102.md Design decision B.
_ALTER = (
    "ALTER TABLE fact_goal ADD COLUMN IF NOT EXISTS status VARCHAR",
    "ALTER TABLE fact_goal ADD COLUMN IF NOT EXISTS verify_command VARCHAR",
)


def resolve_db_path(db_path=None):
    """Resolve the store path: `db_path` if given, else `DEFAULT_DB_PATH`. Returns a
    `pathlib.Path`, not yet created or opened."""
    return pathlib.Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def ensure_schema(conn):
    """Run the seven idempotent `CREATE TABLE IF NOT EXISTS` statements, then the two
    idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements (issue #102 -- see the
    module docstring and .sdlc/plans/102.md §B), against an already-open DuckDB connection.
    Safe to call repeatedly against the same connection or file, including a file created by
    an earlier story before these columns existed."""
    for ddl in _DDL:
        conn.execute(ddl)
    for ddl in _ALTER:
        conn.execute(ddl)


def open_store(db_path=None):
    """Resolve `db_path`, create its parent directory if missing, open (or create) the
    DuckDB file, ensure the schema exists, and return the open connection."""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    ensure_schema(conn)
    return conn
