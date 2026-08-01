# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap engine: five typed gap classes, no LLM in the finding path.

The rule format, loader, and severity vocabulary now exist (issue #116, E3.S1). Coverage now
ships four rules (issue #117, E3.S2): coverage_gate_absent, coverage_verify_no_command,
coverage_review_missing, coverage_degraded_collector. Definition now ships two rules (issue
#118, E3.S3): definition_no_done_when, definition_no_plan_artifact -- the spec's third
Definition clause ("no `verify_command` and no `verify.command`") is met BY REFERENCE to
Coverage's own coverage_verify_no_command.sql (#117), not duplicated -- see that rule's own
guardrail and .sdlc/plans/118.md Design decision 1. Threshold, Consistency, and Debt (#119-#121)
still do not.
"""
