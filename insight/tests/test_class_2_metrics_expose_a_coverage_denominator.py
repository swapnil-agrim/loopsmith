# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Static guard for the SECOND half of issue #114's done_when: "every class-2 metric exposes a
coverage denominator alongside its value". Pure stdlib, no duckdb import -- same zero-dependency
text-scan shape as test_reliability_class_static_check.py.

WHY THIS EXISTS EVEN THOUGH IT MATCHES ZERO FILES TODAY: every one of the 25 metrics in
insight/metrics/ declares `-- reliability_class: 1`, so the rule spec:125-127 states -- "Every
metric derived from Class 2 renders with a coverage denominator beside it... A Class-2 metric
with no coverage figure is a bug, not a number" -- is VACUOUSLY satisfied right now. Vacuous is
not enforced. Without this check the first class-2 metric anyone writes can ship with no coverage
figure at all and CI stays green, which is precisely the failure the done_when clause was written
to prevent. insight/metrics/reliability.py already supplies the fragment an author pastes in;
this is the gate that makes pasting it non-optional. It is deliberately header-CONDITIONAL: a
class-1 file is not required to carry these columns (per spec, only Class-2-derived numbers need
a coverage figure), so this guard costs nothing until the day it matters.

LIMITATION, named in the same spirit as its sibling guard: this is a regex heuristic over the
comment-stripped body, not a SQL parser (no such dependency exists in this repo). It checks that
each of the four coverage columns is ALIASED in the file's executable text; it cannot prove the
alias sits in the outermost SELECT list rather than an inner CTE, nor that the arithmetic behind
it is right. test_reliability_coverage_denominator.py proves the fragment's arithmetic; this
proves nobody forgot to include it."""
import re
import pathlib

METRICS_DIR = pathlib.Path(__file__).parent.parent / "metrics"

_CLASS_2_HEADER = re.compile(r"^--\s*reliability_class:\s*2\s*$", re.MULTILINE)

# The four columns insight/metrics/reliability.py's COVERAGE_DENOMINATOR_COLUMNS defines. Matched
# as `AS <name>` so a bare mention in a CTE's own arithmetic cannot satisfy the check -- only an
# actual aliased output column does.
REQUIRED_COVERAGE_COLUMNS = ("class1_count", "class2_count", "total_count", "coverage_pct")


def _sql_body_only(text):
    """Strip SQL `-- ...` line comments so a file's own prose can never satisfy the check.
    Duplicated from test_metrics_date_trunc_guard.py / test_reliability_class_static_check.py
    (this repo's no-test-imports-test convention)."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_every_class_2_metric_exposes_the_coverage_denominator_columns():
    offenders = []
    for path in sorted(METRICS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8-sig")
        if not _CLASS_2_HEADER.search(text):
            continue
        body = _sql_body_only(text)
        missing = [
            col
            for col in REQUIRED_COVERAGE_COLUMNS
            if not re.search(rf"\bAS\s+{col}\b", body, re.IGNORECASE)
        ]
        if missing:
            offenders.append((path.name, missing))
    assert offenders == [], (
        f"{offenders} -- each (file, missing columns) pair declares `-- reliability_class: 2` but "
        "does not alias every coverage-denominator column. Spec lines 125-127: a Class-2 metric "
        "with no coverage figure is a bug, not a number. Paste "
        "insight/metrics/reliability.py's COVERAGE_DENOMINATOR_COLUMNS into the SELECT list."
    )


def test_the_guard_actually_fires_on_a_class_2_file_without_the_columns(tmp_path):
    """Mutation proof: the check above matches zero files today, so on its own it can never go
    red and would be indistinguishable from a check that does nothing. This runs the SAME logic
    over a synthetic class-2 file to prove it discriminates -- one file carrying the columns
    passes, one missing them is reported."""
    good = tmp_path / "900.sql"
    good.write_text(
        "-- name: Hypothetical class-2 metric\n"
        "-- reliability_class: 2\n"
        "SELECT project_id,\n"
        "  count(*) FILTER (WHERE reliability_class = 1) AS class1_count,\n"
        "  count(*) FILTER (WHERE reliability_class = 2) AS class2_count,\n"
        "  count(*) AS total_count,\n"
        "  0.5 AS coverage_pct\n"
        "FROM fact_event GROUP BY project_id\n"
    )
    bad = tmp_path / "901.sql"
    bad.write_text(
        "-- name: Hypothetical class-2 metric with no coverage figure\n"
        "-- reliability_class: 2\n"
        "-- guardrail: prose naming class1_count AS class1_count must not save this file\n"
        "SELECT project_id, count(*) AS event_count FROM fact_event GROUP BY project_id\n"
    )

    def offenders_in(directory):
        found = []
        for path in sorted(directory.glob("*.sql")):
            text = path.read_text(encoding="utf-8-sig")
            if not _CLASS_2_HEADER.search(text):
                continue
            body = _sql_body_only(text)
            missing = [
                col
                for col in REQUIRED_COVERAGE_COLUMNS
                if not re.search(rf"\bAS\s+{col}\b", body, re.IGNORECASE)
            ]
            if missing:
                found.append((path.name, missing))
        return found

    # The bad file is caught and the good one is not -- and the bad file's own guardrail PROSE
    # mentions `AS class1_count`, so this also pins that comment-stripping is what makes the
    # difference, not luck.
    assert offenders_in(tmp_path) == [
        ("901.sql", ["class1_count", "class2_count", "total_count", "coverage_pct"])
    ]
