# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap engine: five typed gap classes, no LLM in the finding path.

The rule format, loader, and severity vocabulary now exist (issue #116, E3.S1). Coverage now
ships four rules (issue #117, E3.S2): coverage_gate_absent, coverage_verify_no_command,
coverage_review_missing, coverage_degraded_collector. Definition, Threshold, Consistency, and
Debt (#118-#121) still do not.
"""
