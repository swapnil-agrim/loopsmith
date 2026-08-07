// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 1/6. This file must only ever be imported
// from auth.ts's authorize() callback, reached through app/api/auth/[...nextauth]/route.ts (a
// Route Handler, Node runtime by default). NEVER import this from proxy.ts. Not because proxy.ts
// lacks child_process -- Next 16's proxy.ts convention runs on the Node.js runtime unconditionally
// (verified against next@16.3.0's own dist -- see Decision 6) -- but because proxy.ts runs on
// EVERY matched request and this file's verifyCredentials() costs a process spawn plus a ~23ms
// argon2id KDF: acceptable once, on a login POST, not on every navigation.
import { spawn } from "node:child_process";
import path from "node:path";

/** Exit 1 CARRYING THE `invalid_credentials` STDOUT MARKER: the ONE message
 * store.InvalidCredentials already carries for wrong-password/unknown-user/corrupt-single-record
 * -- safe to show the end user verbatim.
 *
 * The marker is load-bearing, not decoration (independent security review of #307). A bare exit 1
 * is NOT evidence of a wrong password: CPython exits 1 for ANY uncaught exception -- including the
 * ModuleNotFoundError you get whenever `insight` is not importable from the child's CWD, which
 * this file's own REPO_ROOT note admits is fragile -- and insight/__main__.py's
 * "not implemented yet" fallthrough returns 1 too. Reading those as InvalidCredentials would
 * answer "invalid username or password" to every user with a CORRECT password, masking a total
 * outage as a credentials problem: exactly the silent lockout this class pair exists to prevent,
 * just reached through a wider door than the KDF-missing case. So the verdict must be positively
 * asserted by the audited Python, never inferred from an exit status it shares with a crash. */
export class InvalidCredentialsError extends Error {}

/** Every other failure mode: the credential check could not run (exit 2), the WHOLE store
 * corrupt (exit 3),
 * malformed stdin (exit 4), python3 missing (spawn ENOENT), or an unrecognized exit code. NEVER
 * caught and re-shown as "invalid credentials" -- that would silently lock out every user behind
 * a misleading message (Decision 1's whole point). */
export class CredentialCheckUnavailableError extends Error {}

// issue #307 [E18.S2], .sdlc/plans/307.md Decision 1's CWD discussion -- two SEPARATE uses of
// this same computed path, with different stakes:
//   1. accountsPath()'s fallback below: local-dev convenience ONLY. Any real deployment MUST set
//      INSIGHT_ACCOUNTS_PATH explicitly (see insight/web/README.md); accountsPath() never
//      assumes this guess is correct, because --accounts-path is always passed explicitly either
//      way (see verifyCredentials() below).
//   2. `cwd` on the spawnSync call below: genuinely load-bearing everywhere this story runs
//      (no environment here does a real `pip install` of `insight` -- see the note after Task 1
//      recording insight/Dockerfile.web's gap). `python3 -m insight` needs `insight`
//      importable, which -- absent a real install -- needs the repo root on sys.path, exactly
//      the way `python3 -m pytest -q insight/tests/` already relies on being run from the repo
//      root today. Assumes this file's own location (insight/web/src/lib/auth/, four directories
//      below the repo root) and that the Node process itself was launched with CWD ==
//      insight/web/ (true for `npm run dev`/`npm run test`/`npm run build`, all invoked from
//      insight/web/ -- see insight/verify_web.py's own `_npm()` helper, `cwd=str(WEB)`).
const REPO_ROOT = path.resolve(process.cwd(), "..", "..");

function accountsPath(): string {
  return (
    process.env.INSIGHT_ACCOUNTS_PATH ??
    path.join(REPO_ROOT, ".sdlc", "insight-accounts.json")
  );
}

/** ASYNC, and deliberately not spawnSync (independent code review of #307). This runs inside the
 * Credentials provider's authorize() on a Node-runtime route handler, and spawnSync blocks Node's
 * single event loop for the WHOLE child lifetime -- interpreter startup plus the `insight` import
 * plus the ~23ms argon2id KDF, i.e. hundreds of ms during which the server serves nobody: not the
 * dashboard, not static assets, not other users' sessions. On an unauthenticated endpoint with no
 * rate limit that is also a free availability lever for anyone who can POST the login form.
 * Awaiting a spawn() keeps Decision 1's "reuse the audited Python" intact and costs only this
 * wrapper. */
export function verifyCredentials(
  username: string,
  password: string,
): Promise<{ role: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "insight", "users", "verify", "--accounts-path", accountsPath()],
      { cwd: REPO_ROOT },
    );

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf-8");
    child.stderr.setEncoding("utf-8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });

    child.on("error", (err) => {
      // Mirrors insight/verify_web.py's own _npm() ENOENT handling: name the cause, never a raw
      // exception, and never treat "the tool is missing" as "credentials were wrong."
      reject(
        new CredentialCheckUnavailableError(
          `python3 was not found or failed to start: ${err.message}`,
        ),
      );
    });

    child.on("close", (status) => {
      switch (status) {
        case 0:
          try {
            const parsed = JSON.parse(stdout);
            if (typeof parsed.role !== "string") throw new Error("no 'role' string in response");
            resolve({ role: parsed.role });
          } catch (e) {
            reject(
              new CredentialCheckUnavailableError(
                `insight users verify exited 0 but printed an unparseable response: ${e}`,
              ),
            );
          }
          return;
        case 1: {
          // Exit 1 is necessary but NOT sufficient -- see InvalidCredentialsError's docstring.
          // The audited Python must also have positively printed its verdict; anything else that
          // merely happens to exit 1 (an uncaught exception, a wrong CWD, the CLI's
          // "not implemented yet" fallthrough) is an OPERATOR failure and must say so.
          let marked = false;
          try {
            marked = JSON.parse(stdout)?.error === "invalid_credentials";
          } catch {
            marked = false;
          }
          reject(
            marked
              ? new InvalidCredentialsError("invalid username or password")
              : new CredentialCheckUnavailableError(
                  "insight users verify exited 1 without its invalid-credentials marker, so the " +
                    "interpreter almost certainly failed before ever checking the password " +
                    `(is \`insight\` importable from ${REPO_ROOT}?): ${stderr}`,
                ),
          );
          return;
        }
        case 2:
          // Exit 2 carries TWO causes, so this text must not name either one (issue #308
          // [E18.S3], PR #485 code review). insight/__main__.py returns it for
          // hashing.KDFUnavailableError AND -- since #308 -- for
          // store.AccountsLockUnavailableError, deliberately reusing the code rather than
          // minting a new one, because prove-python-bridge-exit-codes.mjs pins a small closed
          // set. Hardcoding "KDF unavailable" here labelled a lock-contention timeout as an
          // argon2 install problem, sending an operator after the wrong fault. The Python
          // side's own stderr, appended below, says which one it actually was.
          reject(
            new CredentialCheckUnavailableError(`credential check could not run: ${stderr}`),
          );
          return;
        case 3:
          reject(new CredentialCheckUnavailableError(`accounts store corrupt: ${stderr}`));
          return;
        case 4:
          reject(new CredentialCheckUnavailableError(`malformed bridge request: ${stderr}`));
          return;
        default:
          reject(
            new CredentialCheckUnavailableError(
              `insight users verify exited ${status}: ${stderr}`,
            ),
          );
      }
    });

    // EPIPE if the child died before reading stdin (a crashing interpreter, say). Swallow it here
    // -- the 'close' handler above reports the REAL cause; an unhandled 'error' on this stream
    // would crash the server with a less useful one.
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ username, password }));
  });
}
