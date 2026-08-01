# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The severity vocabulary a gap rule shares with the metric layer (issue #116, E3.S1, Task 2;
see .sdlc/plans/116.md Design decision 5). Pure stdlib -- no `import duckdb`, and no import of
anything from `insight.metrics` or `skills/` -- matching insight.metrics.reliability's own
"documentation-as-code, not infrastructure a metric depends on at query time" posture verbatim:
this module never touches a database or a rule's own query, it only exports one dict constant.

SEVERITY_ORDER is hand-derived directly from skills/sdlc-loop/scripts/pipeline.py:34-35's own
`PASS, WARN, FAIL, ABSENT = "PASS", "WARN", "FAIL", "ABSENT"` / `_ORDER = {PASS: 0, ABSENT: 1,
WARN: 2, FAIL: 3}`, re-read this session -- substituting the string constants into the dict
literal by hand gives exactly the four pairs below, most notably ABSENT: 1 < WARN: 2 (unknown is
worse than a confirmed pass, but better than a confirmed problem).

WHY NO IMPORT OF pipeline.py: insight/ must never `import skills` as a Python package
(tests/test_import_boundary.py, spec section 1.1 rule 1) -- reading a path is the allowed
coupling, importing the package is not. This module instead carries a hardcoded copy of the
vocabulary, backed by insight/tests/test_gaps_severity_vocabulary.py's drift test, which reads
pipeline.py as TEXT and AST-parses it (never a Python import) so a future edit to pipeline.py's
own _ORDER breaks that test loudly instead of leaving this constant silently stale -- exactly
the same technique insight/tests/test_metric_severity_rank.py already uses for the same
vocabulary, one layer up (metric_24/26/30/37's own severity_rank columns)."""

SEVERITY_ORDER = {"PASS": 0, "ABSENT": 1, "WARN": 2, "FAIL": 3}
