// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 1. Compiles the real pythonBridge.ts and
// drives it against the REAL `python3 -m insight users verify` (Task 1) -- no browser, no next
// build. Two scenarios need no argon2-cffi at all (malformed-store -> exit 3, missing accounts
// file behaves the same as an empty store -> real InvalidCredentials via a genuine store lookup
// requires argon2 for the dummy-hash timing path, so that positive scenario is SKIPPED here if
// argon2 is absent -- mirroring insight/tests/test_cli_users.py's own gating discipline, just
// expressed in Node).
import assert from "node:assert/strict";
import { writeFileSync, mkdirSync, readFileSync, rmSync, chmodSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync, mkScratch } from "./lib/tsc-scratch.mjs";

/** Strips `//` line comments before a structural regex scan -- BOTH structural guards below need
 * this (the route-handler guard here, and Task 8's proxy.ts guards appended later in this same
 * file): each guarded file's own header comment QUOTES the literal forbidden code shape as part
 * of explaining why it's forbidden (route.ts: "DO NOT add `export const runtime = \"edge\";`";
 * proxy.ts: "DO NOT add an `export const runtime = ...`"), so a naive regex over the raw source
 * matches the file's OWN explanatory prose, not just real code -- found live during
 * implementation, not in the original plan (issue #307 [E18.S2], .sdlc/plans/307.md Task 6/8).
 * Deliberately simple (no awareness of `//` inside a string literal or block comments) --
 * sufficient for a same-repo textual guard against ONE specific forbidden statement shape, not a
 * general-purpose parser; every guarded file here only ever uses double-slash comments (this
 * repo's own convention, see insight/web/README.md's "Ordering" note). */
function stripLineComments(src) {
  return src
    .split("\n")
    .map((line) => line.replace(/\/\/.*$/, ""))
    .join("\n");
}

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true, types: ["node"],
    },
    include: ["*.ts"],
  };
}

function compileBridge(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  const src = path.join(WEB, "src", "lib", "auth", "pythonBridge.ts");
  writeFileSync(path.join(dir, "pythonBridge.ts"), readFileSync(src, "utf-8"));
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `pythonBridge.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "pythonBridge.js");
}

/** A tiny fake `python3` on PATH that always exits with `code` -- same shape as
 * insight/tests/test_verify_web.py::_stub_npm, just a shell script this time instead of a Python
 * fixture. Returns the directory it was written into, so the caller can prepend it to PATH and
 * remove the prepend in a `finally`. */
function stubPython3(exitCode, stdout = "") {
  const dir = mkScratch("insight-web-python-bridge-stub-");
  const script = path.join(dir, "python3");
  const emit = stdout ? `printf '%s' ${JSON.stringify(stdout)}\n` : "";
  writeFileSync(script, `#!/bin/sh\ncat >/dev/null\n${emit}exit ${exitCode}\n`);
  chmodSync(script, 0o755);
  return dir;
}

/** Runs `fn()` with a stub python3 shadowing the real one on PATH, restoring PATH afterward. */
async function withStubPython3(exitCode, stdout, fn) {
  const stubDir = stubPython3(exitCode, stdout);
  const originalPath = process.env.PATH;
  process.env.PATH = `${stubDir}${path.delimiter}${originalPath}`;
  try {
    return await fn();
  } finally {
    process.env.PATH = originalPath;
    rmSync(stubDir, { recursive: true, force: true });
  }
}

// Repo root, two ".." above insight/web/.
const REPO_ROOT = path.resolve(WEB, "..", "..");

async function main() {
  // runScenarioInWebAsync, not runScenarioAsync: this fixture imports node:child_process /
  // node:path, so tsc needs @types/node reachable via its ordinary upward node_modules walk --
  // only true when the scratch dir is rooted under insight/web/ itself (issue #307 [E18.S2],
  // .sdlc/plans/307.md Task 4 -- found live during implementation: a scratch dir under the OS
  // tmpdir has no node_modules ancestor at all, so tsc fails TS2688 "Cannot find type definition
  // file for 'node'" before ever reaching this fixture's own logic; see tsc-scratch.mjs's own
  // runScenarioInWebAsync docstring).
  const { verifyCredentials, CredentialCheckUnavailableError, InvalidCredentialsError } = await runScenarioInWebAsync(
    ".python-bridge-proof-scratch-",
    async (dir) => {
      const emitted = compileBridge(dir);
      return import(pathToFileURL(emitted).href);
    },
  );

  // Scenario 1: a corrupt store -> exit 3 -> CredentialCheckUnavailableError, never
  // InvalidCredentialsError. No argon2 needed (store.py detects this before any KDF call).
  const accountsDir = path.join(REPO_ROOT, ".sdlc-proof-scratch");
  mkdirSync(accountsDir, { recursive: true });
  const corruptPath = path.join(accountsDir, "corrupt-accounts.json");
  writeFileSync(corruptPath, "{not valid json");
  process.env.INSIGHT_ACCOUNTS_PATH = corruptPath;
  try {
    await assert.rejects(
      verifyCredentials("alice", "whatever"),
      (e) => e instanceof CredentialCheckUnavailableError,
      "corrupt store must reject with CredentialCheckUnavailableError, never InvalidCredentialsError",
    );
  } finally {
    delete process.env.INSIGHT_ACCOUNTS_PATH;
    rmSync(accountsDir, { recursive: true, force: true });
  }
  console.log("OK: corrupt accounts store surfaces as CredentialCheckUnavailableError, not InvalidCredentialsError");

  // Scenario 2: python3 replaced with a stub that always exits 2 (simulates KDF-unavailable) --
  // proves the exit-code SWITCH itself, independent of a real argon2-cffi install being present.
  // Same shape as insight/tests/test_verify_web.py::_stub_npm, just a shell script on PATH
  // instead of a Python fixture patched onto a module. Prepends the stub's directory to PATH for
  // this one spawnSync call only (pythonBridge.ts always invokes the literal name "python3", so
  // shadowing it on PATH is the only way to substitute a fake one without editing product code
  // for a test).
  await withStubPython3(2, "", async () => {
    await assert.rejects(
      verifyCredentials("alice", "whatever"),
      (e) =>
        e instanceof CredentialCheckUnavailableError &&
        /credential check could not run/.test(e.message),
      "exit 2 must reject with CredentialCheckUnavailableError",
    );
  });
  console.log("OK: a stub python3 exiting 2 surfaces as CredentialCheckUnavailableError");

  // Scenario 3: THE SILENT-LOCKOUT REGRESSION (independent security review of #307). Exit 1 is
  // CPython's status for ANY uncaught exception -- the ModuleNotFoundError from a wrong CWD above
  // all -- and insight/__main__.py's "not implemented yet" fallthrough returns 1 too. Before the
  // fix, the bridge mapped a bare exit 1 straight to InvalidCredentialsError, so a broken
  // interpreter told every user with a CORRECT password that it was wrong, hiding a total outage
  // behind a credentials message. The verdict must now be positively asserted on stdout by the
  // audited Python, never inferred from an exit status it shares with a crash.
  await withStubPython3(1, "", async () => {
    await assert.rejects(
      verifyCredentials("alice", "whatever"),
      (e) => e instanceof CredentialCheckUnavailableError,
      "exit 1 WITHOUT the invalid-credentials marker is a crash, not a wrong password: it must " +
      "reject with CredentialCheckUnavailableError",
    );
  });
  console.log("OK: a bare exit 1 (no stdout marker) is an operator failure, NOT invalid credentials");

  // ...and the same exit code WITH the marker still means exactly what it always did, so the fix
  // above did not simply break the real credential path.
  await withStubPython3(1, JSON.stringify({ error: "invalid_credentials" }), async () => {
    await assert.rejects(
      verifyCredentials("alice", "whatever"),
      (e) => e instanceof InvalidCredentialsError && e.message === "invalid username or password",
      "exit 1 WITH the marker must still be InvalidCredentialsError carrying the one generic message",
    );
  });
  console.log("OK: exit 1 carrying the invalid_credentials marker is still InvalidCredentialsError");

  // Scenario 4: the real `python3 -m insight users verify` emits that marker -- the two halves of
  // the contract are pinned by insight/tests/test_cli_users.py on the Python side and by the
  // stub-driven scenarios above on the Node side, but nothing yet proves they AGREE. This does,
  // end to end, with no argon2 needed: an empty store makes any lookup a genuine
  // InvalidCredentials via store.py's own unknown-user path... which pays a dummy-hash KDF, so it
  // IS argon2-dependent. Skipped rather than faked when argon2-cffi is absent, mirroring
  // insight/tests/test_cli_users.py's own gating discipline (and this file's header note).
  const emptyDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-empty");
  mkdirSync(emptyDir, { recursive: true });
  const emptyPath = path.join(emptyDir, "accounts.json");
  writeFileSync(emptyPath, JSON.stringify({ version: 1, users: {} }));
  process.env.INSIGHT_ACCOUNTS_PATH = emptyPath;
  try {
    await verifyCredentials("nobody", "whatever");
    assert.fail("expected a rejection for an unknown user");
  } catch (e) {
    if (
      e instanceof CredentialCheckUnavailableError &&
      /credential check could not run/.test(e.message)
    ) {
      console.log("SKIP: argon2-cffi absent, so the real CLI cannot reach its invalid-credentials path");
    } else {
      assert.ok(
        e instanceof InvalidCredentialsError,
        `the REAL cli must reject an unknown user as InvalidCredentialsError (proving it emits the ` +
        `marker the bridge now requires), got ${e.constructor.name}: ${e.message}`,
      );
      console.log("OK: the real `insight users verify` emits the marker the bridge requires (end to end)");
    }
  } finally {
    delete process.env.INSIGHT_ACCOUNTS_PATH;
    rmSync(emptyDir, { recursive: true, force: true });
  }

  // Decision 6's structural guard for the ORDINARY route-runtime opt-in (distinct from proxy.ts's
  // own rule -- Task 8 adds that one once proxy.ts exists). Textual scan, mirroring
  // tests/test_licence_boundary.py's own established style (repo root, not insight/tests/) -- no
  // AST needed for a single forbidden literal.
  const routeSrc = readFileSync(
    path.join(WEB, "src", "app", "api", "auth", "[...nextauth]", "route.ts"),
    "utf-8",
  );
  // stripLineComments() first -- route.ts's OWN header comment quotes the literal forbidden code
  // ("DO NOT add `export const runtime = \"edge\";`") to explain why it's forbidden, so scanning
  // the raw source (comments included) makes this guard fail against the file's own
  // documentation, not real code (found live during implementation -- see stripLineComments()'s
  // own docstring above).
  assert.ok(
    !/export\s+const\s+runtime\s*=\s*["']edge["']/.test(stripLineComments(routeSrc)),
    "app/api/auth/[...nextauth]/route.ts must never opt into the Edge runtime -- " +
    "the Credentials provider's authorize() needs child_process (Decision 6)",
  );
  console.log("OK: the NextAuth route handler stays on the Node runtime (no Edge opt-in)");

  // Decision 6's proxy-file guards, added once proxy.ts exists (issue #307 [E18.S2],
  // .sdlc/plans/307.md, plan-review BLOCKING 1). Unlike the route handler's guard above (which
  // checks for a VALID-but-dangerous opt-in), both of these check for states next@16.3.0 itself
  // refuses to build at all -- E1031 and E900 respectively (verified against its own dist) -- so
  // these two exist to fail with THIS proof's clearer message before a contributor has to
  // decode a Next.js build error to learn the same thing. stripLineComments() first, same reason
  // as the route-handler guard above -- proxy.ts's own header comment quotes the forbidden
  // `export const runtime` shape in prose to explain why it's forbidden.
  const proxySrc = stripLineComments(readFileSync(path.join(WEB, "src", "proxy.ts"), "utf-8"));
  assert.ok(
    !/export\s+const\s+runtime\s*=/.test(proxySrc),
    "src/proxy.ts must not export `runtime` at all -- Next.js throws E1031 for a proxy file " +
    "that does (\"Proxy always runs on Node.js runtime\"); this file already runs on Node " +
    "unconditionally, so there is nothing to configure",
  );
  console.log("OK: src/proxy.ts exports no runtime override (would be a hard E1031 build error)");

  assert.ok(
    !existsSync(path.join(WEB, "src", "middleware.ts")),
    "src/middleware.ts must not exist alongside src/proxy.ts -- Next.js throws E900 " +
    "(\"Both middleware file ... and proxy file ... are detected\") if both are present",
  );
  console.log("OK: no stray src/middleware.ts alongside src/proxy.ts (would be a hard E900 build error)");
}

main();
