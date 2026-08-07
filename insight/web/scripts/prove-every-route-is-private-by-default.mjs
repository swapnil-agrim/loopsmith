// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2/3/4. The mechanical proof for done-when 3:
// "every route is asserted individually ... not a sample." Two parts:
//
//   PART A -- route-inventory.mjs's segmentFor()/walkRoutes() mapping logic, table-tested against
//   SYNTHETIC fixtures (route groups, private folders, dynamic segments, catch-alls) built in a
//   scratch dir -- this repo's REAL tree has none of those shapes yet, so this is how the
//   MECHANISM itself, not just today's two routes, gets proven against what E19 will add.
//
//   PART B -- walks the REAL src/app/, compiles the REAL route-policy.ts with the local tsc
//   (same pattern as prove-metric-view-behavior.mjs), dynamic-imports it, and asserts EVERY
//   discovered route decide()s exactly as its PUBLIC_EXACT_ROUTES/PUBLIC_PREFIX_ROUTES
//   membership predicts, with hasSession=false -- the actual redirect-every-protected-route
//   proof, importing the SAME module proxy.ts imports (Decision 3), so the two cannot drift.
//
//   PART C -- EXECUTES the real proxy.ts's own default export. Parts A and B together still left
//   the nine lines that actually enforce anything untested: they prove decide() is right, but
//   proxy.ts is what CALLS it, and nothing imported proxy.ts at all. Inverting its branch to
//   `if (decision === "allow") return NextResponse.redirect(...)`, or dropping the `!!req.auth`
//   argument, makes every route public and every assertion in Parts A and B still passes -- the
//   whole `npm run test` chain exits 0 while the app protects nothing. Found by two independent
//   author-blind reviews of this change, which both landed on it separately. So Part C compiles
//   the REAL src/proxy.ts against stub `next/server` and `@/auth` modules (the stub `auth()` is
//   the identity function, which makes proxy.ts's default export the callback itself) and runs
//   it against fake requests. Stubs replace only the two FRAMEWORK imports; route-policy.ts is
//   the real file, and proxy.ts's body is compiled verbatim, never rewritten.
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";
import { walkRoutes } from "./lib/route-inventory.mjs";

const SRC_APP = path.join(WEB, "src", "app");
const SRC_AUTH_LIB = path.join(WEB, "src", "lib", "auth");

// --------------------------------------------------------------------------------------- part A

function buildSyntheticAppTree() {
  const root = mkdtempSync(path.join(tmpdir(), "insight-web-route-inventory-fixture-"));
  const touch = (relDir) => {
    const dir = path.join(root, relDir);
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, "page.tsx"), "export default function P() { return null; }");
  };
  touch("(marketing)/about"); // route group -> /about, no "(marketing)" segment
  touch("blog/[slug]"); // dynamic -> /blog/sample-id
  touch("docs/[...slug]"); // catch-all -> /docs/sample-catch-all
  touch("shop/[[...filters]]"); // optional catch-all -> /shop/sample-catch-all
  touch("_internal/secret"); // private folder -> excluded ENTIRELY
  touch(""); // root page -> /
  return root;
}

function partA() {
  const root = buildSyntheticAppTree();
  try {
    const routes = walkRoutes(root);
    assert.deepEqual(
      routes,
      ["/", "/about", "/blog/sample-id", "/docs/sample-catch-all", "/shop/sample-catch-all"].sort(),
      `synthetic route-shape mapping is wrong, got: ${JSON.stringify(routes)}`,
    );
    assert.ok(
      !routes.some((r) => r.includes("secret") || r.includes("_internal")),
      "a private folder (_internal) must be excluded from the walk entirely, subtree and all",
    );
    console.log("OK: route-inventory mapping is correct for route groups, dynamic segments, catch-alls, and private folders");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

// --------------------------------------------------------------------------------------- part B

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
  copyFileSync(path.join(SRC_AUTH_LIB, "route-policy.ts"), path.join(dir, "route-policy.ts"));
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `route-policy.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "route-policy.js");
}

async function partB() {
  const { decide, isPublicRoute, PUBLIC_EXACT_ROUTES, PUBLIC_PREFIX_ROUTES } =
    await runScenarioAsync("insight-web-route-privacy-proof-", async (dir) => {
      const emitted = compileRoutePolicy(dir);
      return import(pathToFileURL(emitted).href);
    });

  assert.ok(
    PUBLIC_EXACT_ROUTES.length + PUBLIC_PREFIX_ROUTES.length <= 4,
    "the public allowlist must stay small and explicit (Decision 7) -- " +
    `got ${PUBLIC_EXACT_ROUTES.length + PUBLIC_PREFIX_ROUTES.length} entries`,
  );

  const routes = walkRoutes(SRC_APP);
  assert.ok(routes.length >= 2, "sanity: the real app must have at least the two known routes");

  let redirectCount = 0;
  for (const route of routes) {
    const decision = decide(route, /* hasSession */ false);
    const expected = isPublicRoute(route) ? "allow" : "redirect";
    assert.equal(
      decision, expected,
      `route ${route}: expected decide() to ${expected}, got ${decision}`,
    );
    if (decision === "redirect") redirectCount += 1;
    console.log(`OK: ${route} -> ${decision}`);
  }
  // Not vacuous: at least one real route must actually redirect, or a broken decide() that
  // always returns "allow" would pass every assertion above trivially.
  assert.ok(redirectCount >= 1, "at least one real route must redirect an unauthenticated request");
}

// --------------------------------------------------------------------------------------- part C

const STUB_NEXT_SERVER = `// scratch stub -- not shipped.
export const NextResponse = {
  redirect: (url: URL) => ({ kind: "redirect" as const, location: String(url) }),
  next: () => ({ kind: "next" as const }),
};
`;

// Real next-auth's auth() returns a handler that decodes the session and hands the callback a req
// carrying \`auth\`. The stub returns the callback itself, so we can drive it with a fake req and no
// AUTH_SECRET, no JWT, no server.
//
// It resolves to that callback through a PROMISE, deliberately, because that is what the real one
// does HERE: auth.ts uses next-auth's lazy config form (a function, so useSecureCookies can be
// per-request -- Decision 5), and initAuth()'s \`typeof config === "function"\` branch returns
// \`async (...args) => ...\`, making auth(cb) a Promise of the handler rather than the handler.
// An earlier version of this stub returned the callback DIRECTLY, and that is precisely what let a
// real defect through green: proxy.ts did \`export default auth(...)\`, which exports a Promise, and
// Next 16's proxy loader throws "must export a function named \\\`proxy\\\` or a default function" on
// every request. typecheck/lint/build/test all passed; only CI's booted-server proof failed.
// A stub that is easier than the real thing tests the stub. This one matches the real shape.
const STUB_AUTH = `// scratch stub -- not shipped.
type Req = { nextUrl: URL; auth: unknown };
export const auth = <T,>(cb: (req: Req) => T) => Promise.resolve(cb);
`;

function compileProxy(dir) {
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify({
    compilerOptions: {
      // lib DOM for the URL/URLSearchParams globals proxy.ts uses -- a tmpdir scratch has no
      // node_modules ancestor to resolve @types/node from (see tsc-scratch.mjs's own docstring).
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
  copyFileSync(path.join(SRC_AUTH_LIB, "route-policy.ts"), path.join(dir, "route-policy.ts"));
  copyFileSync(path.join(WEB, "src", "proxy.ts"), path.join(dir, "proxy.ts"));

  const { ok, output } = runTsc(dir);
  assert.ok(ok, `src/proxy.ts must compile clean against the scratch stubs:\n${output}`);

  // tsc's `paths` redirect TYPE resolution only -- the emitted JS keeps the original bare
  // specifiers, which no runtime can resolve here. Rewrite the three import specifiers (and only
  // those) in the EMITTED output, never in the .ts source, so the code under test is compiled
  // verbatim. Asserting the count catches a silently-missed rewrite, which would otherwise
  // surface as a confusing ERR_MODULE_NOT_FOUND instead of a real failure.
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

async function partC() {
  const mod = await runScenarioAsync("insight-web-proxy-execution-proof-", async (dir) => {
    const emitted = compileProxy(dir);
    return import(pathToFileURL(emitted).href);
  });

  // Next 16's proxy loader does exactly this check on the default export
  // (next/dist/build/templates/middleware.js: `typeof handlerUserland !== "function"`) and throws
  // for EVERY request if it fails -- so assert it the same way, first. A Promise here (what
  // `export default auth(...)` produces under the lazy config form) is green through typecheck,
  // lint and build, and 500s the entire app the moment a server boots.
  const handler = mod.default;
  assert.equal(
    typeof handler, "function",
    "src/proxy.ts's default export must BE a function, not a Promise of one -- Next 16's proxy " +
    "loader rejects anything else at request time, which no static check catches",
  );
  const call = (pathname, session) =>
    handler({ nextUrl: new URL(pathname, "https://insight.example"), auth: session });

  // 1. A private route with no session REDIRECTS -- the enforcement itself.
  const denied = await call("/", null);
  assert.equal(denied.kind, "redirect", "proxy.ts must redirect an unauthenticated request to a private route");
  const loginUrl = new URL(denied.location);
  assert.equal(loginUrl.pathname, "/login", `redirect must target /login, got ${loginUrl.pathname}`);
  assert.equal(
    loginUrl.searchParams.get("callbackUrl"), "/",
    "the redirect must carry the originally-requested path as callbackUrl",
  );

  // 2. A private route WITH a session passes through. Fails if a later edit drops the
  //    `!!req.auth` argument (which would redirect signed-in users into an infinite loop).
  assert.equal(
    (await call("/", { user: { name: "someone" } })).kind, "next",
    "proxy.ts must let an AUTHENTICATED request through to a private route",
  );

  // 3. The public login page passes through with no session. Fails if the branch is inverted,
  //    which would redirect /login to /login forever.
  assert.equal(
    (await call("/login", null)).kind, "next",
    "proxy.ts must let an unauthenticated request through to the public /login route",
  );

  console.log("OK: src/proxy.ts's own handler redirects unauthenticated private requests, and only those");
}

async function main() {
  partA();
  await partB();
  await partC();
}

main();
