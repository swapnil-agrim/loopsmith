# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #27, Decision-gate denials (issue #147, [E7.S4]). See .sdlc/plans/147.md Design
decision 5 -- the BLOCKING FINDING from an independent plan review: the draft extraction
pattern `regexp_extract(why, ':\\s*(\\S+)\\s+[^:]+:', 1)` silently returned a WRONG non-NULL id
('DEC-002:') for a title-less decision that has a rationale, because `\\S+` swallowed the
trailing colon of the id whole. The FIXED pattern, `^[^:]*:\\s*([^\\s:]+)`, is what this file
pins -- re-verified live against DuckDB 1.4.5 across all seven of the plan's enumerated
extraction shapes, including the fix's own regression case.

This view is functionally DEEP-dark against any real, currently-ingested store: fact_event.why
had no column at all before this story (#147 Design decision 1), and even once it exists,
ledger_writer.py's _write_event still never populates it (#243, separately deferred) -- every
fixture here bypasses the ledger/ingest pipeline entirely and inserts directly into fact_event,
exactly like every other metric test in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "27.jsonl"

_SUFFIX = (
    " — This is a recorded INVARIANT. If it genuinely needs to change, supersede it in "
    ".sdlc/decisions.json first; that is a decision, not an edit."
)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _insert_denial(conn, project_id, why, ts="2026-01-01T00:00:01", reliability_class=2):
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, why, reliability_class) VALUES "
        "(?, '(decision-gate)', ?, 'gate', 'decision', 'block', ?, ?)",
        [project_id, ts, why, reliability_class],
    )


def test_metric_27_decision_gate_denials_matches_the_full_fixture(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["27"]["reliability_class"] == 2

    rows = rows_as_dicts(
        conn.execute(
            "SELECT * FROM metric_27 ORDER BY project_id, decision_id_best_effort NULLS LAST"
        )
    )
    p1_rows = [r for r in rows if r["project_id"] == "p1"]
    p2_rows = [r for r in rows if r["project_id"] == "p2"]
    assert len(p1_rows) == 5
    assert len(p2_rows) == 1

    by_key = {(r["project_id"], r["decision_id_best_effort"], r["decision_id_extracted"]): r
              for r in rows}

    dec001 = by_key[("p1", "DEC-001", True)]
    assert dec001["denial_count"] == 2
    assert "max_x=9" in dec001["sample_why"]  # later ts wins via arg_max(why, ts)

    dec002 = by_key[("p1", "DEC-002", True)]
    assert dec002["denial_count"] == 1
    assert "DEC-002" in dec002["sample_why"]

    dec003 = by_key[("p1", "DEC-003", True)]
    assert dec003["denial_count"] == 1

    null_why = by_key[("p1", None, None)]
    assert null_why["denial_count"] == 1
    assert null_why["sample_why"] is None

    unparseable = by_key[("p1", None, False)]
    assert unparseable["denial_count"] == 1
    assert unparseable["sample_why"].startswith("app/config.py: :")

    assert dec001["total_denial_count"] == 6
    assert dec001["identified_denial_count"] == 4  # DEC-001(2) + DEC-002(1) + DEC-003(1)

    p2_dec001 = by_key[("p2", "DEC-001", True)]
    assert p2_dec001["denial_count"] == 1  # NOT 3 -- no cross-project merge on identical why text
    assert p2_dec001["total_denial_count"] == 1
    assert p2_dec001["identified_denial_count"] == 1

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] == 34
    assert "27" in html_text or "Decision-gate denials" in html_text


def test_metric_27_gate_not_decision_is_excluded(conn):
    """Fixture row (f): a post_review gate carrying a why-shaped string that itself contains
    'NOT-A-DECISION' -- proves the gate='decision' filter excludes it, not merely that the
    regex doesn't match it."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))
    for row in rows:
        assert row["sample_why"] is None or "NOT-A-DECISION" not in row["sample_why"]
    total = sum(r["denial_count"] or 0 for r in rows if r["project_id"] == "p1")
    assert total == 6  # the post_review row is not counted anywhere


def test_extraction_case_1_normal_id_and_title_extracts_correctly(conn):
    _insert_denial(
        conn, "p1",
        "hooks/decision_gate.py: DEC-100 A normal title: max_x=5 violates lte 5." + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] == "DEC-100"
    assert row["decision_id_extracted"] is True


def test_extraction_case_2_colon_in_title_still_extracts_correctly(conn):
    _insert_denial(
        conn, "p1",
        "hooks/decision_gate.py: DEC-101 A: title with a colon: max_x=5 violates lte 5." + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] == "DEC-101"
    assert row["decision_id_extracted"] is True


def test_extraction_case_3_multi_violation_drops_the_second_id(conn):
    _insert_denial(
        conn, "p1",
        "hooks/decision_gate.py: DEC-200 First: max_x=5 violates lte 5. "
        "| DEC-201 Second: max_y=9 violates lte 8." + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] == "DEC-200"  # only the FIRST id extracted
    assert row["decision_id_extracted"] is True


def test_extraction_case_5_title_less_with_rationale_now_extracts_correctly_the_blocking_fix(conn):
    """THE regression test pinning the fix: the independent plan review's own repro of the
    blocking bug (id DEC-002, title '', rationale present). The DRAFT pattern silently returned
    'DEC-002:' (non-NULL, wrong); the FIXED pattern shipped here returns 'DEC-002' (correct)."""
    _insert_denial(
        conn, "p1",
        "hooks/decision_gate.py: DEC-002: max_x=5 violates lte 5. x must stay <= 5 "
        "(Why: because it matters a lot)" + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] == "DEC-002"
    assert row["decision_id_best_effort"] != "DEC-002:"  # the exact defect the fix closes
    assert row["decision_id_extracted"] is True


def test_extraction_case_6_id_with_internal_whitespace_silently_truncates_the_sharpest_remaining_limitation(conn):
    """UNCHANGED by the fix, named as the single sharpest limitation remaining: an id containing
    internal whitespace ('DEC 001') is syntactically indistinguishable from a real single-word
    id, so extraction silently truncates to its first word ('DEC')."""
    _insert_denial(
        conn, "p1",
        "hooks/decision_gate.py: DEC 001 Title: max_x=5 violates lte 5." + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] == "DEC"  # truncated, silently wrong, not absent
    assert row["decision_id_extracted"] is True


def test_extraction_case_7_empty_id_lands_in_the_false_state_the_load_bearing_proof(conn):
    """The fixture's load-bearing proof that decision_id_extracted=FALSE is real and reachable
    post-fix: an empty-id registry-authoring shape (validate()-flagged but evaluate()-reachable,
    since validate() is advisory-only) collapses `ident` to '', so the capture group has nothing
    to match immediately after the prefix colon+space."""
    _insert_denial(
        conn, "p1",
        "app/config.py: : max_x=5 violates lte 5. x must stay <= 5" + _SUFFIX,
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] is None
    assert row["decision_id_extracted"] is False


def test_null_why_lands_in_the_null_state_distinguishable_from_false(conn):
    _insert_denial(conn, "p1", None)
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))[0]
    assert row["decision_id_best_effort"] is None
    assert row["decision_id_extracted"] is None
    assert row["decision_id_extracted"] is not False  # NULL and FALSE must not be conflated


def test_metric_27_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")  # must not raise CoverageDenominatorMissing


def test_metric_27_coverage_denominator_reflects_the_denial_population(conn):
    _insert_denial(conn, "p1", "hooks/decision_gate.py: DEC-1: x=1." + _SUFFIX,
                    ts="2026-01-01T00:00:01", reliability_class=1)
    _insert_denial(conn, "p1", "hooks/decision_gate.py: DEC-2: x=2." + _SUFFIX,
                    ts="2026-01-01T00:00:02", reliability_class=2)
    _insert_denial(conn, "p1", "hooks/decision_gate.py: DEC-3: x=3." + _SUFFIX,
                    ts="2026-01-01T00:00:03", reliability_class=2)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_27"))
    row = rows[0]
    assert row["class1_count"] == 1
    assert row["class2_count"] == 2
    assert row["total_count"] == 3
    assert row["coverage_pct"] == pytest.approx(1 / 3, abs=1e-4)
