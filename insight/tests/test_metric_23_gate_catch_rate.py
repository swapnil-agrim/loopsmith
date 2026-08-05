# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #23, Gate catch rate by gate (issue #147, [E7.S4]). See .sdlc/plans/147.md Design
decision 4: the late-catch classification is a 12-way CASE over ledger.GATE_KINDS's full
vocabulary, every value named explicitly -- plan_review is never late (no code exists yet to
review); code_review/post_review/merge are always late (merge() is structurally unreachable
until a PR exists); decision/alignment are NULL because they are positioned outside the
implement-vs-review pipeline by design (sentinel goal ids, fire at any point); verify and the
five risk_* gates are ALSO NULL, but for a genuinely different reason -- they have zero writers
anywhere in the codebase, so their pipeline position is simply unknown, not merely unattached.

This view is functionally SHALLOW-dark against any real, currently-ingested store
(`ledger_writer.py`'s `_write_event` never populates `gate`/`verdict`/`cycle` -- #243): every
fixture here bypasses the ledger/ingest pipeline entirely and inserts directly into
`fact_event`, exactly like every other metric test in this directory."""
import json
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "23.jsonl"
METRIC_PATH = pathlib.Path(__file__).resolve().parents[1] / "metrics" / "23.sql"

# Issue #298 ([E15.S4]): this used to AST-parse skills/sdlc-loop/scripts/ledger.py's GATE_KINDS
# tuple off disk as TEXT, module-skipping both tests below whenever skills/ was absent (e.g. a
# standalone extraction of insight/ alone). Read from the frozen, versioned contract fixture
# instead -- GATE_KINDS is already pinned engine-side at tests/test_ledger.py:411-413, so this
# conversion needs no new engine work.
VOCAB = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "contract" / "vocabulary.json")
    .read_text(encoding="utf-8")
)


def _gate_kinds():
    return tuple(VOCAB["gate_kinds"])


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_gate_kinds_matches_the_contract():
    """Direct list-equality assertion against insight/contract/vocabulary.json's "gate_kinds" --
    no parser left to sanity-check (issue #298 removed it)."""
    assert _gate_kinds() == (
        "plan_review", "code_review", "post_review", "merge", "decision", "alignment",
        "verify", "risk_security", "risk_contract", "risk_migration", "risk_release",
        "risk_debug",
    )


def test_metric_23_case_enumerates_every_ledger_gate_kind():
    """Independent plan-review nit, closed mechanically: nothing pins the 12-way CASE in
    23.sql against the contract's gate_kinds itself beyond manual inspection -- a hypothetical
    future 13th gate kind would otherwise fall silently into the unreachable ELSE NULL branch
    with no signal a classification decision is newly owed for it. A plain substring/regex
    check against the file's raw text, not a SQL parser -- the CASE branches are already simple
    quoted-string literals, so no more machinery is warranted."""
    text = METRIC_PATH.read_text(encoding="utf-8")
    for gate_kind in _gate_kinds():
        assert f"'{gate_kind}'" in text, (
            f"23.sql's 12-way CASE does not mention gate kind {gate_kind!r} as a quoted "
            "literal -- a classification decision is owed for it"
        )


def test_metric_23_gate_catch_rate_matches_the_full_fixture(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["23"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_23 ORDER BY project_id, gate"))
    by_key = {(r["project_id"], r["gate"]): r for r in rows}
    assert len(rows) == 9

    p1_alignment = by_key[("p1", "alignment")]
    assert p1_alignment["late_catch"] is None
    assert p1_alignment["gate_event_count"] == 1
    assert p1_alignment["catch_count"] == 1
    assert p1_alignment["catch_rate"] == 1.0
    assert p1_alignment["avg_catch_cycle"] is None

    p1_code_review = by_key[("p1", "code_review")]
    assert p1_code_review["late_catch"] is True
    assert p1_code_review["gate_event_count"] == 1
    assert p1_code_review["catch_count"] == 1
    assert p1_code_review["catch_rate"] == 1.0

    p1_decision = by_key[("p1", "decision")]
    assert p1_decision["late_catch"] is None
    assert p1_decision["catch_count"] == 1

    p1_merge = by_key[("p1", "merge")]
    assert p1_merge["late_catch"] is True
    assert p1_merge["catch_count"] == 1

    p1_plan_review = by_key[("p1", "plan_review")]
    assert p1_plan_review["late_catch"] is False
    assert p1_plan_review["gate_event_count"] == 2
    assert p1_plan_review["catch_count"] == 1
    assert p1_plan_review["catch_rate"] == 0.5
    assert p1_plan_review["avg_catch_cycle"] is None

    p1_post_review = by_key[("p1", "post_review")]
    assert p1_post_review["late_catch"] is True
    assert p1_post_review["gate_event_count"] == 2
    assert p1_post_review["catch_count"] == 1
    assert p1_post_review["catch_rate"] == 0.5
    assert p1_post_review["avg_catch_cycle"] == 2.0  # only the blocked cycle (2), not (2+3)/2

    p1_verify = by_key[("p1", "verify")]
    assert p1_verify["late_catch"] is None
    assert p1_verify["catch_count"] == 1

    # Project-level headline share, identical on every p1 row: 3 late (code_review, merge,
    # post_review) out of 7 total catches.
    for key, row in by_key.items():
        if key[0] != "p1":
            continue
        assert row["project_catch_count"] == 7
        assert row["project_late_catch_count"] == 3
        assert row["project_late_catch_share"] == 0.4286

    p2_plan_review = by_key[("p2", "plan_review")]
    assert p2_plan_review["late_catch"] is False
    assert p2_plan_review["catch_count"] == 1

    p2_post_review = by_key[("p2", "post_review")]
    assert p2_post_review["late_catch"] is True
    assert p2_post_review["catch_count"] == 1
    # THE partition-proof assertion: p2's own avg_catch_cycle (5.0, from its own cycle=5 row)
    # must not blend with p1's post_review avg_catch_cycle (2.0).
    assert p2_post_review["avg_catch_cycle"] == 5.0

    for key, row in by_key.items():
        if key[0] != "p2":
            continue
        assert row["project_catch_count"] == 2
        assert row["project_late_catch_count"] == 1
        assert row["project_late_catch_share"] == 0.5

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] == 34
    assert "23" in html_text or "Gate catch rate" in html_text


def test_metric_23_avg_catch_cycle_partition_regression(conn):
    """Dedicated, explicit regression pin for the avg_catch_cycle partition proof -- already
    covered by the fixture assertions above, restated as its own inline-SQL test so a future
    SQL edit that accidentally drops project_id from a GROUP BY/JOIN fails loudly and
    specifically, not just as a symptom buried inside a larger assertion block."""
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('pa', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 2, 2),"
        "('pb', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 8, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT project_id, avg_catch_cycle FROM metric_23 ORDER BY project_id")
    )
    assert rows == [
        {"project_id": "pa", "avg_catch_cycle": 2.0},
        {"project_id": "pb", "avg_catch_cycle": 8.0},
    ]


def test_metric_23_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_23"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")  # must not raise CoverageDenominatorMissing


def test_metric_23_coverage_denominator_reflects_the_gate_population(conn):
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 1),"
        "('p1', 'g2', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 2),"
        "('p1', 'g3', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_23"))
    assert len(rows) == 1
    row = rows[0]
    assert row["class1_count"] == 1
    assert row["class2_count"] == 2
    assert row["total_count"] == 3
    assert row["coverage_pct"] == pytest.approx(1 / 3, abs=1e-4)
