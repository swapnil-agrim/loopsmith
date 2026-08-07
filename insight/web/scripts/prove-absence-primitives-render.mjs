// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Decision (d), Step 6. CI-ONLY -- needs a real browser,
// see prove-fonts-actually-apply.mjs's own header/insight/verify_web.py:6-19 for why a
// browser-dependent check cannot go in `npm run test`/CHECKS. Wired as `npm run
// prove:absence-states`, in .github/workflows/ci.yml's `web` job, after `npm run prove:fonts`
// (which already installs the browser this script also uses).
//
// Server: spawns `next start` against the `.next` output the preceding `npm run build` (inside
// insight/verify_web.py's CHECKS, run earlier in the same job) already produced -- no rebuild.
// Reachable at /dev/absence-states specifically because INSIGHT_DEV_ROUTES=1 was set at JOB level
// before that build ran (ci.yml's `web` job env, Step 5) -- the route is a build-time-resolved
// static page, so this script's own environment does not need the var set again.
//
// For each of <Metric>/<MetricCell> x three states:
//   measured        -- FIRST asserts the numeral slot (data-testid="metric-numeral") EXISTS
//                       (count() === 1), THEN asserts its text/coverage. Existence-first: a
//                       typo'd selector matching zero elements must not make the "no digit"
//                       assertions below pass vacuously. ALSO asserts the rendered
//                       `background-image` is exactly "none" -- issue #312 [E20.S1] Goal B, Task
//                       B1's own negative control: hatch must be state-gated, not always-on.
//   absent_no_data /
//   absent_unbuilt  -- re-asserts the SAME selector still resolves to exactly one element (the
//                       slot renders empty for absent states, never omitted), THEN asserts NO
//                       DIGIT CHARACTER appears in its text -- a direct proof of done-when 1's
//                       "no numeral at all", not merely "the field is empty" (also catches a
//                       stray literal "0"). Separately asserts the rendered `border-style`
//                       (getComputedStyle, never a className/data-attribute string comparison --
//                       Decision (d)) is the pinned structural value (dashed / dotted) and that
//                       the two absent states differ from each other. Also re-asserts fixText
//                       differs at the DOM level (Step 1's logic proof, re-checked here).
//                       issue #312 [E20.S1] Goal B, Task B1: ALSO asserts the rendered
//                       `background-image` (getComputedStyle, same never-a-className-comparison
//                       discipline as border-style) contains "repeating-linear-gradient" -- the
//                       hatch ported verbatim from insight/dash/instrument.py onto Metric.tsx/
//                       MetricCell.tsx (done-when 3's "absent panels use shared absence material:
//                       achromatic, hatched").
//
// Negative control: compares the SAME state's border-style across <Metric> and <MetricCell> (both
// use the identical BORDER_CLASS mapping) and asserts EQUAL -- proves the comparator can actually
// report "not distinct", not a tautology that always reports "different" regardless of input.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

import { authenticatedContext, proofServerEnv } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

const STATES = [
  { id: "measured", expectedBorderStyle: "solid" },
  { id: "absent-no-data", expectedBorderStyle: "dashed" },
  { id: "absent-unbuilt", expectedBorderStyle: "dotted" },
];
const COMPONENTS = [
  { name: "Metric", containerPrefix: "case-metric-" },
  { name: "MetricCell", containerPrefix: "case-cell-" },
];

// ---- server lifecycle --------------------------------------------------------------------------

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForServer(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 404) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`server did not become ready at ${url} within ${timeoutMs}ms: ${lastErr}`);
}

async function startNext() {
  const port = await getFreePort();
  const proc = spawn(NEXT_BIN, ["start", "-p", String(port)], {
    cwd: WEB,
    stdio: ["ignore", "pipe", "pipe"],
    // issue #307 [E18.S2]: the server needs the same AUTH_SECRET the proof mints its session
    // cookie with, or the proxy decodes that cookie to null and 302s this proof to /login.
    env: proofServerEnv(),
  });
  let out = "";
  proc.stdout.on("data", (d) => (out += d.toString()));
  proc.stderr.on("data", (d) => (out += d.toString()));
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForServer(`${baseUrl}/`);
  } catch (err) {
    proc.kill();
    throw new Error(`${err.message}\n-- next start output --\n${out}`);
  }
  return { proc, baseUrl, getOutput: () => out };
}

// ---- browser launch (same fallback reasoning as prove-fonts-actually-apply.mjs) ----------------

async function launchBrowser() {
  try {
    const browser = await chromium.launch({ channel: "chrome" });
    console.log('browser: system Google Chrome (channel:"chrome") -- no download needed');
    return browser;
  } catch (err) {
    console.log(
      `channel:"chrome" launch failed (${String(err.message).split("\n")[0]}) -- falling back ` +
      "to Playwright's bundled Chromium...",
    );
  }
  const browser = await chromium.launch();
  console.log("browser: Playwright's bundled Chromium (fallback -- system Chrome unavailable)");
  return browser;
}

// ---- assertions -----------------------------------------------------------------------------

const DIGIT = /[0-9]/;

async function assertCase(page, component, state) {
  const containerSelector = `[data-testid="${component.containerPrefix}${state.id}"]`;
  const numeralLocator = page.locator(`${containerSelector} [data-testid="metric-numeral"]`);

  // Existence-first, always -- see module header.
  assert.equal(
    await numeralLocator.count(),
    1,
    `${component.name}/${state.id}: expected exactly one [data-testid="metric-numeral"] inside ` +
    `${containerSelector}, found ${await numeralLocator.count()}`,
  );

  const numeralText = (await numeralLocator.textContent()) ?? "";
  const rootLocator = page.locator(`${containerSelector} [data-testid="metric-root"]`);
  assert.equal(await rootLocator.count(), 1, `${component.name}/${state.id}: expected exactly one metric-root element`);
  const backgroundImage = await rootLocator.evaluate((el) => getComputedStyle(el).backgroundImage);

  if (state.id === "measured") {
    assert.equal(
      numeralText.trim(),
      "0.82",
      `${component.name}/measured: numeral must show the formatted value, got "${numeralText}"`,
    );
    const coverageLocator = page.locator(`${containerSelector} [data-testid="metric-coverage"]`);
    assert.equal(
      await coverageLocator.count(),
      1,
      `${component.name}/measured: expected exactly one coverage element`,
    );
    const coverageText = (await coverageLocator.textContent()) ?? "";
    assert.ok(
      coverageText.includes("41") && coverageText.includes("50"),
      `${component.name}/measured: coverage text must include both the numerator (41) and ` +
      `denominator (50), got "${coverageText}"`,
    );
    // issue #312 [E20.S1] Goal B, Task B1's own MANDATORY negative control: a measured readout
    // must carry NO hatch at all -- proves the hatch is gated on absence state, not painted
    // unconditionally (which would make the "absent states are hatched" assertion below vacuous).
    assert.equal(
      backgroundImage,
      "none",
      `${component.name}/measured: expected NO background-image (hatch must be state-gated, not ` +
      `always-on), got "${backgroundImage}"`,
    );
    console.log(
      `OK: ${component.name}/measured -- numeral "${numeralText.trim()}", coverage ` +
      `"${coverageText.trim()}", background-image "none" (negative control)`,
    );
    return;
  }

  // Absent states: no digit anywhere in the numeral slot's text -- done-when 1's actual claim.
  assert.ok(
    !DIGIT.test(numeralText),
    `${component.name}/${state.id}: numeral slot must carry no digit character, got "${numeralText}"`,
  );

  const borderStyle = await rootLocator.evaluate((el) => getComputedStyle(el).borderStyle);
  assert.equal(
    borderStyle,
    state.expectedBorderStyle,
    `${component.name}/${state.id}: expected computed border-style "${state.expectedBorderStyle}", got "${borderStyle}"`,
  );

  // issue #312 [E20.S1] Goal B, Task B1: done-when 3's "hatched" requirement, ported verbatim
  // from instrument.py onto the shared primitive -- asserted via getComputedStyle, same
  // never-a-className-comparison discipline as border-style above.
  assert.ok(
    backgroundImage.includes("repeating-linear-gradient"),
    `${component.name}/${state.id}: expected computed background-image to contain ` +
    `"repeating-linear-gradient" (the ported hatch), got "${backgroundImage}"`,
  );

  const fixLocator = page.locator(`${containerSelector} [data-testid="metric-fix"]`);
  assert.equal(await fixLocator.count(), 1, `${component.name}/${state.id}: expected exactly one metric-fix element`);
  const fixText = ((await fixLocator.textContent()) ?? "").trim();
  assert.ok(fixText.length > 0, `${component.name}/${state.id}: fixText must be non-empty`);

  console.log(
    `OK: ${component.name}/${state.id} -- no digit in numeral, border-style "${borderStyle}", ` +
    `background-image hatched, fixText "${fixText}"`,
  );
  return { borderStyle, backgroundImage, fixText };
}

async function main() {
  const t0 = Date.now();
  const { proc, baseUrl, getOutput } = await startNext();
  let browser;
  try {
    browser = await launchBrowser();
    // issue #307 [E18.S2]: /dev/absence-states is private by default now, so this proof has to
    // arrive with a session -- anonymously it just renders /login and every assertion below finds
    // nothing. See scripts/lib/proof-session.mjs for why the session is minted, not logged into.
    const context = await authenticatedContext(browser, baseUrl);
    const page = await context.newPage();
    await page.goto(`${baseUrl}/dev/absence-states`);
    assert.equal(
      new URL(page.url()).pathname, "/dev/absence-states",
      `expected to land on /dev/absence-states with a valid session, got ${page.url()} -- ` +
      "a redirect to /login means the minted session was not accepted, so this proof would " +
      "otherwise assert against the login page instead of the thing it is meant to check",
    );

    const results = {}; // component.name -> state.id -> { borderStyle, fixText }
    for (const component of COMPONENTS) {
      results[component.name] = {};
      for (const state of STATES) {
        const r = await assertCase(page, component, state);
        if (r) results[component.name][state.id] = r;
      }
    }

    // ---- distinctness: the two absent states must differ (per component) -------------------
    for (const component of COMPONENTS) {
      const noData = results[component.name]["absent-no-data"];
      const unbuilt = results[component.name]["absent-unbuilt"];
      assert.notEqual(
        noData.borderStyle,
        unbuilt.borderStyle,
        `${component.name}: absent_no_data and absent_unbuilt must have DIFFERENT computed ` +
        `border-style, both were "${noData.borderStyle}"`,
      );
      assert.notEqual(
        noData.fixText,
        unbuilt.fixText,
        `${component.name}: absent_no_data and absent_unbuilt must render DIFFERENT fix text, ` +
        `both were "${noData.fixText}"`,
      );
      console.log(
        `OK: ${component.name} -- absent_no_data ("${noData.borderStyle}") and absent_unbuilt ` +
        `("${unbuilt.borderStyle}") are structurally distinct`,
      );
    }

    // ---- negative control: SAME state, two different components, must compare EQUAL --------
    // Proves the comparator can report "not distinct" and isn't a tautology that always finds a
    // difference regardless of input.
    for (const stateId of ["absent-no-data", "absent-unbuilt"]) {
      const a = results.Metric[stateId].borderStyle;
      const b = results.MetricCell[stateId].borderStyle;
      assert.equal(
        a,
        b,
        `negative control: <Metric> and <MetricCell> render the SAME state (${stateId}) and must ` +
        `compare EQUAL on border-style, got "${a}" vs "${b}"`,
      );
      console.log(`OK: negative control -- ${stateId} border-style equal across components ("${a}")`);
    }

    await page.close();
  } finally {
    if (browser) await browser.close();
    proc.kill();
  }

  console.log(`\nOK: prove-absence-primitives-render (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-absence-primitives-render");
  console.error(err);
  process.exit(1);
});
