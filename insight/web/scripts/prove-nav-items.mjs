// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Step 1 (original file, static NAV_ITEMS).
// issue #311 [E19.S3], .sdlc/plans/311.md Task 4 (full rewrite: nav is now role-aware, generated
// from route-policy.ts's own table -- this file's job changes from "assert one static list's
// shape" to "prove nav and enforcement agree, and share one source"). Browser-free, wired into
// `npm run test` (package.json chains this after prove-metric-view-behavior.mjs) -- part of the
// always-on gate.
//
// Proves done-when 1 (every role x every route asserted, generated from the table), done-when 2
// (nav renders only reachable views -- no dead links, no hints about unbuilt views), and done-when
// 3 (nav is derived from the SAME matrix as enforcement, not a separately-declared, merely-equal
// copy) -- all offline, all generated from the real, live `route-policy.ts` exports, zero
// hand-typed role/route literals. This does NOT (and cannot, browser-free) prove the nav RENDERS
// correctly -- that's scripts/prove-shell-responsive-frame.mjs's job, CI-only.
//
// Compiles the real src/lib/nav.ts AND src/lib/auth/route-policy.ts with the local tsc into one
// scratch dir (mirrors prove-metric-view-behavior.mjs's established `include: ["*.ts", "api/*.ts"]`
// subdirectory pattern), then dynamic-import()s the emitted nav.js -- the same pattern
// prove-role-route-matrix.mjs Part A uses for route-policy.ts alone (see that script's own header
// for why: unflagged Node TS-stripping is unsafe below Node 22.18.0, and this repo pins no Node
// version).
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { mkdirSync, writeFileSync, readFileSync, copyFileSync } from "node:fs";
import path from "node:path";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

const SRC_LIB = path.join(WEB, "src", "lib");
const SRC_AUTH_LIB = path.join(SRC_LIB, "auth");

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
    include: ["*.ts", "auth/*.ts"],
  };
}

function compileNav(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  mkdirSync(path.join(dir, "auth"));
  copyFileSync(path.join(SRC_AUTH_LIB, "route-policy.ts"), path.join(dir, "auth", "route-policy.ts"));
  copyFileSync(path.join(SRC_LIB, "nav.ts"), path.join(dir, "nav.ts"));

  const { ok, output } = runTsc(dir);
  assert.ok(ok, `nav.ts and auth/route-policy.ts must compile clean together with the local tsc:\n${output}`);

  // Same reasoning as prove-role-route-matrix.mjs Part B's compileProxy(): tsc's module resolution
  // under moduleResolution:"Bundler" emits the bare relative specifier VERBATIM ("./auth/route-
  // policy", no extension) -- Node's ESM loader needs the extension to resolve it at runtime. Fix
  // up the EMITTED output only, never the .ts source, so the code under test is compiled verbatim.
  const emitted = path.join(dir, "out", "nav.js");
  let js = readFileSync(emitted, "utf-8");
  const before = js;
  js = js.replace('from "./auth/route-policy"', 'from "./auth/route-policy.js"');
  const rewrites = js === before ? 0 : 1;
  assert.equal(rewrites, 1, "expected to rewrite exactly 1 import specifier in the emitted nav.js, did " + rewrites);
  writeFileSync(emitted, js);
  return emitted;
}

// Exported so scripts/prove-shell-responsive-frame.mjs can assert the RENDERED nav against the
// SAME compiled module this file's own proofs run against, instead of hardcoding a count that
// would silently drift. Renamed from the old loadNavItems() (issue #311): the old function handed
// callers a static array; this one hands them the role-aware function plus the same live table
// decide() reads, so a caller can compute the expected nav for any role/session under test.
//
// nav.ts itself only exports navItemsFor (it does not re-export route-policy.ts's own bindings),
// so this dynamic-import()s BOTH emitted files -- nav.js AND the auth/route-policy.js it resolves
// to -- and merges their exports. Node's ESM loader caches a module by its resolved URL, and
// nav.js's own (rewritten) relative import of "./auth/route-policy.js" resolves to the exact same
// absolute path this function imports directly below, so this is still ONE module graph, ONE
// ROLE_ROUTES object instance, imported once -- not two separate copies.
export async function loadNav() {
  return runScenarioAsync("insight-web-nav-items-proof-", async (dir) => {
    const emitted = compileNav(dir);
    const navMod = await import(pathToFileURL(emitted).href);
    const routePolicyMod = await import(
      pathToFileURL(path.join(dir, "out", "auth", "route-policy.js")).href
    );
    return { ...routePolicyMod, ...navMod };
  });
}

async function main() {
  // AMENDMENT 1 (independent plan-review of .sdlc/plans/311.md): loadNav() must hand back every
  // binding proof body (a) below calls directly, or this throws ReferenceError, not a coincidental
  // pass -- representativePath is destructured here for exactly that reason.
  const { navItemsFor, decide, ROLE_ROUTES, SHARED_AUTHENTICATED_ROUTES, representativePath, isKnownRole } =
    await loadNav();

  // ---- (a) generated cross-product -- done-when 1 and 2 together --------------------------------
  // For every role (generated from the live table, plus undefined and an unknown-role sentinel,
  // mirroring prove-role-route-matrix.mjs's own edge cases) x every hasSession x every real table
  // entry: nav must show an item IFF that entry has a navLabel AND is implemented AND decide()
  // allows it. This is the single assertion that resolves whether an `implemented: false` route
  // (e.g. /manager for role:"manager", where decide() allows it) is correctly ABSENT from nav --
  // if navItemsFor's `implemented` filter were ever dropped, this is what turns red.
  const rolesUnderTest = [...Object.keys(ROLE_ROUTES), undefined, "owner"];
  // issue #312 [E20.S1] Goal A: ROLE_ROUTES[role] is now an array of entries per role -- flatten.
  const allEntries = [...SHARED_AUTHENTICATED_ROUTES, ...Object.values(ROLE_ROUTES).flat()];
  let assertions = 0;
  for (const role of rolesUnderTest) {
    for (const hasSession of [true, false]) {
      const nav = navItemsFor(hasSession, role);
      for (const entry of allEntries) {
        const routePath = representativePath(entry);
        const allowed = decide(routePath, hasSession, role) === "allow";
        const inNav = nav.some((item) => item.href === routePath);
        if (entry.navLabel && entry.implemented) {
          assert.equal(
            inNav, allowed,
            `${routePath} for role=${role} hasSession=${hasSession}: nav (${inNav}) must match ` +
            `enforcement (${allowed})`,
          );
        } else {
          assert.equal(
            inNav, false,
            `${routePath} has no navLabel or is not implemented -- must NEVER appear in nav for ` +
            `role=${role} hasSession=${hasSession}, even though decide() says ${allowed ? "allow" : "forbid"}`,
          );
        }
        assertions += 1;
      }
    }
  }
  console.log(
    `OK: generated nav/enforcement cross-product -- ${assertions} assertions over ` +
    `${rolesUnderTest.length} role(s) x 2 session states x ${allEntries.length} table entries, ` +
    "all derived from the live table, zero hand-typed role/route literals",
  );

  // ---- (b) structural same-source proof -- done-when 3 -------------------------------------------
  // (a) above can still pass if navItemsFor reads a SECOND, independently-declared array that
  // happens to be deep-equal to ROLE_ROUTES today -- exactly the drift done-when 3 warns against.
  // Mutate the REAL, live-imported ROLE_ROUTES object after import (JS does not enforce TS's
  // `readonly` at runtime) and confirm navItemsFor observes the mutation on its very next call --
  // only a live reference (not a value copied at import time) would see this.
  //
  // NOTE for a future hardening pass: this probe depends on ROLE_ROUTES never being
  // `Object.freeze`d. If route-policy.ts is later frozen for genuine runtime immutability, the
  // `Object.assign` below throws TypeError under strict-mode ESM -- a false-alarm breakage
  // unrelated to drift. Fix the probe (e.g. mutate a shallow clone and pass IT through a
  // freeze-tolerant seam) rather than deleting it if that day comes.
  // issue #312 [E20.S1] Goal A: ROLE_ROUTES.manager is now an array -- target its FIRST element
  // (ROLE_ROUTES.manager[0]) instead of the (formerly single) entry object itself. Still proves
  // the same thing (nav reads the live object graph, not a copy); only the addressing changes.
  const before = { ...ROLE_ROUTES.manager[0] };
  Object.assign(ROLE_ROUTES.manager[0], { navLabel: "MUTATED-PROBE", implemented: true });
  const probed = navItemsFor(true, "manager").some((item) => item.label === "MUTATED-PROBE");
  Object.assign(ROLE_ROUTES.manager[0], before); // restore before any later assertion depends on it
  assert.ok(
    probed,
    "navItemsFor did not observe a live mutation of the real ROLE_ROUTES.manager entry -- this " +
    "means nav is reading a SEPARATE, merely-equal copy of the table, which is the drift done-when " +
    "3 exists to prevent",
  );
  console.log("OK: structural same-source proof -- navItemsFor observes a live mutation of the real ROLE_ROUTES object");

  // ---- (c) named regression assertions -------------------------------------------------------
  assert.deepEqual(
    navItemsFor(false, undefined), [],
    "an anonymous /login visitor must see NO nav items at all",
  );
  assert.ok(
    navItemsFor(true, "manager").some((item) => item.label === "Manager" && item.href === "/manager"),
    "a manager's nav must link to /manager now that the page is real (issue #313)",
  );
  assert.ok(
    !navItemsFor(true, "ic").some((item) => item.href.includes("dev")),
    "no nav entry may link to a /dev/... route -- those are env-gated out of every production build",
  );
  assert.ok(
    navItemsFor(true, "ic").some((item) => item.href === "/ic"),
    "an ic session must see a real, linked IC item",
  );
  console.log("OK: named regression assertions -- anonymous nav is empty, manager has no dead /manager link, no dev-route leak, ic sees its real item");

  console.log("\nOK: prove-nav-items -- nav derived from and cross-checked against the live route-policy.ts table");
}

// Only run the proof when invoked directly -- prove-shell-responsive-frame.mjs imports
// loadNav() from here and must not trigger a second full run as a side effect.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((err) => {
    console.error("FAIL: prove-nav-items");
    console.error(err);
    process.exit(1);
  });
}
