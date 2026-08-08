// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B4 (done-when 4): "Cold-start test: empty fact tables, assert
// no readout renders a numeral." The exact regression class this whole story exists to prevent --
// insight/dash/panel.py once shipped a literal `0` for "goals landed" against an empty store
// (test_dash_panel_absence.py's own test_cold_start_never_renders_a_bare_zero is the Python-side
// version of this same guarantee; test_api_metrics_route.py::
// test_cold_start_every_metric_is_absent_with_no_numeral_in_the_body is the API-layer version).
// This is the WEB-layer version: a real running Next server, a real booted `insight web delivery`
// bridge, a real schema-only zero-row DuckDB store, and a raw HTML response body scanned for
// digits inside every `metric-numeral` slot -- not merely "the field looks empty."
//
// CI-ONLY, same family as prove:ic-no-leak -- needs a real DuckDB store, which `npm run test`'s
// offline chain (run in a fresh worktree, before any pip install -- the exact #310 scar this
// repo's other CI-only proofs already document) cannot provide. Wired as
// `npm run prove:delivery-cold-start`, in .github/workflows/ci.yml's `web` job, reusing the SAME
// actions/setup-python + pip install -e insight/ steps prove:role-forbidden/prove:ic-bridge/
// prove:ic-no-leak already share -- no new install step needed.
//
// Mirrors prove-ic-no-cross-actor-leak.mjs's own getFreePort/startNext/plain-fetch shape (no
// browser dependency needed -- this only needs response TEXT, not computed styles) and, above
// all, its MANDATORY executed negative control discipline: this repo settles falsifiability by
// RUNNING a negative control, never by asserting it in prose (see that file's own header comment
// -- now scripts/lib/cold-start-proof.mjs's own header, since the mechanism moved there).
//
// issue #314 [E20.S3], .sdlc/plans/314.md Decision B / Step 2: this file's own server-lifecycle/
// digit-scan/two-section machinery was extracted into scripts/lib/cold-start-proof.mjs (issue
// #574's own ask -- "extract the shared cold-start harness before a third view lands", since this
// file and prove-manager-cold-start-no-numerals.mjs were near-identical copies of each other).
// This is now a thin call site of that harness, preserving this proof's own floor (46: 4 primary
// readouts + 42 board cells) and populate flag (--populate-metric-12) exactly as before.
import { runColdStartNoNumeralsProof } from "./lib/cold-start-proof.mjs";

runColdStartNoNumeralsProof({
  proofName: "prove-delivery-cold-start-no-numerals",
  route: "/delivery",
  role: "manager",
  populateFlags: ["--populate-metric-12"],
  floor: 46,
}).catch((err) => {
  console.error("FAIL: prove-delivery-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
