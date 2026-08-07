// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md. Shared "compile a scratch dir with the LOCAL tsc"
// machinery, factored out of prove-metric-contract-safety.mjs (issue #301) so
// prove-metric-view-behavior.mjs can use the exact same pattern instead of Node's unflagged
// TypeScript type-stripping (unsafe below Node 22.18.0 -- see prove-metric-view-behavior.mjs's
// own header for the failure this replaced). Both callers stay inside insight/web/ -- insight/
// must stay extractable to its own repo, so nothing here may reach outside it.
//
// `node_modules/.bin/tsc` directly, never `npx` -- see generate-schema.mjs's docstring for why
// (npx can still hit the registry for a version check even when the package is installed).
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

// This file lives at scripts/lib/tsc-scratch.mjs -- three ".." from here (lib/ -> scripts/ ->
// web/) reaches insight/web/, one more than generate-schema.mjs's own two ".." (it lives directly
// under scripts/).
export const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..", "..");
export const TSC_BIN = path.join(WEB, "node_modules", ".bin", "tsc");

/** Runs `tsc -p dir`, returning { ok, output } -- never throws on a nonzero exit. */
export function runTsc(dir) {
  try {
    const output = execFileSync(TSC_BIN, ["-p", dir], { encoding: "utf-8" });
    return { ok: true, output };
  } catch (err) {
    // tsc writes diagnostics to stdout and exits nonzero; execFileSync throws with both
    // captured on the error object.
    return { ok: false, output: (err.stdout || "") + (err.stderr || "") };
  }
}

/** A scratch dir under the OS tmpdir, outside the repo tree entirely -- nothing to gitignore,
 * nothing that can leak into `git status`. Not suitable for a scenario that needs TypeScript's
 * ordinary upward node_modules walk to resolve a real package (e.g. @types/react); see
 * mkScratchInWeb() below for that case. */
export function mkScratch(prefix) {
  return mkdtempSync(path.join(tmpdir(), prefix));
}

/** Same contract as mkScratch(), rooted under insight/web/ itself instead of the OS tmpdir: lets
 * tsc's ordinary upward node_modules walk find real packages under insight/web/node_modules
 * (e.g. @types/react for a .tsx fixture) exactly as the real src/**\/*.tsx files already do
 * under the real tsconfig.json. The caller's `prefix` MUST be covered by an
 * insight/web/.gitignore entry -- this helper does not add one. */
export function mkScratchInWeb(prefix) {
  return mkdtempSync(path.join(WEB, prefix));
}

/** Runs `fn(dir)` against a fresh mkScratch(prefix) dir, always tearing it down afterward (even
 * on throw) -- a `finally` block, not a correctness guard the tests depend on. */
export function runScenario(prefix, fn) {
  const dir = mkScratch(prefix);
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Same as runScenario(), but backed by mkScratchInWeb(prefix) -- see that function's docstring
 * for when this is the one to use. */
export function runScenarioInWeb(prefix, fn) {
  const dir = mkScratchInWeb(prefix);
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Async sibling of runScenario(): AWAITS `fn(dir)` before the `finally` cleanup runs. Needed for
 * any scenario that `import()`s something out of the scratch dir -- unlike the synchronous
 * scenarios above (compile with `tsc`, read `execFileSync`'s captured output, done), a dynamic
 * `import()` is asynchronous, so the plain `try { return fn(dir); } finally { rmSync(...) }`
 * shape would delete the dir out from under a still-pending read before Node finishes loading the
 * module -- a real race, not a hypothetical one. */
export async function runScenarioAsync(prefix, fn) {
  const dir = mkScratch(prefix);
  try {
    return await fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Async sibling of runScenarioInWeb() -- same "await before cleanup" reasoning as
 * runScenarioAsync() above, but rooted under insight/web/ (mkScratchInWeb()) instead of the OS
 * tmpdir, for a scenario that BOTH dynamic-`import()`s its compiled output AND needs tsc's
 * ordinary upward node_modules walk to resolve a real package -- e.g. `@types/node` for a
 * fixture that imports a `node:`-prefixed built-in (issue #307 [E18.S2],
 * .sdlc/plans/307.md Task 4: pythonBridge.ts imports `node:child_process`/`node:path`, and a
 * scratch dir under the OS tmpdir has no node_modules ancestor to find `@types/node` in at all --
 * `tsc` fails with TS2688 "Cannot find type definition file for 'node'" there, exactly the
 * failure mkScratchInWeb()'s own docstring already names for `@types/react`). The caller's
 * `prefix` MUST be covered by an insight/web/.gitignore entry, same requirement as
 * mkScratchInWeb() itself. */
export async function runScenarioInWebAsync(prefix, fn) {
  const dir = mkScratchInWeb(prefix);
  try {
    return await fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
