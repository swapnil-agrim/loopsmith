# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/coverage_degraded_collector.sql (issue #117, E3.S2, Task 2; see
.sdlc/plans/117.md Design decision 4). Spec section B.3.1's own sentence made literal:
degraded[] IS the ABSENT signal, consumed directly rather than re-derived from a computed
denominator (that is coverage_gate_absent's separate, different mechanism)."""
import datetime

import pytest

from insight.gaps.evaluate import evaluate_rule
from insight.gaps.loader import load_gap_rules


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def registry():
    return load_gap_rules()


def test_flags_any_pack_with_a_collector_or_adapter_code_and_leaves_clean_packs_alone(
    conn, registry
):
    """Three packs, project p1, distinct schemas, same collected_ts. Row 1
    (alignment-collect/v1) has a degraded_collector code. Row 2 (git-facts/v1) is clean. Row 3
    (gh-facts/v1) has a degraded_adapter code.

    Hand-computed: population = count(*) = 3 (> 0). Row 1: len(['no_test_command'])=1 > 0 ->
    flagged. Row 2: both arrays empty -> not flagged. Row 3:
    len(['gh_invalid_window_days'])=1 > 0 -> flagged. Evidence, ordered by project_id, schema,
    collected_ts ('alignment-collect/v1' < 'gh-facts/v1' lexically)."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter) VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', ?, ?)",
        [["no_test_command"], []],
    )
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter) VALUES "
        "('p1', 'git-facts/v1', '2026-01-01', ?, ?)",
        [[], []],
    )
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter) VALUES "
        "('p1', 'gh-facts/v1', '2026-01-01', ?, ?)",
        [[], ["gh_invalid_window_days"]],
    )
    finding = evaluate_rule(conn, registry["coverage_degraded_collector"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [
        {"project_id": "p1", "schema": "alignment-collect/v1",
         "collected_ts": datetime.datetime(2026, 1, 1),
         "degraded_collector": ["no_test_command"], "degraded_adapter": []},
        {"project_id": "p1", "schema": "gh-facts/v1",
         "collected_ts": datetime.datetime(2026, 1, 1),
         "degraded_collector": [], "degraded_adapter": ["gh_invalid_window_days"]},
    ]


def test_a_pack_degraded_only_by_a_null_array_column_is_not_flagged(conn, registry):
    """One row with degraded_collector=NULL (genuinely omitted), degraded_adapter=[] -- proves
    COALESCE(len(...), 0) treats a genuinely NULL array the same as empty, not a crash and not
    a false positive.

    Named explicitly, per the plan's own guardrail, as schema-defensive and unreached by the
    real writer: packs.py:153-164's write_pack always passes an explicit list, possibly empty,
    never None -- this state cannot occur via any real `insight ingest` run today; the test
    exists to pin the guard's own behaviour against the nullable VARCHAR[] column type the
    schema still allows, not because the real writer can produce it.

    Hand-computed: population 1, evidence 0 rows -> PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter) VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', NULL, ?)",
        [[]],
    )
    rule = registry["coverage_degraded_collector"]
    assert evaluate_rule(conn, rule) == {
        "class": "Coverage", "metric": "ingest_reliability", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_non_alignment_collect_schema_still_counts_toward_population_and_can_be_flagged(
    conn, registry
):
    """A single git-facts/v1 row with degraded_collector=['no_git'] only (no
    alignment-collect/v1 row at all in the store).

    Hand-computed: population 1 (all-schema, per this rule's own deliberate scoping choice),
    evidence 1 row -> WARN. This is the case that would silently vanish if population were
    mistakenly scoped to schema = 'alignment-collect/v1' the way coverage_gate_absent's is."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter) VALUES "
        "('p1', 'git-facts/v1', '2026-01-01', ?, ?)",
        [["no_git"], []],
    )
    finding = evaluate_rule(conn, registry["coverage_degraded_collector"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [
        {"project_id": "p1", "schema": "git-facts/v1",
         "collected_ts": datetime.datetime(2026, 1, 1),
         "degraded_collector": ["no_git"], "degraded_adapter": []},
    ]
