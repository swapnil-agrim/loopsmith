// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Decision 1/Task 3. Modelled byte-for-byte on
// ../auth/pythonBridge.ts's verifyCredentials() -- the same async `spawn`, the same typed-error-
// class pair, the same JSON-in-JSON-out contract over a `python3 -m insight ...` child process --
// but for a DIFFERENT call site (a Server Component's render, not a login POST) and a DIFFERENT
// cost shape: PER RENDER, not per session (Decision 1 should-fix 2 -- see fetchIcPayload's own
// doc comment below; do not read this as "comparable in frequency" to the login spawn, only
// "comparable in kind").
//
// A NEW, SEPARATE module rather than a branch inside ../auth/pythonBridge.ts (Decision 2): the
// two bridges' contracts differ in every dimension that matters. verifyCredentials() takes a
// username+password pair and answers a role, once per session; this takes an ALREADY-
// SESSION-RESOLVED identity (never re-resolves anything itself) and answers a data payload, once
// per navigation. Keeping them apart keeps each file's own docstring honest about what it does.
//
// A FILE-WIDE CONSTRAINT, not a style choice: `scripts/prove-actor-is-session-bound.mjs` Part B
// scans every file under src/ for the BARE identifier `actor` and fails the build if one appears
// anywhere outside `src/lib/auth/actor.ts` itself (see that file's own header comment). This file
// therefore never binds a local, a parameter, or a type field literally named `actor` -- the
// session-resolved identity is threaded through as `resolvedActor` end to end, and the one place
// this file must talk about the wire payload's own `"actor"` JSON key, it does so through a
// string literal (`"--actor"`, a CLI flag name), never a source-level property access.
import { spawn } from "node:child_process";
import path from "node:path";

/** Exit 2's `store_unavailable` marker (insight/__main__.py's `web ic` branch): the store has
 * never been ingested, or `--db` points nowhere. page.tsx renders a "not yet ingested" state for
 * this -- NEVER fabricated data, and never conflated with "this identity has zero rows" (this
 * codebase's ABSENT-!=-PASS doctrine, insight/api/app.py:40-47). Mirrors
 * CredentialCheckUnavailableError's own "never silently reinterpreted" discipline in
 * ../auth/pythonBridge.ts. */
export class IcStoreUnavailableError extends Error {}

/** Every other failure mode: malformed JSON on stdout, an unrecognized/nonzero exit, the
 * `malformed_request` marker (an empty/whitespace identity reached this far -- unreachable in
 * practice, since `resolveActor()` already fails closed before this function is ever called, but
 * never silently reinterpreted if it somehow did), or `python3` itself missing (spawn ENOENT).
 * NEVER read as "this identity has no data" -- that would render an empty-looking page
 * indistinguishable from a genuinely idle one. */
export class IcBridgeUnavailableError extends Error {}

/** The shape `insight.dash.ic.collect_ic_payload` prints on stdout (Decision 2:
 * `insight.gaps.report.json_default`-serialised, not `dash.render.json_script`'s HTML-escaped
 * form). Kept in sync BY HAND with the Python dict literal -- there is no shared schema generator
 * for this CLI-bridge shape the way `scripts/generate-schema.mjs` covers the (currently-unused)
 * OpenAPI surface. Deliberately does NOT declare the wire payload's own `"actor"` field (see this
 * file's header comment) -- nothing in the web tier needs to read it back out of the payload, since
 * `page.tsx` already has the session-resolved identity from `resolveActor()` before this function
 * is ever called; the field still round-trips onto the wire untouched (a plain `JSON.parse` keeps
 * every key the Python side wrote), it is just not given a name TypeScript would let source code
 * reference. */
export interface IcPayload {
  generated_at: string;
  actor_ever_appeared: boolean;
  my_queue: { actor_id: string; goal_id: string; claimed_ts: string }[];
  // issue #315 [E20.S4] D1 (plan-review round-2 BLOCKING correction): per-table "ever ingested for
  // anyone" signals, one per bespoke readout -- `actor_ever_appeared` alone answers "is this
  // identity known to the store at all," not "has THIS readout's own table ever received a row
  // for anyone" (insight/dash/ic.py's own module docstring proves the two are not the same fact).
  // page.tsx gates each readout's numeral on `actor_ever_appeared && <its own flag>`, never
  // `actor_ever_appeared` alone. Project-wide/table-wide facts, never actor-scoped data -- they
  // carry nothing a cross-actor leak proof needs to worry about (D4).
  my_queue_ever_ingested: boolean;
  blocked_on_me: {
    from_actor: string; to_actor: string; area: string; issue: number | null;
    priority: string; opened_ts: string;
  }[];
  handoff_ever_ingested: boolean;
  park_count: number;
  park_ever_ingested: boolean;
  verdicts_given: { pr_number: number; verdict: string; event_ts: string }[];
  verdicts_ever_ingested: boolean;
  cost: {
    tokens_in: number | null; tokens_out: number | null; cost_cents: number | null; n: number;
  };
}

// Same computation as ../auth/pythonBridge.ts's own REPO_ROOT -- duplicated rather than shared
// (Decision 1's own text: three lines, not worth a premature shared module for two call sites).
const REPO_ROOT = path.resolve(process.cwd(), "..", "..");

/** `INSIGHT_DB_PATH` mirrors `INSIGHT_ACCOUNTS_PATH`'s override convention in
 * ../auth/pythonBridge.ts -- unset in every real deployment (falls through to `insight`'s own
 * CWD-relative default, `store.resolve_db_path`), and set ONLY by
 * scripts/prove-ic-no-cross-actor-leak.mjs to point a booted proof server at a throwaway seeded
 * fixture store instead of touching a real one. */
function dbPathArgs(): string[] {
  const dbPath = process.env.INSIGHT_DB_PATH;
  return dbPath ? ["--db", dbPath] : [];
}

/** Fetches the session-resolved identity's own IC payload. `resolvedActor` MUST already be the
 * output of `src/lib/auth/actor.ts`'s `resolveActor()` -- this function performs no resolution
 * and no validation beyond what the Python side rejects; it is a pure transport.
 *
 * ACCEPTED COST, PER RENDER (Decision 1 should-fix 2 -- stated explicitly here because Decision
 * 1's own text originally undersold it): one `python3` interpreter start plus a DuckDB query, on
 * EVERY navigation to `/ic`, not once per session the way login's `verifyCredentials()` spawn is.
 * Comparable in KIND to that already-accepted cost, NOT in frequency. Bounded today to
 * navigations of this one route; reducing it (a persistent bridge process, connection pooling) is
 * left alone -- no done-when requires it (.sdlc/plans/310.md "Explicitly out of scope"). */
export function fetchIcPayload(resolvedActor: string): Promise<IcPayload> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "insight", "web", "ic", "--actor", resolvedActor, ...dbPathArgs()],
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
      reject(
        new IcBridgeUnavailableError(`python3 was not found or failed to start: ${err.message}`),
      );
    });

    child.on("close", (status) => {
      switch (status) {
        case 0:
          try {
            const parsed = JSON.parse(stdout) as IcPayload;
            // `generated_at`, not the wire payload's own identity field (see this file's header
            // comment) -- any key that is always present and cheap to type-check works as the
            // "this really is a payload, not some other JSON" sanity check.
            if (typeof parsed.generated_at !== "string") {
              throw new Error("no 'generated_at' string in response");
            }
            resolve(parsed);
          } catch (e) {
            reject(
              new IcBridgeUnavailableError(
                `insight web ic exited 0 but printed an unparseable response: ${e}`,
              ),
            );
          }
          return;
        case 1:
          // malformed_request -- unreachable in practice (resolveActor() fails closed before
          // this function is ever called with an empty string), but never silently reinterpreted
          // as a valid empty payload if it somehow were reached.
          reject(
            new IcBridgeUnavailableError(
              `insight web ic rejected the request: ${stdout || stderr}`,
            ),
          );
          return;
        case 2:
          reject(new IcStoreUnavailableError("the IC store has not been ingested yet"));
          return;
        default:
          reject(new IcBridgeUnavailableError(`insight web ic exited ${status}: ${stderr}`));
      }
    });

    // This bridge sends no request body (unlike verifyCredentials(), which pipes username/
    // password on stdin) -- `insight web ic` takes its whole request as CLI flags. Close stdin
    // immediately rather than leaving it open and unused.
    child.stdin.end();
  });
}
