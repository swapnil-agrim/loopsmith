// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #314 [E20.S3], .sdlc/plans/314.md Decision C / Step 12 (done-when 5): the leadership
// page's own zero-individual-grain guarantee (spec
// docs/superpowers/specs/2026-08-04-loopsmith-insight-web-app-design.md:379), proven against a
// REAL booted `next start`, whole raw response body, not the Python-side render function alone.
// Same family as prove-ic-no-cross-actor-leak.mjs (CI-only, needs a real DuckDB store and a real
// server -- see insight/verify_web.py's own docstring for why a booted-server proof has no place
// in the always-on offline local gate). Wired as `npm run prove:leadership-no-leak`, in
// .github/workflows/ci.yml's `web` job.
//
// SHAPE, decided piece by piece (see .sdlc/plans/314.md Decision C for the full reasoning):
// - Seeds via seed-cold-start-store.py's --populate-actor-rows (carol's fact_event row, the
//   carol->dave fact_handoff row, one dim_project row -- the SAME shapes
//   test_dash_leadership_guardrail.py's own `conn` fixture uses) PLUS --populate-metric-5, so the
//   page also carries real, non-vacuous panel content: a leak check against an empty/absent-only
//   page proves nothing (mirrors prove-ic-no-cross-actor-leak.mjs's own step 1 reasoning).
// - Needles: ["carol", "dave"] -- matches
//   test_dash_leadership_guardrail.py::_assert_no_individual_grain_leak's own needle list exactly
//   (zero sanctioned exceptions, stricter than the manager guardrail's one carve-out).
// - Whole-body scan, ONE fetch: unlike prove-ic-no-cross-actor-leak.mjs (fundamentally about
//   CROSS-ACTOR isolation -- two sessions, compare what each sees), leadership has no actor
//   dimension at all -- there is one fetch, one body, one check. Mirrors
//   test_dash_leadership_guardrail.py's own single-fetch shape more closely than the IC proof's
//   two-session shape.
// - stripBuildArtifacts() (scripts/lib/strip-build-artifacts.mjs, Step 10's extraction) runs
//   first, required per the PR #517 incident that file's own header documents -- the discipline is
//   "every new substring leak scan strips build artifacts first," not "only the ones that need it
//   this time," even though "carol"/"dave" are alphabetic, not the short bare-digit kind that
//   collided before.
// - EXECUTED NEGATIVE CONTROL (mandatory, not prose -- this repo settles falsifiability by RUNNING
//   a negative control, see prove-ic-no-cross-actor-leak.mjs's own header): mirrors the SHAPE of
//   the Python guardrail's own negative control
//   (test_negative_control_proves_the_leadership_privacy_check_has_teeth), not a second live fetch
//   (there is no "carol's own /leadership page" -- leadership isn't actor-scoped, so that IC-proof
//   pattern doesn't apply here). Takes the real fetched, stripped body, string-injects a needle
//   into a real panel -- id 5's own rendered label, the one measured card the populated fixture
//   guarantees (NOT the Python render function's own "<h2>Impact...</h2>" heading text, which
//   this web page never renders -- it composes from Metric/IntegrityStrip, not
//   insight/dash/leadership.py's HTML template) -- and asserts the SAME
//   assertNoIndividualGrainLeak function now throws.
import assert from "node:assert/strict";
import { mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { seedFixture, startNext, WEB } from "./lib/cold-start-proof.mjs";
import { mintSessionToken, SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";
import { makeStripBuildArtifacts } from "./lib/strip-build-artifacts.mjs";

const REPO_ROOT = path.resolve(WEB, "..", "..");

// ---- needle list (mirrors test_dash_leadership_guardrail.py's own exactly) ------------------

const NEEDLES = ["carol", "dave"];

/** THE check both the positive assertion and the executed negative control share -- factored out
 * so the two can never drift into checking different things (mirrors
 * test_dash_leadership_guardrail.py's own `_assert_no_individual_grain_leak`, and
 * prove-ic-no-cross-actor-leak.mjs's own assertCarolNeedlesAbsent() pattern). */
function assertNoIndividualGrainLeak(strippedBody) {
  for (const needle of NEEDLES) {
    assert.ok(!strippedBody.includes(needle), `individual-grain leak: ${JSON.stringify(needle)} found on the leadership page`);
  }
}

/** GETs /leadership carrying a real Auth.js session for the "leadership" role -- page.tsx itself
 * calls no auth() (leadership data is aggregate-only, no actor), so only proxy.ts's own cookie
 * lookup matters here; the plain (non `__Secure-`-prefixed) cookie name is enough, same as
 * prove-role-forbidden-real-server.mjs's own fetchAs(). */
async function fetchLeadershipAsLeadership(baseUrl) {
  const token = await mintSessionToken("leadership", "proof-user");
  return fetch(`${baseUrl}/leadership`, {
    headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` },
    redirect: "manual",
  });
}

async function main() {
  const t0 = Date.now();
  const scratchDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-leadership-no-leak");
  rmSync(scratchDir, { recursive: true, force: true });
  mkdirSync(scratchDir, { recursive: true });
  const dbPath = seedFixture(scratchDir, "s.duckdb", ["--populate-actor-rows", "--populate-metric-5"]);

  const { proc, baseUrl } = await startNext(dbPath);
  try {
    const stripBuildArtifacts = makeStripBuildArtifacts(WEB);

    // 1. POSITIVE CONTROL -- guards against a vacuous pass from an empty/broken page. If this
    //    fails, the leak assertion below it is meaningless (there would be nothing to leak from).
    const res = await fetchLeadershipAsLeadership(baseUrl);
    assert.equal(res.status, 200, `a real "leadership" session on /leadership must succeed, got ${res.status}`);
    const rawBody = await res.text();
    assert.ok(rawBody.includes("15.0%"), "leadership's populated /leadership page must render metric_5's real numeral (a positive control -- there must be real content to leak into)");
    console.log('OK: positive control -- /leadership (real "leadership" session, populated fixture) renders real panel content');

    // 2. LEAK ASSERTION (done-when 5, "the whole response body") -- Next inlines the RSC flight
    //    payload via __next_f script tags, so a plain res.text() on the full HTML document already
    //    captures it; no separate ?_rsc= fetch needed.
    const strippedBody = stripBuildArtifacts(rawBody);
    assertNoIndividualGrainLeak(strippedBody);
    console.log('OK: /leadership\'s raw response body carries none of carol\'s or dave\'s identifiers');

    // 3. EXECUTED NEGATIVE CONTROL (mandatory, not prose). Mirrors
    //    test_negative_control_proves_the_leadership_privacy_check_has_teeth exactly: string-inject
    //    a needle into a REAL panel of the real, stripped body, and assert the SAME
    //    assertNoIndividualGrainLeak function now throws. Without this, a broken/tautological check
    //    (e.g. a typo that made every `includes()` call vacuously false) would have passed step 2
    //    without ever having been capable of catching a real leak.
    // A real panel's own label, in the actual rendered DOM -- id 5 (Change failure rate) is the
    // one measured card the populated fixture guarantees, so its label is a stable, real anchor
    // (unlike test_dash_leadership_guardrail.py's own render_leadership_view() heading text,
    // which this web page does not render at all -- it composes from Metric/IntegrityStrip, not
    // insight/dash/leadership.py's HTML template).
    const panelLabel = '<span class="panel-label truncate">Change failure rate</span>';
    assert.ok(strippedBody.includes(panelLabel), "fixture regressed: the metric-5 panel label this negative control injects into is no longer present in the real body");
    const mutatedBody = strippedBody.replace(panelLabel, `${panelLabel}<span>reported by: carol</span>`);
    assert.throws(
      () => assertNoIndividualGrainLeak(mutatedBody),
      "NEGATIVE CONTROL FAILED: assertNoIndividualGrainLeak found nothing wrong with a body that " +
        "has \"carol\" string-injected into a real panel -- this check has no teeth and would not " +
        "catch a real leak either",
    );
    console.log("OK: negative control -- the same individual-grain leak check correctly FAILS once \"carol\" is injected into a real panel");
  } finally {
    proc.kill();
    rmSync(scratchDir, { recursive: true, force: true });
  }

  console.log(`\nOK: prove-leadership-no-individual-grain-leak (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-leadership-no-individual-grain-leak");
  console.error(err);
  process.exit(1);
});
