# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""DuckDB store bootstrap (issue #99, E1.S1): open/create the store and ensure schema.

Scope is schema bootstrap only — no collector adapter, no ledger reading, no rows
written, no `phase_trace_completeness` computation. `ensure_schema` runs five
`CREATE TABLE IF NOT EXISTS` statements against an open connection; that idempotent
CREATE is the entire correctness story for "re-running never duplicates anything" in
v1 — no `schema_version` table, no `ALTER` diffing, no migration framework. A future
story that needs to change a column's type or add a column to an existing on-disk
store will need to introspect `information_schema.columns` and diff against the
expected shape; that is explicitly not built here.

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

#: The five tables this story creates. Order matches the tuple used for the
#: CREATE TABLE statements below.
TABLES = ("dim_project", "dim_actor", "fact_goal", "fact_event", "fact_handoff")

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
)


def resolve_db_path(db_path=None):
    """Resolve the store path: `db_path` if given, else `DEFAULT_DB_PATH`. Returns a
    `pathlib.Path`, not yet created or opened."""
    return pathlib.Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def ensure_schema(conn):
    """Run the five idempotent `CREATE TABLE IF NOT EXISTS` statements against an
    already-open DuckDB connection. Safe to call repeatedly against the same
    connection or file — a no-op against an already-correct schema."""
    for ddl in _DDL:
        conn.execute(ddl)


def open_store(db_path=None):
    """Resolve `db_path`, create its parent directory if missing, open (or create) the
    DuckDB file, ensure the schema exists, and return the open connection."""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    ensure_schema(conn)
    return conn
