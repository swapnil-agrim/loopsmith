# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Severity rank pinning (issue #111, task 2 follow-up, post-review fix). Issue #111's own task
list says: "Adopt pipeline.py's PASS/WARN/FAIL/ABSENT AND ITS SEVERITY ORDER." Shipping only the
bare status string left the order undefined for a consumer -- a naive `ORDER BY status` gets
DuckDB's default alphabetical order (ABSENT, FAIL, PASS, WARN), which sorts a genuine pass ahead
of nothing except itself and puts FAIL ahead of PASS only by accident of spelling, not severity.
metric_24/26/30 now each carry a `severity_rank` column so a consumer reads the ordering off the
view instead of re-deriving pipeline.py's {PASS:0, ABSENT:1, WARN:2, FAIL:3} a second time --
exactly the "a value re-derived instead of read from its source" failure shape #109 already spent
four review rounds on.

WHY THIS TEST DOES NOT `import` pipeline.py: insight/ must never `import skills` or `import
hooks` as a Python package (test_import_boundary.py, spec section 1.1 rule 1) -- reading a path
is the allowed coupling, importing the package is not, and `sys.path` tricks to import a skills/
submodule under a different top-level name would violate the SAME rule in substance even though
the AST checker's exact-first-segment match would not catch it (see that file's own module
docstring, "A further residue specific to this checker"). So `_pipeline_order()` below reads
skills/sdlc-loop/scripts/pipeline.py as TEXT and walks its AST as data (no Import/ImportFrom node
is ever created), resolving the PASS/WARN/FAIL/ABSENT name-to-string bindings and then the
_ORDER dict's keys against them -- rather than hardcoding a second copy of
{'PASS':0,'ABSENT':1,'WARN':2,'FAIL':3} in this file that could silently drift from pipeline.py's
real source the next time someone edits _ORDER there. If pipeline.py's assignment shape changes
enough that this parser can no longer find PASS/WARN/FAIL/ABSENT or _ORDER, this test fails loudly
(AssertionError on `order is not None`) rather than silently comparing against a stale value.

SEVERITY_RANK COLUMN NAME AND SHAPE: `severity_rank`, an INTEGER, added as the last column of
each of metric_24/26/30, derived from that view's own already-computed `status` column via
`CASE status WHEN 'PASS' THEN 0 WHEN 'ABSENT' THEN 1 WHEN 'WARN' THEN 2 WHEN 'FAIL' THEN 3 ELSE
NULL END` (DuckDB resolves this same-SELECT alias reference -- "lateral column alias" --
confirmed live: `SELECT 1 AS x, x + 1 AS y` returns `(1, 2)`, both duckdb 1.4.5 and 1.5.5). Chosen
over re-deriving the CASE branches from the raw denominator/threshold columns a second time,
because deriving severity_rank FROM status (rather than recomputing it independently) means the
two columns cannot disagree with each other by construction within a single view -- only status's
own CASE can be wrong, not a second, parallel CASE that could drift from it.

OUT-OF-VOCABULARY CASE, DECIDED: `ELSE NULL`, stated explicitly rather than left as an implicit
fall-through. Each view's own `status` CASE already has an unconditional ELSE branch that always
resolves to one of the four canonical literals (verified by reading all three status CASEs: 24.sql
falls through to 'FAIL', 26.sql and 30.sql fall through to 'ABSENT' -- none has a bare `WHEN` with
no covering ELSE), so `status` itself can never hold a fifth value at the point severity_rank reads
it, and the `ELSE NULL` branch is therefore UNREACHABLE by any of the three shipped views today --
not claimed as impossible in general (a future edit to a status CASE that drops its own ELSE could
reach it), just never reachable by the SQL that exists in this repo right now. Not independently
pinned by a fixture (there is no way to fixture a status value the view's own CASE cannot produce);
stated as defensive-only, same posture as the NULL/COALESCE guards elsewhere in these three files."""
import ast
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
PIPELINE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "skills" / "sdlc-loop" / "scripts" / "pipeline.py"
)


def _pipeline_order():
    """Read pipeline.py's PASS/WARN/FAIL/ABSENT + _ORDER off disk as TEXT/AST -- see this file's
    own module docstring for why this is never a Python `import`."""
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_PATH))
    names = {}
    order = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Tuple)
            and isinstance(node.value, ast.Tuple)
            and len(target.elts) == len(node.value.elts)
        ):
            for name_node, value_node in zip(target.elts, node.value.elts):
                if isinstance(name_node, ast.Name) and isinstance(value_node, ast.Constant):
                    names[name_node.id] = value_node.value
        elif isinstance(target, ast.Name) and target.id == "_ORDER" and isinstance(node.value, ast.Dict):
            order = {}
            for key_node, value_node in zip(node.value.keys, node.value.values):
                assert isinstance(key_node, ast.Name) and isinstance(value_node, ast.Constant), (
                    "pipeline.py's _ORDER dict no longer uses bare Name keys / int Constant "
                    "values -- this parser's assumptions about its shape are stale"
                )
                order[names[key_node.id]] = value_node.value
    assert order is not None, (
        f"pipeline.py's _ORDER assignment was not found by AST-walking {PIPELINE_PATH} -- "
        "either the file moved or the assignment shape changed; this parser needs updating, "
        "not the hardcoded fallback this test deliberately does not have"
    )
    return order


ORDER = _pipeline_order()


def test_pipeline_order_parsed_off_disk_matches_the_known_vocabulary():
    """Sanity-checks the parser itself against the exact values re-read directly from
    pipeline.py this session (plan's own Verification method section) -- if this fails, the
    parser above is broken, not the three metric views."""
    assert ORDER == {"PASS": 0, "ABSENT": 1, "WARN": 2, "FAIL": 3}


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


@pytest.mark.parametrize("metric_id", ["24", "26", "30", "37", "16"])
def test_severity_rank_matches_pipelines_own_order(conn, metric_id):
    load_fixture_jsonl(conn, FIXTURES_DIR / f"{metric_id}.jsonl")
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute(f"SELECT status, severity_rank FROM metric_{metric_id}"))
    assert rows, f"metric_{metric_id}'s fixture produced zero rows -- this test would be vacuous"
    for row in rows:
        assert row["status"] in ORDER, (
            f"metric_{metric_id} emitted status {row['status']!r}, outside pipeline.py's own "
            f"vocabulary {sorted(ORDER)}"
        )
        assert row["severity_rank"] == ORDER[row["status"]], (
            f"metric_{metric_id} row {row} has severity_rank={row['severity_rank']!r} but "
            f"pipeline.py's own _ORDER says {row['status']} should rank {ORDER[row['status']]}"
        )


def test_all_four_statuses_are_exercised_across_the_three_fixtures(conn):
    """Guards against a vacuous pin: confirms PASS/WARN/FAIL/ABSENT each appear at least once
    across 24/26/30's own fixtures, so test_severity_rank_matches_pipelines_own_order actually
    exercises all four ranks somewhere rather than only ever seeing a subset. (26.sql's own
    design never emits WARN -- see 26.sql's guardrail -- so WARN is covered by 24/30 instead.)"""
    seen = set()
    for metric_id in ("24", "26", "30"):
        load_fixture_jsonl(conn, FIXTURES_DIR / f"{metric_id}.jsonl")
    load_metrics(conn)
    for metric_id in ("24", "26", "30"):
        rows = rows_as_dicts(conn.execute(f"SELECT status FROM metric_{metric_id}"))
        seen |= {r["status"] for r in rows}
    assert seen == {"PASS", "WARN", "FAIL", "ABSENT"}
