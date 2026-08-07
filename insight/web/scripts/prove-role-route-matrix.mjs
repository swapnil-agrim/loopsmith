// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #309 [E19.S1], .sdlc/plans/309.md. Three parts, mirroring
// prove-every-route-is-private-by-default.mjs's own Part B (compiled route-policy.ts, pure logic)
// and Part C (compiled + EXECUTED proxy.ts against stubs) -- that file's own Part A (synthetic
// filesystem-walk fixture) has no role analogue and is not repeated here.
//
//   PART A -- issue #311 [E19.S3], .sdlc/plans/311.md Task 3: compiles the real route-policy.ts
//   with the local tsc, dynamic-imports it, and table-tests decide() directly -- GENERATED from
//   the real, live `ROLE_ROUTES`/`SHARED_AUTHENTICATED_ROUTES` exports, not hand-typed copies of
//   the role list or route strings (the defect this story exists to fix: the old `ROLES` array and
//   `OWN_ROUTE` map here were both retyped literals that a route/role change could silently drift
//   from). issue #312 [E20.S1] Goal A: every route string appearing anywhere in `ROLE_ROUTES` is
//   checked against its live GRANTEE SET (allow iff granted, forbid otherwise) -- the
//   generalisation of "a role's own route is denied to every other role" once a route can have
//   more than one grantee (/delivery has three). Also: an unknown role string, role=undefined, the
//   deliberately-unregistered "/finance-exports" against every known role (done-when 3), and every
//   SHARED_AUTHENTICATED_ROUTES entry against every role including an unknown one (Decision 3's
//   carve-out, proven not just asserted) -- all iterated over the live table, so a route added to
//   either object tomorrow is automatically exercised here.
//
//   PART B -- compiles and EXECUTES the real src/proxy.ts against stub next/server (extended with
//   NextResponse.json) and stub @/auth (Req.auth widened to carry user.role). Drives the real,
//   compiled handler and asserts the WHOLE captured response body for a forbidden request --
//   done-when 2's own wording, verified on the actual enforcement point, not the pure function
//   alone (same reasoning as the existing script's own Part C doc comment).
//
//   PART C -- issue #311 [E19.S3] Task 3b: filesystem <-> table drift, both directions. Reuses
//   walkRoutes(SRC_APP) (scripts/lib/route-inventory.mjs, the SAME real-filesystem walker
//   prove-every-route-is-private-by-default.mjs's own Part B already uses for the public/private
//   axis) and the SAME compiled route-policy.ts module Part A already produced -- a route that
//   exists on disk but is missing from the table, or a table entry marked `implemented: true` with
//   no page on disk, is a detectable, tested condition, not a silent one.
import assert from "node:assert/strict";
import { writeFileSync, readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";
import { walkRoutes } from "./lib/route-inventory.mjs";

const SRC_AUTH_LIB = path.join(WEB, "src", "lib", "auth");
const SRC_APP = path.join(WEB, "src", "app");

// --------------------------------------------------------------------------------- shared compile

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts"],
  };
}

function compileRoutePolicy(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  writeFileSync(
    path.join(dir, "route-policy.ts"),
    readFileSync(path.join(SRC_AUTH_LIB, "route-policy.ts"), "utf-8"),
  );
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `route-policy.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "route-policy.js");
}

async function loadRoutePolicy() {
  return runScenarioAsync("insight-web-role-route-matrix-proof-", async (dir) => {
    const emitted = compileRoutePolicy(dir);
    return import(pathToFileURL(emitted).href);
  });
}

const UNREGISTERED_ROUTE = "/finance-exports"; // deliberately in NO list anywhere -- done-when 3

// --------------------------------------------------------------------------------------- part A

async function partA(mod) {
  const { decide, ROLE_ROUTES, SHARED_AUTHENTICATED_ROUTES, representativePath, isKnownRole } = mod;

  // Generated, not hand-typed (issue #311): the role axis is whatever ROLE_ROUTES actually
  // declares right now.
  const ROLES = Object.keys(ROLE_ROUTES);
  assert.ok(ROLES.length >= 1, "ROLE_ROUTES must declare at least one role");

  // Sanity guard: representativePath() picks entry.exact[0] ?? entry.prefix[0] and assumes exactly
  // one path per entry -- documents and enforces that assumption so a second string silently added
  // to one array fails loudly here rather than picking the wrong string somewhere downstream.
  //
  // issue #312 [E20.S1] Goal A: ROLE_ROUTES[role] is now itself an array of entries (a role can
  // have more than one route), so this must iterate every (role, entry) pair, not one entry per
  // role -- flatMap over Object.entries(ROLE_ROUTES) instead of Object.entries(ROLE_ROUTES) alone.
  for (const [key, entry] of [
    ...Object.entries(ROLE_ROUTES).flatMap(
      ([role, entries]) => entries.map((e, i) => [`${role}[${i}]`, e]),
    ),
    ...SHARED_AUTHENTICATED_ROUTES.map((entry, i) => [`SHARED_AUTHENTICATED_ROUTES[${i}]`, entry]),
  ]) {
    const list = entry.exact ?? entry.prefix;
    assert.equal(
      list.length, 1,
      `${key}'s route pattern must have exactly one path (representativePath()'s assumption) -- ` +
      `got ${JSON.stringify(list)}`,
    );
  }

  // issue #312 [E20.S1] Goal A: "a role's own route is denied to every other role" (the old
  // per-role block that lived here) assumed exactly one route per role, so "own route" vs "every
  // other role" was unambiguous. That framing breaks the moment a route has more than one grantee
  // (/delivery now has three). Replaced with the table-derived generalisation: for every route
  // string appearing anywhere in ROLE_ROUTES, compute its GRANTEE SET from the live table, then
  // assert every role gets "allow" iff it is a grantee and "forbid" otherwise. GENERATED, not
  // hand-typed -- a route added to any role's array tomorrow is automatically covered here with
  // zero new code. Collapses to the old invariant exactly when a route has exactly one grantee
  // (every pre-#312 route), so this is zero coverage loss for the existing single-grantee routes.
  const routeGrantees = new Map();
  for (const [role, entries] of Object.entries(ROLE_ROUTES)) {
    for (const entry of entries) {
      const route = representativePath(entry);
      if (!routeGrantees.has(route)) routeGrantees.set(route, new Set());
      routeGrantees.get(route).add(role);
    }
  }
  for (const [route, grantees] of routeGrantees) {
    for (const role of ROLES) {
      const expected = grantees.has(role) ? "allow" : "forbid";
      assert.equal(
        decide(route, true, role), expected,
        `${role} on ${route}: expected ${expected} (granted to: ${[...grantees].join(", ")})`,
      );
    }
  }
  console.log(
    `OK: route-grantee cross-product -- ${routeGrantees.size} distinct route(s) in ROLE_ROUTES, ` +
    "every role checked allow/forbid against its live grantee set, GENERATED from the table",
  );

  // issue #312 [E20.S1] Goal A, Task A2: belt-and-suspenders on top of the generated cross-product
  // above (which already covers /delivery automatically the moment the table changes, with zero
  // new code) -- one named block pinning the SPECIFIC policy in a form a reviewer can read without
  // deriving it from the generated loop: /delivery granted to manager/leadership/ic, denied to
  // cross-functional. cross-functional's denial is structural (no /delivery entry in its array),
  // never a special case.
  assert.equal(decide("/delivery", true, "manager"), "allow", "manager must reach /delivery");
  assert.equal(decide("/delivery", true, "leadership"), "allow", "leadership must reach /delivery");
  assert.equal(decide("/delivery", true, "ic"), "allow", "ic must reach /delivery");
  assert.equal(
    decide("/delivery", true, "cross-functional"), "forbid",
    "cross-functional must be denied /delivery",
  );
  console.log("OK: /delivery named grants -- manager/leadership/ic allowed, cross-functional forbidden");

  // AMENDMENT 2 (.sdlc/plans/312.md): the block above (lines ~100-115 pre-#312) is REPLACED by the
  // table-derived cross-product above. The unregistered-route and shared-route-reachability checks
  // below SURVIVE UNCHANGED inside the same `for (const role of ROLES)` loop -- only the now-dead
  // `ownRoute`/"other role" variables are removed.
  for (const role of ROLES) {
    // done-when 3: an unregistered route is denied for EVERY known role.
    assert.equal(
      decide(UNREGISTERED_ROUTE, true, role), "forbid",
      `${role} must be forbidden on the deliberately-unregistered route ${UNREGISTERED_ROUTE}`,
    );
    // Decision 3: EVERY shared-authenticated route stays reachable for every real role too --
    // iterated over the live array, so a THIRD shared route added tomorrow is automatically
    // covered instead of getting zero new coverage silently (issue #311's own named gap).
    for (const entry of SHARED_AUTHENTICATED_ROUTES) {
      const sharedRoute = representativePath(entry);
      assert.equal(
        decide(sharedRoute, true, role), "allow",
        `${role} must reach the shared ${sharedRoute} route`,
      );
    }
  }

  // Decision 2: an unknown role string -- NOT a crash, a denial.
  assert.doesNotThrow(() => decide("/manager", true, "owner"));
  assert.equal(decide("/manager", true, "owner"), "forbid", "an unknown role must be forbidden, not allowed");
  assert.equal(isKnownRole("owner"), false, "sanity: \"owner\" must not be a known role");
  // An unknown role still reaches every shared route (Decision 3 is role-agnostic).
  for (const entry of SHARED_AUTHENTICATED_ROUTES) {
    const sharedRoute = representativePath(entry);
    assert.equal(
      decide(sharedRoute, true, "owner"), "allow",
      `an unknown role must still reach the shared ${sharedRoute} route`,
    );
  }

  // Decision 2: role=undefined -- NOT a crash, a denial (a session that predates any role).
  assert.doesNotThrow(() => decide("/manager", true, undefined));
  assert.equal(decide("/manager", true, undefined), "forbid", "role=undefined must be forbidden, not allowed");

  // Regression: the session gate still wins outright -- role is never even inspected without one.
  assert.equal(decide("/manager", false, "manager"), "redirect", "no session must redirect regardless of role");

  // Regression: public routes are unaffected by any of this.
  assert.equal(decide("/login", false, undefined), "allow", "the public /login route must be unaffected");

  console.log(
    `OK: route-policy.ts's role matrix -- ${ROLES.length} role(s) and ${SHARED_AUTHENTICATED_ROUTES.length} ` +
    "shared route(s), all GENERATED from the live table, own route allowed, other roles forbidden, " +
    "unknown role and unregistered route both denied, every shared route reachable by every role",
  );
}

// --------------------------------------------------------------------------------------- part B
//
// Compiles and EXECUTES the real src/proxy.ts against stub next/server + stub @/auth -- same
// pattern as prove-every-route-is-private-by-default.mjs's own Part C, and for the same reason
// stated there: Part A above proves decide() is right, but proxy.ts is what CALLS it, and nothing
// in Part A imports proxy.ts at all. Stubs replace only the two FRAMEWORK imports; route-policy.ts
// is the real file, and proxy.ts's body is compiled verbatim, never rewritten.

// issue #309 Decision 6: types added on every param (unlike the plan doc's own JS-flavored
// sketch) because this stub is written into a real .ts file compiled under this repo's
// strict: true -- noImplicitAny rejects an untyped arrow function param outright. Matches the
// existing prove-every-route-is-private-by-default.mjs Part C stub's own typed shape
// (`redirect: (url: URL) => ...`), extended with the one new member proxy.ts now calls: `.json`.
const STUB_NEXT_SERVER = `// scratch stub -- not shipped.
export const NextResponse = {
  redirect: (url: URL) => ({ kind: "redirect" as const, location: String(url) }),
  next: () => ({ kind: "next" as const }),
  json: (body: unknown, init?: { status?: number }) =>
    ({ kind: "forbid" as const, status: init?.status, body }),
};
`;

const STUB_AUTH = `// scratch stub -- not shipped. Same shape as
// prove-every-route-is-private-by-default.mjs's own Part C stub (issue #309 Decision 6) -- Req.auth
// carries an optional user.role so the real proxy.ts's req.auth?.user?.role compiles.
type Req = { nextUrl: URL; auth: { user?: { role?: string } } | null };
export const auth = <T,>(cb: (req: Req) => T) => Promise.resolve(cb);
`;

function compileProxy(dir) {
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify({
    compilerOptions: {
      target: "ES2022", lib: ["ES2022", "DOM"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      baseUrl: ".",
      paths: {
        "next/server": ["./stub-next-server"],
        "@/auth": ["./stub-auth"],
        "@/lib/auth/route-policy": ["./route-policy"],
      },
    },
    include: ["*.ts"],
  }, null, 2));
  writeFileSync(path.join(dir, "stub-next-server.ts"), STUB_NEXT_SERVER);
  writeFileSync(path.join(dir, "stub-auth.ts"), STUB_AUTH);
  writeFileSync(
    path.join(dir, "route-policy.ts"),
    readFileSync(path.join(SRC_AUTH_LIB, "route-policy.ts"), "utf-8"),
  );
  writeFileSync(
    path.join(dir, "proxy.ts"),
    readFileSync(path.join(WEB, "src", "proxy.ts"), "utf-8"),
  );

  const { ok, output } = runTsc(dir);
  assert.ok(ok, `src/proxy.ts must compile clean against the scratch stubs:\n${output}`);

  const emitted = path.join(dir, "out", "proxy.js");
  let js = readFileSync(emitted, "utf-8");
  let rewrites = 0;
  for (const [from, to] of [
    ["next/server", "./stub-next-server.js"],
    ["@/auth", "./stub-auth.js"],
    ["@/lib/auth/route-policy", "./route-policy.js"],
  ]) {
    const before = js;
    js = js.replace(`from "${from}"`, `from "${to}"`);
    if (js !== before) rewrites += 1;
  }
  assert.equal(rewrites, 3, `expected to rewrite 3 import specifiers in the emitted proxy.js, did ${rewrites}`);
  writeFileSync(emitted, js);
  return emitted;
}

async function partB() {
  const mod = await runScenarioAsync("insight-web-role-forbid-proxy-proof-", async (dir) => {
    const emitted = compileProxy(dir);
    return import(pathToFileURL(emitted).href);
  });
  const handler = mod.default;
  assert.equal(typeof handler, "function", "src/proxy.ts's default export must BE a function");

  const call = (pathname, auth) =>
    handler({ nextUrl: new URL(pathname, "https://insight.example"), auth });

  // done-when 2, literally: a forbidden request's WHOLE response body, asserted exactly.
  const forbidden = await call("/manager", { user: { role: "leadership" } });
  assert.equal(forbidden.kind, "forbid", "a role-mismatched request must be forbidden");
  assert.equal(forbidden.status, 403, "a forbidden request must carry HTTP 403");
  assert.deepEqual(
    forbidden.body, { error: "forbidden" },
    `the ENTIRE forbidden response body must be exactly {error:"forbidden"} -- no route name, no ` +
    `role name, no underlying data. Got: ${JSON.stringify(forbidden.body)}`,
  );

  // Allowed: a role reaching its own route passes through.
  assert.equal(
    (await call("/manager", { user: { role: "manager" } })).kind, "next",
    "manager must be let through to /manager",
  );

  // done-when 3, through the REAL handler (not just the pure decide() in Part A): a deliberately
  // unregistered route is forbidden even for a real, valid role.
  const unregistered = await call(UNREGISTERED_ROUTE, { user: { role: "manager" } });
  assert.equal(unregistered.kind, "forbid", `${UNREGISTERED_ROUTE} must be forbidden even for a valid role`);
  assert.deepEqual(unregistered.body, { error: "forbidden" });

  // Decision 2, through the REAL handler: an unknown role denies, does not throw.
  const unknownRole = await call("/manager", { user: { role: "owner" } });
  assert.equal(unknownRole.kind, "forbid", "an unknown role must be forbidden through the real handler too");

  // Regression: no session still redirects, role never inspected.
  const noSession = await call("/manager", null);
  assert.equal(noSession.kind, "redirect", "no session must still redirect regardless of role");
  assert.equal(new URL(noSession.location).pathname, "/login");

  // Regression: the shared "/" route stays reachable for a real role (Decision 3 didn't regress
  // #307's own Home-page behavior).
  assert.equal(
    (await call("/", { user: { role: "ic" } })).kind, "next",
    "the shared / route must still be reachable for any real role",
  );

  console.log("OK: src/proxy.ts's own handler returns a fixed, data-free 403 body for a forbidden request, and denies both an unknown role and a deliberately-unregistered route without throwing");
}

// --------------------------------------------------------------------------------------- part C
//
// issue #311 [E19.S3] Task 3b: filesystem <-> table drift, both directions -- the directive's
// explicit ask, "a route that exists in the filesystem but is missing from the table, or vice
// versa, is a detectable, tested condition rather than a silent one."

// AMENDMENT 2 (plan review of .sdlc/plans/311.md): mirrors matchesRoutes' prefix-or-exact rule
// (route-policy.ts) exactly -- `route === representativePath(e) || route.startsWith(rep + "/")` --
// so this drift check can never disagree with decide() about what a pattern covers. Re-derived
// locally (not imported) because route-policy.ts's own matchesPattern/matchesRoutes are module-
// private, and exporting an internal matcher just for one test is a worse trade than restating the
// same one-line rule that already appears three times inside that file (matchesRoutes,
// matchesPattern, representativePath's own doc comment) -- a deliberate, tiny exception to "never
// retype policy logic."
function matchesRepresentative(route, entry, representativePath) {
  const rep = representativePath(entry);
  return route === rep || route.startsWith(`${rep}/`);
}

/** Both directions of the drift check. `routes` is the real (or synthetic) filesystem inventory;
 *  `reachabilityEntries` is every SHARED_AUTHENTICATED_ROUTES/ROLE_ROUTES entry. */
function assertRoutesCovered(routes, reachabilityEntries, isPublicRoute, representativePath) {
  // Direction 1: every real, non-public route must be claimed by some table entry, and EVERY
  // matching entry must say the page is actually built.
  //
  // issue #312 [E20.S1] Goal B (plan-review carry-over from Goal A's own PR #509 review): this
  // USED to be `reachabilityEntries.find(...)` -- picking the FIRST matching entry only. That was
  // silently correct pre-#312, when every route had exactly one grantee. Now that /delivery has
  // THREE grantee entries for one path (manager/leadership/ic), `.find()` would validate only
  // whichever entry happens to come first in object-iteration order: if manager's /delivery entry
  // were flipped to `implemented:true` while leadership's or ic's stayed `false`, `.find()` would
  // return manager's (already true) entry and this direction would pass -- while Direction 2 below
  // SKIPS any entry with `implemented:false`, so it says nothing about leadership/ic either. Net
  // result: leadership/ic would silently lose their nav link to a page decide() genuinely allows
  // them, and NEITHER direction would catch it. Fixed by requiring ALL matching entries to agree,
  // not just the first found.
  for (const route of routes) {
    if (isPublicRoute(route)) continue; // already proven by the sibling public/private proof
    const matching = reachabilityEntries.filter((e) => matchesRepresentative(route, e, representativePath));
    assert.ok(matching.length > 0, `real route ${route} is not public and matches NO entry in ` +
      `SHARED_AUTHENTICATED_ROUTES or ROLE_ROUTES -- decide() would forbid it for every role, ` +
      `silently, because nothing in the table claims it`);
    assert.ok(
      matching.every((e) => e.implemented),
      `real route ${route} exists on disk but at least one of its ${matching.length} matching ` +
      `table entries has implemented:false -- flip EVERY entry claiming this route to true now ` +
      `that the page exists (a shared route with one grantee left false silently loses its nav ` +
      `link for that role, undetected by either drift direction)`,
    );
  }
  // Direction 2: every table entry marked implemented:true must have a real page on disk.
  for (const entry of reachabilityEntries) {
    if (!entry.implemented) continue;
    const p = representativePath(entry);
    const covered = routes.some((r) => r === p || r.startsWith(`${p}/`));
    assert.ok(covered, `table entry ${p} is marked implemented:true but no page exists on ` +
      `disk at or under it`);
  }
}

function partC(mod) {
  const { ROLE_ROUTES, SHARED_AUTHENTICATED_ROUTES, isPublicRoute, representativePath } = mod;
  // issue #312 [E20.S1] Goal A: Object.values(ROLE_ROUTES) now yields an array of entries per
  // role -- flatten so reachabilityEntries stays a flat list of individual table entries.
  const reachabilityEntries = [...SHARED_AUTHENTICATED_ROUTES, ...Object.values(ROLE_ROUTES).flat()];

  // Real-tree case: the actual src/app/ inventory against the actual table. Must pass with zero
  // findings -- confirms no pre-existing drift.
  const realRoutes = walkRoutes(SRC_APP);
  assertRoutesCovered(realRoutes, reachabilityEntries, isPublicRoute, representativePath);
  console.log(
    `OK: filesystem <-> table drift check -- ${realRoutes.length} real route(s) all claimed by an ` +
    "implemented table entry, and every implemented table entry has a real page on disk",
  );

  // ---- negative controls -------------------------------------------------------------------
  // Proves the checker can actually FAIL, not just happens to pass on today's clean tree (same
  // ethos as prove-every-route-is-private-by-default.mjs Part A's synthetic-tree check).

  // 1. A page shipped, the table forgot it: a real route with no matching entry at all.
  assert.throws(
    () => assertRoutesCovered(
      ["/orphan-page"],
      [], // no entries at all -- nothing could possibly claim it
      () => false, // not public
      representativePath,
    ),
    /matches NO entry/,
    "negative control 1 failed to fire: a filesystem route with no table entry must be caught",
  );
  console.log("OK: negative control 1 -- an unclaimed real route is correctly caught (page shipped, table forgot it)");

  // 2. The table says live, the page doesn't exist: implemented:true with nothing on disk.
  assert.throws(
    () => assertRoutesCovered(
      [], // empty filesystem -- the entry below claims a page that isn't there
      [{ exact: ["/ghost-page"], implemented: true }],
      () => false,
      representativePath,
    ),
    /no page exists on/,
    "negative control 2 failed to fire: an implemented:true entry with no real page must be caught",
  );
  console.log("OK: negative control 2 -- an implemented:true entry with no real page is correctly caught (table says live, page doesn't exist)");

  // 3. issue #312 [E20.S1] Goal B: the .find() -> .filter()+.every() fix above, proven with teeth.
  // TWO entries share one real, on-disk route; the FIRST (in array order) is already
  // implemented:true, the SECOND is still implemented:false. A `.find()`-based Direction 1 would
  // stop at the first match and pass vacuously; this must still fail, because the second grantee's
  // page-existence claim disagrees.
  assert.throws(
    () => assertRoutesCovered(
      ["/shared-page"],
      [
        { exact: ["/shared-page"], implemented: true },
        { exact: ["/shared-page"], implemented: false },
      ],
      () => false,
      representativePath,
    ),
    /implemented:false/,
    "negative control 3 failed to fire: a real route with TWO grantee entries, only the FIRST " +
    "implemented:true, must still be caught -- a .find()-based check would stop at the first " +
    "match and pass vacuously",
  );
  console.log(
    "OK: negative control 3 -- a multi-grantee route with one entry left implemented:false is " +
    "correctly caught even though another matching entry is already implemented:true",
  );
}

async function main() {
  const mod = await loadRoutePolicy();
  await partA(mod);
  await partB();
  partC(mod);
}

main();
