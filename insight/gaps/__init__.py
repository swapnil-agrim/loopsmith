# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap engine: five typed gap classes, no LLM in the finding path.

The rule format, loader, and severity vocabulary now exist (issue #116, E3.S1). Coverage now
ships four rules (issue #117, E3.S2): coverage_gate_absent, coverage_verify_no_command,
coverage_review_missing, coverage_degraded_collector. Definition now ships two rules (issue
#118, E3.S3): definition_no_done_when, definition_no_plan_artifact -- the spec's third
Definition clause ("no `verify_command` and no `verify.command`") is met BY REFERENCE to
Coverage's own coverage_verify_no_command.sql (#117), not duplicated -- see that rule's own
guardrail and .sdlc/plans/118.md Design decision 1. Threshold now ships one rule (issue #119,
E3.S4): threshold_lead_time_breach -- fires only when a measured merge's lead time is part of a
run of 3 or more consecutive merges that each crossed their own trailing p85 (derived per
project, never a hardcoded magnitude), anywhere in that project's history, not only its most
recent merges; see that rule's own guardrail and .sdlc/plans/119.md. Consistency now ships three
rules (issue #120, E3.S5): consistency_ledger_done_pr_open, consistency_verify_no_test_touched,
consistency_files_outside_plan -- the first is a genuine cross-table join (fact_event ->
fact_goal.pr -> fact_pr_review/fact_pr_check) that renders ABSENT on this repo's own real data
today because fact_goal.pr has no writer yet (see that rule's own guardrail); the other two are
single-fact_collector_pack-record checks over alignment-collect's own d1/d2 dimensions, per
spec :529's own naming. Debt now ships one rule (issue #121, E3.S6): debt_discovery_scan_rising,
firing when a project's own discovery-scan candidate_count breaches its own trailing p85 (not
metric 30's bare LAG delta -- see .sdlc/plans/121.md Design decision 2 for why a bare delta and a
bare-LAG run-length filter were both measured and rejected) for 3 consecutive scans. This is the
fifth gap class to ship a rule, but it does NOT close epic #115 -- the epic also carries #209 (a
short-history detection hole affecting this rule and #119's Threshold rule together, not fixed
here) and #210 (knowledge/gaps.md query ingest, no writer exists yet), both still open; see
.sdlc/plans/121.md's own correction banner. THE DONE_WHEN'S SECOND CLAUSE DOES NOT SHIP:
knowledge/gaps.md unanswered queries have no ingest surface anywhere in insight/ today (no
collector, no table, no column), and fabricating one purely to render a rule ABSENT was rejected
as inventing an instrument rather than disclosing a missing one (.sdlc/plans/121.md Design
decision 1) -- tracked as issue #210, not silently dropped. Evidence also intentionally carries
no `file` column (.sdlc/plans/121.md Design decision 3): discovery-scan/v1's own wire schema has
no structured per-candidate location field, only a shell script's own
undocumented-at-the-schema-level evidence[] location strings, which this rule declines to parse
into a column that would misrepresent an implementation detail as a schema guarantee.
The RUNNER over these rules ships with
issue #122 (E3.S7): report.py's build_report/render_report execute the whole catalog against a
store -- isolating a rule that crashes rather than aborting the run -- and compare.py's
compare_reports classifies one run against a prior one, so `insight gaps [--compare]` is a real
command rather than the stub it was through #116-#121.
"""
