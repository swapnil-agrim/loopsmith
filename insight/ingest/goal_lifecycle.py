# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Goal lifecycle derivation from fact_event (issue #217): populates fact_goal's
claimed_ts/first_done_ts/terminal_ts/outcome for a github-mode repo by REPLAYING
fact_event -- the ledger events already ingested by insight.ingest.ledger_writer.ingest_ledger
-- rather than reading .sdlc/goals/*.md files, which share no key with fact_event under github
mode (a goal_id there is a zero-padded local frontmatter id; fact_event.goal_id is the bare
github issue number the loop's own ledger.py:append writes). See .sdlc/plans/217.md Decisions
1-4 for the full research/design record; this module implements exactly that, nothing more.

NEVER RAISES. `insight/` is offline-safe by hard rule -- this module makes no `gh` call and no
network request; every one of the four derived columns is fully computable from fact_event rows
already sitting in the store after ingest_ledger runs earlier in the same `insight ingest` call
(see insight/__main__.py:_ingest_one_repo -- this must be wired in AFTER ingest_ledger, order
matters).

reliability_class = 1 (bare equality, not != 2) is enforced on every read here, matching
metrics/13.sql's and metrics/14.sql's own established convention: a NOW metric must never read a
reliability_class=2 row (spec line 563), and a real ingested ledger row is never NULL by
ledger_writer.py's own contract, so excluding NULL identically to 2 is correct, not a legacy
grandfather.

Decision 3 (this module's `_discovery_source`) and Decision 3's OTHER copy in
insight/ingest/artifact_reader.py are a DELIBERATE second implementation of the same
seven-line config read gh_reader.py's `_repo_from_config` and ledger_reader.py's
`_telemetry_share_is_off` already established -- never a shared config module. The
plugin/product boundary tests/test_import_boundary.py enforces means insight/ cannot import a
skills/sdlc-loop/scripts/*.py config reader, and insight/ has no shared internal config module
either.
"""
import json
import pathlib

from insight.ingest.packs import project_id_for


def _discovery_source(sdlc_dir):
    """.sdlc/config.json's discovery.source, or "local-goals" on anything missing/unreadable/
    malformed -- byte-for-byte the same shape as gh_reader._repo_from_config /
    ledger_reader._telemetry_share_is_off, and the same default sources.py's own factory uses
    (skills/sdlc-loop/scripts/sources.py:491-493). Never raises."""
    try:
        raw = (pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8-sig", errors="replace")
        config = json.loads(raw)
    except (OSError, ValueError):
        return "local-goals"
    discovery = config.get("discovery") if isinstance(config, dict) else None
    source = discovery.get("source") if isinstance(discovery, dict) else None
    return source if isinstance(source, str) and source else "local-goals"


# Decision 4: purge fact_goal rows for this project, under github mode, that no longer have a
# matching fact_event row -- scoped to (project_id, github-mode, not in fact_event), run BEFORE
# the Decision-1 insert so a stale row (e.g. this repo's own 18 local-mode '0001'..'0018' rows)
# does not linger forever with every lifecycle column NULL.
_PURGE_STALE_SQL = """
    DELETE FROM fact_goal
    WHERE project_id = ?
      AND goal_id NOT IN (
        SELECT DISTINCT goal_id FROM fact_event
        WHERE project_id = ? AND goal_id IS NOT NULL AND reliability_class = 1
      )
"""

# Decision 1: derive claimed_ts (FIRST claimed event, not latest -- Decision 2, the #105
# re-work case), first_done_ts (first done event, independent of outcome/terminal_ts by
# construction -- Decision 1), terminal_ts/outcome (the LATEST done/failed event via arg_max --
# Decision 2, "latest wins", the #124 two-done-events case). ON CONFLICT only sets these four
# columns -- the same "touch only the columns you own" contract as artifact_reader.write_goal's
# own upsert, so a later gh-metadata writer can safely upsert title/lane into the same row
# without collision (Decision 5).
_LIFECYCLE_UPSERT_SQL = """
    INSERT INTO fact_goal (project_id, goal_id, claimed_ts, first_done_ts, terminal_ts, outcome)
    SELECT
        project_id,
        goal_id,
        min(ts) FILTER (WHERE kind = 'claimed')                       AS claimed_ts,
        min(ts) FILTER (WHERE kind = 'done')                          AS first_done_ts,
        max(ts) FILTER (WHERE kind IN ('done', 'failed'))             AS terminal_ts,
        arg_max(kind, ts) FILTER (WHERE kind IN ('done', 'failed'))   AS outcome
    FROM fact_event
    WHERE project_id = ? AND goal_id IS NOT NULL AND reliability_class = 1
    GROUP BY project_id, goal_id
    ON CONFLICT (project_id, goal_id) DO UPDATE SET
        claimed_ts    = excluded.claimed_ts,
        first_done_ts = excluded.first_done_ts,
        terminal_ts   = excluded.terminal_ts,
        outcome       = excluded.outcome
"""


def derive_goals_from_events(conn, project_id):
    """Run the Decision-4 purge then the Decision-1 upsert for `project_id`, in one transaction.
    NEVER RAISES -- on any Exception, ROLLBACK and return {"goals": 0, "purged": 0}, the same
    idiom as artifact_reader.ingest_artifacts' own per-goal transaction.

    DuckDB has no changes() scalar function (verified live: Catalog Error) -- counts are a plain
    pre/post SELECT count(*) diff computed inside the same transaction: purged = before -
    after_purge; goals = after_insert - after_purge (rows newly inserted by this call, not a
    row-count of every upserted row -- an already-present row that only gets its lifecycle
    columns updated is not double-counted, matching ledger_writer.py's own "idempotent means
    identity-keyed, not row-count-stable" convention)."""
    conn.execute("BEGIN TRANSACTION")
    try:
        before = conn.execute(
            "SELECT count(*) FROM fact_goal WHERE project_id = ?", [project_id]
        ).fetchone()[0]
        conn.execute(_PURGE_STALE_SQL, [project_id, project_id])
        after_purge = conn.execute(
            "SELECT count(*) FROM fact_goal WHERE project_id = ?", [project_id]
        ).fetchone()[0]
        conn.execute(_LIFECYCLE_UPSERT_SQL, [project_id])
        after_insert = conn.execute(
            "SELECT count(*) FROM fact_goal WHERE project_id = ?", [project_id]
        ).fetchone()[0]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        return {"goals": 0, "purged": 0}
    return {"goals": after_insert - after_purge, "purged": before - after_purge}


def ingest_goal_lifecycle(conn, project_root, sdlc_dir=None):
    """The write orchestration `insight ingest` calls, immediately after ingest_ledger (order
    matters -- see module docstring). No-op, no query at all, unless discovery.source ==
    "github" for this project (Decision 3) -- a local-mode repo's fact_goal rows (written by
    artifact_reader.ingest_artifacts from .sdlc/goals/*.md) are byte-for-byte untouched."""
    project_root = pathlib.Path(project_root)
    sdlc_dir = pathlib.Path(sdlc_dir) if sdlc_dir is not None else project_root / ".sdlc"
    if _discovery_source(sdlc_dir) != "github":
        return {"goals": 0, "purged": 0}
    project_id = project_id_for(project_root)
    return derive_goals_from_events(conn, project_id)
