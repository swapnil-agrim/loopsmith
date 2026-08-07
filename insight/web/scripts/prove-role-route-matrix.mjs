// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #309 [E19.S1], .sdlc/plans/309.md. Two parts, mirroring
// prove-every-route-is-private-by-default.mjs's own Part B (compiled route-policy.ts, pure logic)
// and Part C (compiled + EXECUTED proxy.ts against stubs) -- that file's own Part A (synthetic
// filesystem-walk fixture) has no role analogue and is not repeated here.
//
//   PART A -- compiles the real route-policy.ts with the local tsc, dynamic-imports it, and
//   table-tests decide() directly: each role against its own route and a DIFFERENT role's route,
//   an unknown role string, role=undefined, the deliberately-unregistered "/finance-exports"
//   against every known role (done-when 3), and the two shared-authenticated routes against every
//   role including an unknown one (Decision 3's carve-out, proven not just asserted).
//
//   PART B -- compiles and EXECUTES the real src/proxy.ts against stub next/server (extended with
//   NextResponse.json) and stub @/auth (Req.auth widened to carry user.role). Drives the real,
//   compiled handler and asserts the WHOLE captured response body for a forbidden request --
//   done-when 2's own wording, verified on the actual enforcement point, not the pure function
//   alone (same reasoning as the existing script's own Part C doc comment).
import assert from "node:assert/strict";
import { writeFileSync, readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

const SRC_AUTH_LIB = path.join(WEB, "src", "lib", "auth");

// --------------------------------------------------------------------------------------- part A

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

const ROLES = ["manager", "leadership", "ic", "cross-functional"];
const UNREGISTERED_ROUTE = "/finance-exports"; // deliberately in NO list anywhere -- done-when 3

async function partA() {
  const mod = await runScenarioAsync("insight-web-role-route-matrix-proof-", async (dir) => {
    const emitted = compileRoutePolicy(dir);
    return import(pathToFileURL(emitted).href);
  });
  const { decide, ROLE_ROUTES } = mod;

  assert.deepEqual(
    Object.keys(ROLE_ROUTES).sort(), [...ROLES].sort(),
    `ROLE_ROUTES must have exactly the four canonical roles, got: ${Object.keys(ROLE_ROUTES)}`,
  );

  const OWN_ROUTE = { manager: "/manager", leadership: "/leadership", ic: "/ic", "cross-functional": "/cross-functional" };

  for (const role of ROLES) {
    // A role reaches its own route.
    assert.equal(
      decide(OWN_ROUTE[role], true, role), "allow",
      `${role} must be allowed on its own route ${OWN_ROUTE[role]}`,
    );
    // A role does NOT reach another role's route.
    for (const other of ROLES) {
      if (other === role) continue;
      assert.equal(
        decide(OWN_ROUTE[other], true, role), "forbid",
        `${role} must be forbidden on ${other}'s route ${OWN_ROUTE[other]}`,
      );
    }
    // done-when 3: an unregistered route is denied for EVERY known role.
    assert.equal(
      decide(UNREGISTERED_ROUTE, true, role), "forbid",
      `${role} must be forbidden on the deliberately-unregistered route ${UNREGISTERED_ROUTE}`,
    );
    // Decision 3: shared-authenticated routes stay reachable for every real role too.
    assert.equal(decide("/", true, role), "allow", `${role} must reach the shared "/" route`);
    assert.equal(
      decide("/dev/absence-states", true, role), "allow",
      `${role} must reach the shared /dev/absence-states route`,
    );
  }

  // Decision 2: an unknown role string -- NOT a crash, a denial.
  assert.doesNotThrow(() => decide("/manager", true, "owner"));
  assert.equal(decide("/manager", true, "owner"), "forbid", "an unknown role must be forbidden, not allowed");
  // An unknown role still reaches the shared routes (Decision 3 is role-agnostic).
  assert.equal(decide("/", true, "owner"), "allow", "an unknown role must still reach the shared / route");

  // Decision 2: role=undefined -- NOT a crash, a denial (a session that predates any role).
  assert.doesNotThrow(() => decide("/manager", true, undefined));
  assert.equal(decide("/manager", true, undefined), "forbid", "role=undefined must be forbidden, not allowed");

  // Regression: the session gate still wins outright -- role is never even inspected without one.
  assert.equal(decide("/manager", false, "manager"), "redirect", "no session must redirect regardless of role");

  // Regression: public routes are unaffected by any of this.
  assert.equal(decide("/login", false, undefined), "allow", "the public /login route must be unaffected");

  console.log("OK: route-policy.ts's role matrix -- own route allowed, other roles forbidden, unknown role and unregistered route both denied, shared routes reachable by every role");
}

// --------------------------------------------------------------------------------------- part B
//
// Compiles and EXECUTES the real src/proxy.ts against stub next/server + stub @/auth -- same
// pattern as prove-every-route-is-private-by-default.mjs's own Part C, and for the same reason
// stated there: Part A above proves decide() is right, but proxy.ts is what CALLS it, and nothing
// in Part A imports proxy.ts at all. Stubs replace only the two FRAMEWORK imports; route-policy.ts
// is the real file, and proxy.ts's body is compiled verbatim, never rewritten. No new imports are
// needed beyond what Part A already pulled in at the top of the file (writeFileSync, readFileSync,
// path, pathToFileURL, runScenarioAsync, runTsc are all already in scope).

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

async function main() {
  await partA();
  await partB();
}

main();
