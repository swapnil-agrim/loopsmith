// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Tasks 4. Spec §5.2 rule 1: "the actor is resolved from
// the session and nowhere else." No server, no browser -- this one joins the always-on
// `npm run test` chain, the same split prove-role-route-matrix.mjs (offline) and
// prove-role-forbidden-real-server.mjs (CI-only) already make for the role axis.
//
//   PART A -- compiles the real src/lib/auth/actor.ts with the local tsc, dynamic-imports it, and
//   table-tests resolveActor() directly: every fail-closed input, and the one identity input.
//
//   PART A2 -- the COMPILE-TIME half of the invariant, and the one that actually matters. Decision
//   2 says the signature IS the enforcement: resolveActor takes a Session and nothing else, so a
//   query string / header / body value is not merely ignored, it is unpassable. Asserted by
//   compiling call sites that pass exactly those shapes and requiring tsc to REJECT them. Without
//   this, "the parameter is never consulted" would rest on Part B's string scan alone -- and a
//   string scan is defeated by renaming an identifier, while a type error is not.
//
//   PART B -- the source scan, the breadth guard ("not sampling", the property #307 done-when 3
//   established for the public/private axis). Walks the REAL src/ tree, so a route added by a
//   future story is covered automatically rather than needing a maintained list. Two invariants:
//   (1) no file under src/ names an actor at all, except actor.ts itself and the exact token
//   `resolveActor` at a call site; (2) no route file under src/app/ic/ touches ANY request-derived
//   input -- not searchParams/params, not their client hooks, not headers()/cookies() nor the
//   .headers/.cookies property forms.
//
//   PART C -- the negative control. Part B's own scanner, run over
//   fixtures/actor-from-search-params.tsx.fixture, MUST fail. Without this, Part B passing would
//   prove nothing: a scanner with a broken regex passes against every tree there is. Same
//   methodology insight/tests/test_dash_ic_no_leak.py and test_dash_leadership_guardrail.py use.
//
// HONEST LIMIT (plan-review, .sdlc/plans/310.md Risk 1). Part B is a string scan. It is defeated by
// a computed property name, and -- the sharper shape -- by RENAMING: a future `getPrincipal()` that
// calls resolveActor(session) (allowed at any call site) and then applies a request-derived
// override contains no `actor` token to catch. Part A2 is the backstop for that, and Part C proves
// falsifiability against one canonical shape (searchParams.actor), not against renaming. Part B is
// breadth, not proof.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync } from "./lib/tsc-scratch.mjs";

const SRC = path.join(WEB, "src");
const ACTOR_TS = path.join(SRC, "lib", "auth", "actor.ts");

// --------------------------------------------------------------------------------------- part A

// runScenarioInWebAsync, not runScenarioAsync: actor.ts does `import type { Session } from
// "next-auth"`, so tsc's ordinary upward node_modules walk has to be able to reach the real
// next-auth package -- which only works rooted under insight/web/. Same reason
// prove-secure-cookie-flag-is-tls-conditional.mjs needs it for @types/node. The scratch prefix is
// covered by an insight/web/.gitignore entry (mkScratchInWeb()'s own documented requirement).
const SCRATCH_PREFIX = ".actor-proof-scratch-";

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

function writeScratch(dir, extraFiles = {}) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  writeFileSync(path.join(dir, "actor.ts"), readFileSync(ACTOR_TS, "utf-8"));
  for (const [name, body] of Object.entries(extraFiles)) {
    writeFileSync(path.join(dir, name), body);
  }
}

async function partA() {
  const { resolveActor } = await runScenarioInWebAsync(SCRATCH_PREFIX, async (dir) => {
    writeScratch(dir);
    const { ok, output } = runTsc(dir);
    assert.ok(ok, `src/lib/auth/actor.ts must compile clean with the local tsc:\n${output}`);
    return import(pathToFileURL(path.join(dir, "out", "actor.js")).href);
  });

  const cases = [
    // Fails closed, every shape, never a placeholder identity (Decision 3).
    { name: "no session at all", input: null, want: null },
    { name: "undefined session", input: undefined, want: null },
    { name: "session with no user", input: {}, want: null },
    { name: "user with no name", input: { user: {} }, want: null },
    { name: "name: null (Auth.js's own DefaultSession allows it)", input: { user: { name: null } }, want: null },
    { name: "name: undefined", input: { user: { name: undefined } }, want: null },
    { name: "empty-string name", input: { user: { name: "" } }, want: null },
    { name: "whitespace-only name", input: { user: { name: "   " } }, want: null },
    { name: "non-string name", input: { user: { name: 42 } }, want: null },
    // The one identity input.
    { name: "a real username", input: { user: { name: "alice" } }, want: "alice" },
    { name: "a username with surrounding whitespace", input: { user: { name: " alice " } }, want: "alice" },
  ];

  for (const { name, input, want } of cases) {
    assert.doesNotThrow(() => resolveActor(input), `resolveActor must never throw -- ${name}`);
    assert.equal(
      resolveActor(input), want,
      `resolveActor(${name}) must be ${JSON.stringify(want)}, got ${JSON.stringify(resolveActor(input))}`,
    );
  }

  console.log(`OK: resolveActor() fails closed on ${cases.length - 2} non-identity inputs and never throws`);
}

// -------------------------------------------------------------------------------------- part A2
//
// Decision 2, asserted rather than asserted-about: each snippet below is a call site that would let
// a request value become the actor, and tsc MUST reject every one. A `// @ts-expect-error` would
// NOT do here -- that proves an error exists at a line, but a future signature widened to
// `resolveActor(session: Session | null, override?: string)` would keep the same snippets erroring
// for a DIFFERENT reason (or stop erroring entirely) with nothing to notice. Compiling each in
// isolation and requiring a failure is what pins the arity and the parameter type together.

const REJECTED_CALL_SITES = {
  "a query-string value": `import { resolveActor } from "./actor";
const searchParams = new Map<string, string>();
export const a = resolveActor(searchParams.get("actor") ?? null);`,

  "a header value": `import { resolveActor } from "./actor";
declare const headers: { get(name: string): string | null };
export const a = resolveActor(headers.get("x-actor"));`,

  "a request body object": `import { resolveActor } from "./actor";
declare const body: { actor: string };
export const a = resolveActor(body);`,

  "a second override argument": `import type { Session } from "next-auth";
import { resolveActor } from "./actor";
declare const session: Session;
// @ts-expect-error is deliberately NOT used -- see this part's header comment.
export const a = (resolveActor as (s: Session | null, override: string) => string | null)(session, "carol");
const _unused: string = a as unknown as string;
export const b = resolveActor(session, "carol");`,
};

async function partA2() {
  for (const [name, source] of Object.entries(REJECTED_CALL_SITES)) {
    // eslint-disable-next-line no-await-in-loop -- each scenario needs its own scratch dir, and
    // running four tsc invocations sequentially is the point (a shared dir would let one snippet's
    // error mask another's).
    const { ok, output } = await runScenarioInWebAsync(SCRATCH_PREFIX, async (dir) => {
      writeScratch(dir, { "callsite.ts": source });
      return runTsc(dir);
    });
    assert.ok(
      !ok,
      `resolveActor() must NOT accept ${name} -- tsc compiled it clean, which means the ` +
        `session-only signature (Decision 2) has been widened and a request value can now become ` +
        `the actor. Compiler output:\n${output}`,
    );
  }
  console.log(`OK: tsc rejects all ${Object.keys(REJECTED_CALL_SITES).length} request-derived call sites -- resolveActor() takes a session and nothing else`);
}

// --------------------------------------------------------------------------------------- part B

const SOURCE_FILE_RE = /\.(ts|tsx)$/;
const ROUTE_FILE_RE = /^(page|route|layout)\.(ts|tsx)$/;

/** Every .ts/.tsx under `dir`, recursively. Reads the REAL tree -- a file added by a future story
 * is scanned automatically, never by a list someone has to remember to update. */
function walkSources(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...walkSources(full));
    } else if (SOURCE_FILE_RE.test(entry)) {
      out.push(full);
    }
  }
  return out.sort();
}

/** Strips comments and string/template literal BODIES, so prose explaining the invariant (this
 * repo comments heavily) and a legitimate `data-testid="ic-actor"` never read as violations. The
 * quotes are kept so the result still tokenizes; only what is between them is blanked. Order
 * matters: comments first, since a comment can contain an apostrophe that would otherwise open a
 * phantom string. */
export function stripCommentsAndStrings(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/`(?:[^`\\]|\\.)*`/g, "``");
}

/** Everything that may produce an actor identity, and the one token allowed to name it elsewhere.
 * `resolveActor` is blanked before the `actor` scan runs, so a call site is allowed but a bare
 * `actor` binding anywhere else is not. */
const RESOLVER_TOKEN = /\bresolveActor\b/g;
const ACTOR_TOKEN = /\bactor\b/gi;

/** Request-derived inputs. Both the next/headers CALL form and the property form (`req.headers`,
 * `request.cookies` -- the natural Route Handler shape), and both the server prop and the client
 * hook for search params and dynamic segments. A word-boundary scan for `searchParams` alone would
 * never match inside `useSearchParams` (there is no non-word character between `use` and
 * `Search`), which is exactly the miss plan-review found. */
const REQUEST_INPUTS = [
  /\bsearchParams\b/, /\buseSearchParams\b/, /\bparams\b/, /\buseParams\b/,
  /\bheaders\s*\(/, /\bcookies\s*\(/, /\.headers\b/, /\.cookies\b/,
  /\brequest\b/, /\breq\b/,
];

/** Returns a list of violation strings; empty means clean. Exported in spirit for Part C, which
 * runs this exact function over a fixture that must produce a non-empty list. */
function scanForActorSources(files, { allowActorIn = [] } = {}) {
  const violations = [];
  for (const file of files) {
    const rel = path.relative(WEB, file);
    if (allowActorIn.includes(file)) continue;
    const code = stripCommentsAndStrings(readFileSync(file, "utf-8")).replace(RESOLVER_TOKEN, " ");
    const hits = code.match(ACTOR_TOKEN);
    if (hits) {
      violations.push(
        `${rel}: names an actor (${[...new Set(hits)].join(", ")}) outside src/lib/auth/actor.ts. ` +
          `The ONLY way to obtain an actor identity is resolveActor(session).`,
      );
    }
  }
  return violations;
}

function scanForRequestInputs(files) {
  const violations = [];
  for (const file of files) {
    const code = stripCommentsAndStrings(readFileSync(file, "utf-8"));
    for (const pattern of REQUEST_INPUTS) {
      if (pattern.test(code)) {
        violations.push(
          `${path.relative(WEB, file)}: touches a request-derived input (${pattern}). An actor-scoped ` +
            `route must take its identity from the session alone (spec §5.2 rule 1).`,
        );
      }
    }
  }
  return violations;
}

function partB() {
  const all = walkSources(SRC);
  assert.ok(all.length > 5, `the source walk found only ${all.length} files -- it is not scanning the real tree`);
  assert.ok(all.includes(ACTOR_TS), "the source walk must reach src/lib/auth/actor.ts");

  const actorViolations = scanForActorSources(all, { allowActorIn: [ACTOR_TS] });
  assert.deepEqual(
    actorViolations, [],
    `no file under src/ may name an actor except src/lib/auth/actor.ts itself:\n  ${actorViolations.join("\n  ")}`,
  );

  const icDir = path.join(SRC, "app", "ic");
  const icRoutes = walkSources(icDir).filter((f) => ROUTE_FILE_RE.test(path.basename(f)));
  assert.ok(icRoutes.length > 0, `no route file found under ${path.relative(WEB, icDir)} -- this proof would assert nothing`);

  const requestViolations = scanForRequestInputs(icRoutes);
  assert.deepEqual(
    requestViolations, [],
    `no route under src/app/ic/ may touch a request-derived input:\n  ${requestViolations.join("\n  ")}`,
  );

  console.log(`OK: ${all.length} source files scanned -- only actor.ts names an actor, and none of the ${icRoutes.length} /ic route file(s) touches a request-derived input`);
}

// --------------------------------------------------------------------------------------- part C

const LEAKY_FIXTURE = path.join(WEB, "fixtures", "actor-from-search-params.tsx.fixture");

function partC() {
  // Both halves of Part B must fire on the fixture: it names an actor outside actor.ts AND reads
  // it from searchParams. If either scan returned clean here, the corresponding Part B assertion
  // above would be a tautology.
  const actorViolations = scanForActorSources([LEAKY_FIXTURE], { allowActorIn: [] });
  assert.ok(
    actorViolations.length > 0,
    "NEGATIVE CONTROL FAILED: the actor scan found nothing in " +
      `${path.relative(WEB, LEAKY_FIXTURE)}, which takes its actor straight from ?actor=. Part B ` +
      "therefore proves nothing about the real tree either.",
  );

  const requestViolations = scanForRequestInputs([LEAKY_FIXTURE]);
  assert.ok(
    requestViolations.length > 0,
    "NEGATIVE CONTROL FAILED: the request-input scan found nothing in " +
      `${path.relative(WEB, LEAKY_FIXTURE)}, which declares a searchParams prop.`,
  );

  // And the stripper must not be so aggressive that it blanks real code: a file that is nothing
  // but comments and strings would pass every scan above vacuously.
  const stripped = stripCommentsAndStrings(readFileSync(LEAKY_FIXTURE, "utf-8"));
  assert.match(stripped, /export default/, "the comment/string stripper blanked real code");

  console.log("OK: negative control -- the leaky fixture trips both halves of Part B's scan");
}

async function main() {
  await partA();
  await partA2();
  partB();
  partC();
}

main();
