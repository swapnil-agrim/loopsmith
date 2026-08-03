# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Static occurrence-count guard for reliability-class enforcement (issue #114, [E2.S7]). Pure
stdlib, no duckdb import -- scans insight/metrics/*.sql as text, matching
test_metrics_date_trunc_guard.py's own zero-dependency precedent exactly.

WHAT THIS CHECKS, AND WHY BY OCCURRENCE NOT BY FILE: spec line 563 says "a NOW metric must not
read any reliability_class=2 row". Only `fact_event` carries a row-level `reliability_class`
column anywhere in the schema (insight/ingest/store.py's own DDL) -- every other fact_*/dim_*
table is structurally incapable of this violation. `test_metrics_date_trunc_guard.py`'s own
precedent is deliberately FILE-level ("this is a FILE-level check, not a per-occurrence one"),
because a tighter per-occurrence rule produced a false positive there. Reusing that same
file-level laxity here would be WRONG for this rule specifically: `35.sql` (.sdlc/plans/114.md
Decision 3) contains THREE independent `FROM fact_event`/`JOIN fact_event` reads -- the
`transitions` CTE, `current_holder`'s own inner subquery, and `all_claimed_goals` -- each its own
scan that needs its own filter. A file-level "does this file mention reliability_class = 1
anywhere" check would go green the moment ANY ONE of the three carries the filter, silently
leaving the other two unguarded. This check therefore counts occurrences of `FROM fact_event`/
`JOIN fact_event` against occurrences of `reliability_class = 1` on the COMMENT-STRIPPED body
(reusing `_sql_body_only`'s stripping -- duplicated here, not imported, per this repo's own
"no test file imports from another test file" convention, plan Global constraints) and asserts
the two counts are equal, per file. `2.sql` and `13.sql` contain literal `FROM metric_2`/
`FROM metric_13` PROSE in their own guardrail comments (a documented consumer contract, not an
executable FROM clause -- .sdlc/plans/113.md Decision 1c) -- comment-stripping is what keeps
those from being misread as a real `fact_event` reference or a real filter mention.

CLASS-2 SKIP (issue #144, [E7.S1]): this parity check is written against spec line 563's "a NOW
metric must not read any reliability_class=2 row" -- it does not apply, by construction, to a
metric that IS a Class-2 read (`22.sql`, the first one this repo ships: it reads `fact_event`
twice -- once for the plan_review-block numerator, once for the post_review/cycle "looped"
population -- with zero `reliability_class = 1` filters on either, by design, not by omission).
Any file whose header declares `-- reliability_class: 2` is skipped from this specific
reads-equal-filters count, using the same header-conditional regex
test_class_2_metrics_expose_a_coverage_denominator.py already established
(`_CLASS_2_HEADER`) -- a class-2 file is still scanned by every OTHER static guard in this
directory; only this one, class-1-specific rule does not apply to it.

LIMITATION, stated explicitly, matching test_metrics_date_trunc_guard.py's own accepted-limitation
shape: this is a regex heuristic, not a real SQL parser (no such dependency exists anywhere in
this repo). It can be fooled by, e.g., a reversed comparison (`1 = reliability_class`) or an
unrelated `reliability_class = 1` mention landing in the same file without actually being
attached to the matching `fact_event` reference -- an equal COUNT does not prove each individual
occurrence is correctly wired. This is an accepted, named limitation (.sdlc/plans/114.md Risks):
Task 2's mutation tests are the real, load-bearing runtime proof; this static check is an
early-warning lint on top, not the sole safety net.

The 7 metric ids that read fact_event today (7, 10, 12, 13, 14, 35, 41) are not hardcoded here --
every metric file is scanned uniformly, and a file with zero fact_event references correctly
produces 0 == 0 (no reference, no filter needed) without a maintained id list drifting out of
sync with the catalog."""
import re
import pathlib

METRICS_DIR = pathlib.Path(__file__).parent.parent / "metrics"

_FACT_EVENT_READ = re.compile(r"(?:FROM|JOIN)\s+fact_event\b", re.IGNORECASE)
_RELIABILITY_FILTER = re.compile(r"reliability_class\s*=\s*1\b", re.IGNORECASE)
# Same regex test_class_2_metrics_expose_a_coverage_denominator.py already established --
# duplicated, not imported, per this repo's no-test-imports-test convention.
_CLASS_2_HEADER = re.compile(r"^--\s*reliability_class:\s*2\s*$", re.MULTILINE)


def _sql_body_only(text):
    """Strip SQL `-- ...` line comments (header fields AND any trailing/inline commentary)
    so a comment's own wording can never satisfy -- or defeat -- a check meant to examine the
    executable query. Everything from the first `--` on a physical line onward is dropped;
    everything before it survives. Duplicated from test_metrics_date_trunc_guard.py's own
    `_sql_body_only` (plan Global constraints: no test-to-test import in this repo)."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_every_fact_event_read_carries_its_own_reliability_class_1_filter():
    offenders = []
    for path in sorted(METRICS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8-sig")
        if _CLASS_2_HEADER.search(text):
            # A class-2 metric reads fact_event deliberately without the class-1 filter --
            # see this file's own module docstring, "CLASS-2 SKIP".
            continue
        body = _sql_body_only(text)
        reads = len(_FACT_EVENT_READ.findall(body))
        filters = len(_RELIABILITY_FILTER.findall(body))
        if reads != filters:
            offenders.append((path.name, reads, filters))
    assert offenders == [], (
        f"{offenders} -- each (file, fact_event read count, reliability_class=1 filter count) "
        "pair must have EQUAL counts: a NOW metric must not read any reliability_class=2 row "
        "(spec line 563), and a class-1 metric's every independent fact_event scan needs its "
        "own filter, not just one filter somewhere in the file (35.sql has three separate reads "
        "-- see this file's own module docstring). Comments are excluded (only the executable "
        "query body is scanned) -- 2.sql/13.sql's own guardrail prose about `FROM metric_2`/"
        "`FROM metric_13` must not be miscounted as a real fact_event read."
    )
