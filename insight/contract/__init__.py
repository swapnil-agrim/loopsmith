# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The versioned engine<->product data contract (issue #298, [E15.S4], spec §2.1.2/§2.1.3).

Golden fixtures for the formats insight/ingest reads off disk: ledger entries, the telemetry
event stream, goal frontmatter, and config.json. `state/*` is deliberately NOT fixtured here --
insight/dash/manager.py's own module docstring states there is zero ingest path for it anywhere
under insight/; fixturing a format nothing reads would be a decorative proof (see README.md).

CONTRACT_VERSION bumps only on a BREAKING change to one of the four fixtured formats -- a field
renamed/removed, a vocabulary member removed or repurposed. Adding an optional field or a new
vocabulary member is additive (ledger.py's own "an older reader ignores what it doesn't know"
contract, spec-required) and does NOT bump this. See README.md for the full rule and for the
"kept in sync by hand, not enforced" statement this module does not pretend otherwise about."""
CONTRACT_VERSION = 1
