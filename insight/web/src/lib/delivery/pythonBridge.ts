// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B2, .sdlc/plans/312.md §3a/§7. Modelled on
// ../ic/pythonBridge.ts's fetchIcPayload() -- the same async `spawn`, JSON-on-stdout contract
// over a `python3 -m insight ...` child process -- but for a DIFFERENT resolver
// (`insight.api.metrics.collect_metrics()`, the same one GET /metrics already calls) and a
// DELIBERATELY narrower contract in two ways, both named in the plan:
//
//   1. NO `--actor` PARAMETER, ANYWHERE IN THIS FILE. Delivery data is aggregate-only (throughput,
//      cycle time, autonomy rate, the 42-metric board) -- there is no per-actor scope to leak
//      across, and the ABSENCE of a parameter a leak could ride on is itself part of the
//      structural guarantee prove-role-forbidden-real-server.mjs's Goal A assertions already
//      proved for the route: a denied cross-functional request never reaches this bridge at all
//      (proxy.ts denies first), and even if it somehow did, there is no identity argument here for
//      it to pass.
//   2. NO STORE-UNAVAILABLE ERROR CLASS. `insight web delivery`'s own missing-store handling
//      mirrors insight/api/app.py's GET /metrics route (app.py:69-72), not `web ic`'s exit-2
//      convention -- a missing store degrades to a normal, successful, all-absent response
//      (insight/__main__.py's `web delivery` branch). So this bridge has only ONE failure class,
//      DeliveryBridgeUnavailableError, covering everything that is NOT a legitimate data response:
//      malformed stdout, a nonzero exit, or `python3` itself missing.
import { spawn } from "node:child_process";
import path from "node:path";

import type { Metric } from "@/lib/api/metric";

/** Every failure mode this bridge can hit: malformed JSON on stdout, an unrecognized/nonzero
 * exit, or `python3` itself missing (spawn ENOENT). NEVER read as "there is no data" -- an
 * all-absent-but-successful response (see this file's header comment) is a DIFFERENT, legitimate
 * outcome that resolves normally instead of rejecting. */
export class DeliveryBridgeUnavailableError extends Error {}

// Same computation as ../ic/pythonBridge.ts's own REPO_ROOT -- duplicated rather than shared, the
// same "three lines, not worth a shared module for two call sites" reasoning that file's own
// header already gives.
const REPO_ROOT = path.resolve(process.cwd(), "..", "..");

/** `INSIGHT_DB_PATH` mirrors ../ic/pythonBridge.ts's own override convention -- unset in every
 * real deployment (falls through to `insight`'s own CWD-relative default), and set only by a
 * proof that needs to point a booted server at a throwaway store instead of a real one. */
function dbPathArgs(): string[] {
  const dbPath = process.env.INSIGHT_DB_PATH;
  return dbPath ? ["--db", dbPath] : [];
}

/** Fetches every catalog metric (all 42, in catalog-id order) via `insight web delivery`. Takes
 * NO parameters -- see this file's header comment for why that is structural, not incidental.
 * ACCEPTED COST, PER RENDER, same shape as ../ic/pythonBridge.ts's own fetchIcPayload(): one
 * `python3` interpreter start plus a DuckDB query on every navigation to `/delivery`. */
export function fetchDeliveryMetrics(): Promise<Metric[]> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "insight", "web", "delivery", ...dbPathArgs()],
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
        new DeliveryBridgeUnavailableError(
          `python3 was not found or failed to start: ${err.message}`,
        ),
      );
    });

    child.on("close", (status) => {
      if (status === 0) {
        try {
          const parsed = JSON.parse(stdout) as Metric[];
          if (!Array.isArray(parsed)) {
            throw new Error("response was not a JSON array");
          }
          resolve(parsed);
        } catch (e) {
          reject(
            new DeliveryBridgeUnavailableError(
              `insight web delivery exited 0 but printed an unparseable response: ${e}`,
            ),
          );
        }
        return;
      }
      reject(
        new DeliveryBridgeUnavailableError(`insight web delivery exited ${status}: ${stderr}`),
      );
    });

    // No request body -- `insight web delivery` takes no flags this bridge ever sets besides
    // --db (an env-driven override, not a per-request argument). Close stdin immediately rather
    // than leaving it open and unused, mirroring ../ic/pythonBridge.ts's own fetchIcPayload().
    child.stdin.end();
  });
}

/** A series is measured (its rows are present) or absent (they are not, and it says why). There is
 * no third shape and, critically, no empty-array shape: `values` exists ONLY on the measured arm,
 * so a consumer cannot render "we looked and found nothing" for a series that was never measured.
 * That is the same discriminated-union discipline `Metric` uses for scalars, applied to charts --
 * see insight/api/series.py's module docstring for the bug that motivated it. */
export type Distribution =
  | {
      state: "measured";
      unit: string;
      values: number[];
      p50: number;
      p85: number;
      min: number;
      max: number;
      measured: number;
      total: number;
    }
  | { state: "absent"; reason: string };

export type WeeklyThroughput =
  | { state: "measured"; points: Array<{ week: string; count: number }> }
  | { state: "absent"; reason: string };

export interface DeliverySeries {
  cycleTime: Distribution;
  interventions: Distribution;
  weeklyThroughput: WeeklyThroughput;
}

/** Sibling of fetchDeliveryMetrics() against `insight web delivery-series`. Same transport, same
 * single failure class, same no-actor contract -- see this file's header. Kept as a second call
 * rather than folded into the first because the two CLI actions are deliberately separate (the
 * metrics response shape is contract-tested as a bare array); the page issues both concurrently,
 * so this costs a process, not a round of latency. */
export function fetchDeliverySeries(): Promise<DeliverySeries> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "insight", "web", "delivery-series", ...dbPathArgs()],
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
        new DeliveryBridgeUnavailableError(
          `python3 was not found or failed to start: ${err.message}`,
        ),
      );
    });
    child.on("close", (status) => {
      if (status === 0) {
        try {
          const parsed = JSON.parse(stdout) as DeliverySeries;
          // An object, not an array -- the inverse of fetchDeliveryMetrics()'s own guard, and for
          // the same reason: a shape mismatch must fail loudly here rather than reaching a chart
          // as `undefined` and rendering as a blank panel that looks like measured emptiness.
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            throw new Error("response was not a JSON object");
          }
          resolve(parsed);
        } catch (e) {
          reject(
            new DeliveryBridgeUnavailableError(
              `insight web delivery-series exited 0 but printed an unparseable response: ${e}`,
            ),
          );
        }
        return;
      }
      reject(
        new DeliveryBridgeUnavailableError(
          `insight web delivery-series exited ${status}: ${stderr}`,
        ),
      );
    });

    child.stdin.end();
  });
}
