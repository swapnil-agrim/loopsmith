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


def test_a_non_adopters_ingest_leaves_the_verdict_unaffected(tmp_path, monkeypatch):
    """The test the reviewer asked for, verified live (BLOCKING finding on issue #146, now
    fixed): before this fix, `insight ingest` with `--claude-analytics` OMITTED still wrote one
    claude-analytics/v1 fact_collector_pack row on EVERY run, degraded with "analytics_disabled"
    -- and because this rule is deliberately all-schema with no exclusion (see the module
    docstring above), that row alone was enough to permanently drag every previously-clean
    project's `insight gaps` verdict from PASS to WARN, via an "action" (fix the underlying
    condition) that had nothing to fix: the collector was never asked to run in the first place.

    This runs the REAL `insight ingest` CLI path (not a hand-built fixture) with the flag off,
    then evaluates THIS rule, via the same evaluate_rule() path every other test in this file
    uses, over the resulting store -- proving the fix end to end, not just at the unit level.

    Deliberately does NOT assert the overall severity is PASS: `insight ingest` in this hermetic,
    gh-less sandbox also writes a genuinely, pre-existingly degraded gh-facts/v1 row (no `gh` on
    PATH -- unrelated to this issue, see insight/tests/test_cli.py's own isolate_path_empty
    fixture for why that is expected and correct), so the rule's overall verdict is legitimately
    WARN here regardless of the analytics fix. The claim this test pins is narrower and more
    precise: schema='claude-analytics/v1' contributes NEITHER a fact_collector_pack row NOR an
    evidence entry -- i.e. a non-adopter's verdict is unaffected BY THIS FEATURE, whatever it
    otherwise is for unrelated reasons."""
    duckdb = pytest.importorskip("duckdb")
    from insight.__main__ import main

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    try:
        # Store-level proof: no claude-analytics/v1 row exists at all -- a non-adopter's ingest
        # never called ingest_analytics_reader (post-#146 fix), so there is nothing for it to
        # write, not even a degraded one.
        pack_count = conn.execute(
            "select count(*) from fact_collector_pack where schema = 'claude-analytics/v1'"
        ).fetchone()[0]
        assert pack_count == 0

        # Gap-rule-level proof, via the SAME evaluate_rule() path every other test in this file
        # exercises: whatever this rule's overall verdict is, it is never because of
        # claude-analytics/v1 -- that schema contributes zero evidence rows.
        registry = load_gap_rules()
        finding = evaluate_rule(conn, registry["coverage_degraded_collector"])
        evidence_schemas = {row["schema"] for row in finding["evidence"]}
        assert "claude-analytics/v1" not in evidence_schemas
    finally:
        conn.close()


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
