# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The 42-metric catalog (issue #300 [E16.S2], Decision (a)): hoisted verbatim out of
`insight.dash.panel`, which is where it originally shipped (issue #264) and still re-exports it
unchanged so every existing `from insight.dash.panel import CATALOG` keeps working.

Moved here rather than left in panel.py because `insight.api` (E16.S2's own `/metrics` route)
now needs the same id -> label mapping, and `insight.dash` is not a dependency `insight.api`
should take on for one dict -- `insight/metrics/` is already the shared home for the per-metric
`.sql` files and their header grammar (`header.py`), and `insight/metrics/__init__.py` already
re-exports sibling submodules the same way.
"""

# The 42-metric catalog, id -> short name. Sourced from the data-platform spec's section 6 table.
# Held here as data rather than re-read from the spec markdown at build time: the spec is prose and
# its table formatting is not a stable interface, whereas this mapping is small and rarely changes.
CATALOG = {
    1: "Throughput", 2: "Cycle time", 3: "Lead time for change", 4: "Merge frequency",
    5: "Change failure rate", 6: "MTTR proxy", 7: "Flow load (WIP)", 8: "Flow efficiency",
    9: "Flow distribution", 10: "Aging WIP", 11: "Throughput forecast", 12: "Autonomy rate",
    13: "Interventions per goal", 14: "Park rate", 15: "Park taxonomy",
    16: "Review-cycle distribution", 17: "Cost per landed goal", 18: "Tokens per phase",
    19: "Budget-exhaustion rate", 20: "Rework ratio", 21: "Model-tier effectiveness",
    22: "Prevented rework", 23: "Gate catch rate", 24: "Gate coverage", 25: "Escape rate",
    26: "Verify reliability", 27: "Decision-gate denials", 28: "Alignment drift",
    29: "Retro grade mix", 30: "Debt inventory", 31: "Handoff graph",
    32: "Handoff response time", 33: "Unanswered handoffs", 34: "Deferred-handoff age",
    35: "Lease contention", 36: "Parallelism yield", 37: "Ownership concentration",
    38: "Cross-area coupling", 39: "DX Core-4 rollup", 40: "Unit economics",
    41: "Portfolio table", 42: "Adoption & flag correlation",
}
