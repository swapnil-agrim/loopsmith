# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The trailing-p85 baseline SQL fragment, and the consecutive-breach run-length constant
(issue #119, [E3.S4], Task 1/7; see .sdlc/plans/119.md Design decisions 7 and D-new). Pure
stdlib (no `duckdb` import): this module never touches a database, only exports a string and an
int.

TRAILING_P85_EXPR is a SQL window-expression fragment a Threshold rule's own .sql file pastes
verbatim into its trailing-baseline column -- NOT a callable helper, NOT a registered database
object, and NOT a template. insight.gaps.loader's own mechanism (conn.execute(rule["query"]), no
conn at load time -- #116 Design decision 7) treats every .sql file as opaque text; there is no
mechanism that could share a Python string INTO a .sql file at runtime. A human author copies the
constant's value in at authoring time -- matching insight.metrics.reliability.
COVERAGE_DENOMINATOR_COLUMNS's own precedent exactly.

K_CONSECUTIVE_BREACHES is the run length a series of consecutive trailing-p85 breaches must
reach before the rule fires -- see .sdlc/plans/119.md Design decision D-new for the full
argument for why this integer is a RUN LENGTH, not the "hardcoded MAGNITUDE constant" issue
#119's own done_when bans: it encodes no fact about lead_time_seconds, or any metric's own unit
or scale -- it is a statement about how much repeated evidence turns noise into a pattern
(0.15**3 ~= 0.34% per point under a stationary series, the spec correction's own cited number),
identical in kind to a statistical-significance threshold, not a domain magnitude. Like
TRAILING_P85_EXPR, this constant cannot be templated into the rule's own .sql body (DuckDB
macros cannot parametrize identifiers -- tested live, #119's own round-1 research); a human
author pastes the literal `3` by hand into the rule's `breach_run_length >= 3` comparison, and
insight/tests/test_gaps_baseline_fragment_is_referenced.py checks that pasted literal against
this constant's own str() value, so the two cannot silently drift apart.

WHY 0.85 IS NOT THE BANNED "hardcoded constant": 0.85 is the quantile PARAMETER that DEFINES
p85, not a tuned MAGNITUDE (.sdlc/plans/119.md Design decision 9).

TRAILING MEANS STRICTLY PRIOR: `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` excludes the
row being judged from its own baseline (.sdlc/plans/119.md Design decision 3).

THESE VALUES' TEETH: insight/tests/test_gaps_baseline_fragment_is_referenced.py asserts every
insight/gaps/threshold_*.sql file's own comment-stripped body contains TRAILING_P85_EXPR verbatim
AND a `breach_run_length >= {K_CONSECUTIVE_BREACHES}` comparison; insight/tests/
test_gaps_no_literal_thresholds.py's own grep test imports K_CONSECUTIVE_BREACHES too, so its one
narrow run-length exemption is derived from this same constant, never a second, independently
typed `3`."""

TRAILING_P85_EXPR = (
    "quantile_cont(lead_time_seconds, 0.85) OVER (PARTITION BY project_id ORDER BY merge_ts, "
    "merge_sha ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"
)

#: A RUN LENGTH, not a magnitude -- see this module's own docstring and .sdlc/plans/119.md
#: Design decision D-new for the full argument. Corrected by PR #202 from a single crossing.
K_CONSECUTIVE_BREACHES = 3
