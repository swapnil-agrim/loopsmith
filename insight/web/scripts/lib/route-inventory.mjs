// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 4. Walks src/app/**/{page,route}.* to produce
// the EXHAUSTIVE list of real routes -- a route added in E19 is picked up automatically because
// this reads the real filesystem, never a maintained list (done-when 3's "not sampling").
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

const ROUTE_FILE_RE = /^(page|route)\.(ts|tsx|js|jsx)$/;

// Distinct from `null` (route group -- contributes no segment, but its children are still
// walked) so the walker can tell "drop this segment" from "drop this segment AND everything
// beneath it" (a private folder).
const SKIP_SUBTREE = Symbol("skip-subtree");

/** Maps one path SEGMENT (a folder name directly under an app-router directory) to what it
 * contributes to the final URL pathname. Exported so route-inventory.test fixtures (see the
 * proof script) can exercise it directly against synthetic shapes this repo does not have yet. */
export function segmentFor(folderName) {
  if (folderName.startsWith("_")) return SKIP_SUBTREE; // Next.js "private folder" convention
  if (folderName.startsWith("(") && folderName.endsWith(")")) return null; // route group
  if (/^\[\[\.\.\..+\]\]$/.test(folderName)) return "sample-catch-all"; // optional catch-all
  if (/^\[\.\.\..+\]$/.test(folderName)) return "sample-catch-all"; // catch-all
  if (/^\[.+\]$/.test(folderName)) return "sample-id"; // dynamic segment
  return folderName;
}

export const SKIP = SKIP_SUBTREE;

/** Walks `appDir` and returns every route pathname it finds, sorted, e.g.
 * ["/", "/dev/absence-states", "/login"]. */
export function walkRoutes(appDir) {
  const routes = [];

  function walk(dir, segments) {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        const seg = segmentFor(entry);
        if (seg === SKIP_SUBTREE) continue;
        walk(full, seg === null ? segments : [...segments, seg]);
        continue;
      }
      if (ROUTE_FILE_RE.test(entry)) {
        routes.push(segments.length === 0 ? "/" : `/${segments.join("/")}`);
      }
    }
  }

  walk(appDir, []);
  return [...new Set(routes)].sort();
}
