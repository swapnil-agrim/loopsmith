// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #308 [E18.S3], .sdlc/plans/308.md Decision 2 (AMENDED after plan-review BLOCKED the
// original design -- read this header before touching anything below).
//
// WHAT THIS FILE IS. One integer "epoch" per username, persisted to a small JSON file
// (`.sdlc/insight-web-sessions.json` by default, `{"version": 1, "epochs": {"<username>": <int>}}`
// -- overridable via INSIGHT_SESSIONS_PATH, mirroring INSIGHT_ACCOUNTS_PATH's own env-var-override
// convention, pythonBridge.ts:48-55). `auth.ts` stamps the current epoch onto a JWT at sign-in and
// compares it on every later read; `events.signOut` bumps the epoch, which is what makes a
// replayed pre-sign-out cookie fail the comparison (Decision 1).
//
// WHY THIS FILE READS FROM DISK ON EVERY CALL, WITH NO IN-MEMORY CACHE -- THE AMENDMENT. The
// original design cached the epoch in a module-scope Map, loaded once at init, justified by
// auth.ts's own `let warnedUntrustedHost` as precedent that module state survives across requests.
// Plan-review showed that precedent does not transfer: the epoch is WRITTEN by the sign-out App
// Router entry (app/api/auth/[...nextauth]/route.ts) and READ by proxy.ts, and Next's standalone
// server loads the two through SEPARATE, independently compiled artifacts -- proxy.ts via its own
// dedicated `require()` (next-server.js:1082) of a bundle the route handler's code is never
// inlined into. So a load-once Map would give the two sides TWO DISTINCT module instances, each
// with its own Map, and `bumpEpoch()` on one side would never be visible to `getEpoch()` on the
// other -- the replayed cookie would keep validating until the process restarts, which is exactly
// the failure done-when 1 (a logged-out session must be refused) forbids. The `warnedUntrustedHost`
// precedent only ever proved state surviving across requests WITHIN one bundle; it says nothing
// about two bundles agreeing, and nothing here tests or notices that gap if it silently reopens.
//
// So: no cache. `getEpoch()` and `bumpEpoch()` both read the file fresh, every call. There is no
// cross-bundle assumption left to be wrong about. The cost is one small-file read per guarded
// request, which is proportionate -- proxy.ts already decrypts a JWT (Web Crypto) on every
// request, more expensive than a readFileSync of a few hundred bytes from page cache, and nowhere
// near the process-spawn-plus-KDF cost pythonBridge.ts's own header explains proxy.ts was built to
// avoid.
//
// ponytail: reads the epoch file per request -- correct and cache-free. If a profile ever shows
// this read mattering, add an mtime/statSync staleness check (still cross-bundle-safe, unlike a
// plain cache); do NOT reintroduce a load-once Map.
//
// WRITE SERIALIZATION, NOT AN OS LOCK. Unlike store.py's fcntl.flock (Decision 4 -- a different
// problem: multiple concurrent OS PROCESSES racing one file), bumpEpoch() only ever runs inside
// one Node process, which is single-threaded per process; an in-process promise chain (below)
// serializes concurrent async writers so two overlapping bumpEpoch() calls never race each other's
// read-modify-write, with no OS-level lock needed. Writes persist atomically via a same-directory
// temp-file-plus-rename, the same shape store.py's `_write_accounts_data` already uses, ported to
// Node (`node:fs.renameSync` on POSIX is `rename()`, non-dereferencing the same way `os.replace()`
// is on the Python side).
//
// OPEN SCOPE LIMITATION, NAMED NOT SOLVED (Decision 2, widened by both reviewers of #308 --
// the original note here understated it, naming only the HOST-level gap). A per-request disk
// READ keeps several processes on ONE host coherent (they all read the same file from the OS
// page cache) -- but `writeQueue` above only ever serializes WRITERS *within a single Node
// process*: it is an in-process Promise chain, not an OS-level lock (unlike store.py's
// `fcntl.flock`, Decision 4, which is keyed on the file itself and therefore correctly
// cross-process). So the gap is not only "multiple HOSTS with separate filesystems," as this note
// used to say -- it is also multiple Node PROCESSES sharing the SAME filesystem (e.g. a
// clustered/multi-worker deployment of this same container, or `next start` run twice against
// the same `.sdlc/`): two such processes can each read the same pre-write `epochs` object,
// independently compute their own `nextEpoch`, and the second `renameSync` silently clobbers
// whatever the first one just wrote -- the exact same read-modify-write race store.py's
// `_locked_accounts` (Decision 4) exists to close on the Python side, just unclosed here. A lost
// bump is not a cosmetic miss: it means a signed-out token's epoch never actually moved, so that
// token keeps validating past the sign-out that was supposed to revoke it -- precisely the
// failure done-when 1 forbids, reintroduced by a race instead of a swallowed exception. NOT
// reachable in today's single-process `insight/Dockerfile.web` (which starts exactly one `next
// start` process) -- flagged for the day a clustered deployment is considered, not blocking now.
import { randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

/** On-disk shape of the sessions file. Kept minimal on purpose -- Decision 2 names exactly this
 * shape, no more. */
interface SessionsFile {
  version: 1;
  epochs: Record<string, number>;
}

const EMPTY_SESSIONS_FILE: SessionsFile = { version: 1, epochs: {} };

// Same file, same directory depth as pythonBridge.ts's own REPO_ROOT (insight/web/src/lib/auth/,
// four directories below the repo root) -- issue #308 [E18.S3], .sdlc/plans/308.md Task 4.
const REPO_ROOT = path.resolve(process.cwd(), "..", "..");

function sessionsPath(): string {
  return (
    process.env.INSIGHT_SESSIONS_PATH ??
    path.join(REPO_ROOT, ".sdlc", "insight-web-sessions.json")
  );
}

function isShapeValid(value: unknown): value is SessionsFile {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (candidate.version !== 1) return false;
  if (typeof candidate.epochs !== "object" || candidate.epochs === null) return false;
  return Object.values(candidate.epochs as Record<string, unknown>).every(
    (v) => typeof v === "number",
  );
}

/** Reads the sessions file fresh from disk -- see this file's own header for why there is no
 * cache. A missing file (never bumped yet) reads as empty, not an error -- that is the ordinary
 * "no logout has ever happened" state, mirroring `_read_accounts_data`'s own FileNotFoundError
 * handling on the Python side. Any OTHER read failure (corrupt JSON, wrong shape, permission
 * denied) is NOT swallowed to empty -- that would silently make a corrupt store read as "epoch 0
 * for everyone," which is the fail-OPEN direction this file exists to avoid (a revoked token
 * stamped with a non-zero epoch would then compare equal to a corrupt-read default of 0 and keep
 * validating). Failing loudly here surfaces as an error out of the `jwt` callback in auth.ts,
 * which Auth.js treats as a hard failure rather than a silently-accepted session -- fail closed,
 * not fail open. */
function readSessionsFile(): SessionsFile {
  let raw: string;
  try {
    // turbopackIgnore: found live during implementation -- `next build`'s Turbopack tracer
    // otherwise statically flags this call ("Dynamic filesystem access causes tracing of the
    // whole project") because sessionsPath() is not a literal, and (for `output: "standalone"`,
    // insight/Dockerfile.web's runtime stage) falls back to tracing the ENTIRE project into the
    // standalone output rather than just this route's real dependencies. The path genuinely is
    // dynamic (INSIGHT_SESSIONS_PATH env var, or a REPO_ROOT-relative default computed at
    // runtime -- Decision 2), so there is no literal path to give the tracer instead; this is
    // the fix Next's own warning names. Not a correctness change -- readSessionsFile()'s
    // behavior is identical either way.
    raw = readFileSync(/* turbopackIgnore: true */ sessionsPath(), "utf-8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return EMPTY_SESSIONS_FILE;
    throw e;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`session-epoch store at ${sessionsPath()} could not be parsed as JSON`);
  }
  if (!isShapeValid(parsed)) {
    throw new Error(
      `session-epoch store at ${sessionsPath()} is not a valid {version:1, epochs:{...}} shape`,
    );
  }
  return parsed;
}

/** Same-directory temp-file-plus-rename, ported from store.py's `_write_accounts_data` (issue
 * #308 [E18.S3], .sdlc/plans/308.md Decision 2). A fresh, unpredictable per-call name
 * (`crypto.randomBytes`, mirroring `secrets.token_hex` on the Python side) rather than a fixed
 * `.tmp` name, then `renameSync` -- POSIX `rename()` never dereferences an existing destination,
 * so the swap always lands a real file at `filePath`. */
function writeSessionsFileAtomic(filePath: string, data: SessionsFile): void {
  const dir = path.dirname(filePath);
  mkdirSync(dir, { recursive: true });
  const tmpPath = path.join(
    dir,
    `.${path.basename(filePath)}.${randomBytes(16).toString("hex")}.tmp`,
  );
  writeFileSync(tmpPath, JSON.stringify(data, null, 2), { mode: 0o600 });
  renameSync(tmpPath, filePath);
}

/** The epoch currently live for `username`, or 0 if it has never been bumped. Reads the file
 * fresh every call -- see this file's own header. */
export function getEpoch(username: string): number {
  return readSessionsFile().epochs[username] ?? 0;
}

// In-process write-serialization queue (issue #308 [E18.S3], .sdlc/plans/308.md Decision 2): NOT
// a cache of epoch values -- it holds only a Promise chain, so two overlapping bumpEpoch() calls
// serialize instead of racing each other's read-modify-write. Failed writes are swallowed here
// ONLY to keep the chain alive for the NEXT caller; the failure itself still propagates to the
// caller that triggered it, via the Promise `next` returns below.
let writeQueue: Promise<void> = Promise.resolve();

function bumpEpochOnce(username: string): void {
  const current = readSessionsFile();
  const nextEpoch = (current.epochs[username] ?? 0) + 1;
  writeSessionsFileAtomic(sessionsPath(), {
    version: 1,
    epochs: { ...current.epochs, [username]: nextEpoch },
  });
}

/** Bumps `username`'s epoch by one and persists it, serialized against any other in-flight
 * bumpEpoch() call in this process (see writeQueue above). Called from auth.ts's
 * `events.signOut` (Decision 1) -- this is the write half of the revocation mechanism; `getEpoch`
 * above is the read half every subsequent request re-checks against. */
export function bumpEpoch(username: string): Promise<void> {
  const result = writeQueue.then(() => bumpEpochOnce(username));
  // Swallow here so one failed write does not permanently wedge the queue for every later caller;
  // the actual error still reaches whoever awaited `result` below, unaffected by this catch.
  writeQueue = result.catch(() => {});
  return result;
}
