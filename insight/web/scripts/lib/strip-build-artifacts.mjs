// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #314 [E20.S3], .sdlc/plans/314.md Step 10 -- rule-of-two extraction: this is the SECOND
// consumer (prove-leadership-no-individual-grain-leak.mjs, Step 12) of the same stripping logic
// prove-ic-no-cross-actor-leak.mjs already carried locally (BUILD_ID/NEXT_STATIC_ASSET_RE/
// stripBuildArtifacts, lines ~168-173 of that file before this extraction), moved here verbatim
// and parameterized on `webRoot` so it stays reusable without a hardcoded path.
//
// WHY THIS EXISTS AT ALL (full history preserved from prove-ic-no-cross-actor-leak.mjs's own
// header comment, since a future third consumer will want it without re-deriving the reasoning):
// Next.js's OWN per-build random buildId -- freshly generated on every `next build`, unrelated to
// source content (verified empirically: two consecutive `next build`s of the byte-identical
// source produced two different `.next/BUILD_ID` values, e.g. "za1F0ONaTZKv3Db0BWHXS" vs
// "ihF9HFtKw3mBPIdwTgJ0l") -- is inlined into EVERY authenticated page's raw HTML via the RSC
// flight payload's own router-state field (literally `\"b\":\"<buildId>\"` in the raw response
// text). Turbopack's content-hashed chunk/asset filenames under `_next/static/chunks/` are the
// same kind of noise: effectively-random alphanumeric strings, identical for every actor's page
// in a given server boot, carrying no application data at all. A short bare-digit needle like
// carol's own "303" (her fact_handoff issue number) can land as a SUBSTRING of either one by pure
// chance. That is exactly what happened in CI on PR #517 (an unrelated CSS-constant refactor that
// only changed which module a string lived in, which was enough to reshuffle Turbopack's chunk
// hashes / this build's buildId): assertCarolNeedlesAbsent tripped on "303" appearing inside a
// build artifact, not inside carol's data -- a false positive, reproduced by inspecting the raw
// dumped HTML. Stripping these two KNOWN, proven noise sources before a needle check runs removes
// exactly that false-positive surface -- and nothing else: this does NOT restrict the haystack to
// any extracted payload or to visible rendered text, so a needle check's power to catch a REAL
// leak is unchanged. The discipline this extraction exists to keep: "every new substring leak scan
// strips build artifacts first," not "only the ones that need it this time" (issue #314's own
// Decision C -- carol/dave are alphabetic, not the short bare-digit kind that collided in #517,
// but the rule applies regardless of needle shape).
import { readFileSync } from "node:fs";
import path from "node:path";

const NEXT_STATIC_ASSET_RE = /_next\/static\/[^"'\\)\s]+/g;

/** Returns a fresh `stripBuildArtifacts(body)` closure bound to the real `.next/BUILD_ID` under
 * `webRoot` -- read ONCE per closure (mirrors the original module-level read: BUILD_ID does not
 * change within one `next build`, so re-reading per call would be pure overhead, not a
 * correctness fix). */
export function makeStripBuildArtifacts(webRoot) {
  const buildId = readFileSync(path.join(webRoot, ".next", "BUILD_ID"), "utf-8").trim();
  return function stripBuildArtifacts(body) {
    return body.split(buildId).join("<build-id>").replace(NEXT_STATIC_ASSET_RE, "_next/static/<asset>");
  };
}
