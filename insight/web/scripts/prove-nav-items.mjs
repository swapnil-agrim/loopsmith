// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Step 1. Browser-free, wired into `npm run test`
// (package.json chains this after prove-metric-view-behavior.mjs) -- part of the always-on gate.
//
// Proves the always-on-gate-provable half of done-when 3 ("navigation is a placeholder list ...
// do not invent an auth model here") and guards the explicit, permanent decision in
// src/app/dev/absence-states/page.tsx's own header comment: that route is never listed in
// NAV_ITEMS, because it's env-gated out of every production build and a nav entry pointing at it
// would 404 for every real user. This does NOT (and cannot, browser-free) prove the nav renders
// correctly -- that's scripts/prove-shell-responsive-frame.mjs's job, CI-only.
//
// Compiles the real src/lib/nav.ts with the local tsc into a scratch dir, then dynamic-import()s
// the emitted JS -- the same pattern prove-metric-view-behavior.mjs uses for metric-view.ts (see
// that script's own header for why: unflagged Node TS-stripping is unsafe below Node 22.18.0, and
// this repo pins no Node version).
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFileSync, copyFileSync } from "node:fs";
import path from "node:path";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

const SRC_LIB = path.join(WEB, "src", "lib");

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022",
      lib: ["ES2022"],
      module: "ESNext",
      moduleResolution: "Bundler",
      strict: true,
      noEmit: false,
      outDir: "out",
      esModuleInterop: true,
      skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts"],
  };
}

function compileNav(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  copyFileSync(path.join(SRC_LIB, "nav.ts"), path.join(dir, "nav.ts"));

  const { ok, output } = runTsc(dir);
  assert.ok(ok, `nav.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "nav.js");
}

// Exported so scripts/prove-shell-responsive-frame.mjs can assert the RENDERED nav against the
// same source of truth, instead of hardcoding a count that would silently drift from nav.ts.
export async function loadNavItems() {
  const { NAV_ITEMS } = await runScenarioAsync("insight-web-nav-items-proof-", async (dir) => {
    const emitted = compileNav(dir);
    return import(pathToFileURL(emitted).href);
  });
  return NAV_ITEMS;
}

async function main() {
  const NAV_ITEMS = await loadNavItems();

  assert.ok(Array.isArray(NAV_ITEMS) && NAV_ITEMS.length > 0, "NAV_ITEMS must be a non-empty array");

  const linked = NAV_ITEMS.filter((item) => item.href !== undefined);
  assert.equal(
    linked.length,
    1,
    `exactly one NAV_ITEMS entry may have an href today (only "/" exists as a production route), ` +
    `got ${linked.length}: ${JSON.stringify(linked)}`,
  );
  assert.equal(linked[0].href, "/", `the one linked item must point at "/", got "${linked[0].href}"`);

  for (const item of NAV_ITEMS) {
    assert.ok(
      item.href === undefined || !item.href.includes("dev"),
      `no NAV_ITEMS entry may link to a "/dev/..." route -- those are env-gated out of every ` +
      `production build; found "${item.label}" -> "${item.href}"`,
    );
  }

  // Negative-control-style guard: without at least one placeholder item, the dev-route loop above
  // would only ever check the one linked item, never a placeholder -- this makes sure the fixture
  // this proof runs against actually exercises the placeholder branch.
  const placeholders = NAV_ITEMS.filter((item) => item.href === undefined);
  assert.ok(
    placeholders.length > 0,
    "NAV_ITEMS must contain at least one placeholder (hrefless) entry -- otherwise the " +
    "dev-route guard above never actually checks a placeholder item",
  );

  console.log(
    `OK: prove-nav-items -- ${NAV_ITEMS.length} items, 1 linked ("/"), ${placeholders.length} ` +
    `placeholder(s), none pointing at a /dev/ route`,
  );
}

// Only run the proof when invoked directly -- prove-shell-responsive-frame.mjs imports
// loadNavItems() from here and must not trigger a second full run as a side effect.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((err) => {
    console.error("FAIL: prove-nav-items");
    console.error(err);
    process.exit(1);
  });
}
