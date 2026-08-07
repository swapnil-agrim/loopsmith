// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Step 4. CI-ONLY -- browser-dependent, same reasoning as
// prove-absence-primitives-render.mjs's own header / insight/verify_web.py's docstring: a hard
// browser dependency in `npm run test` would park every goal in the repo on a machine with no
// Chromium-family browser. Wired as `npm run prove:shell-responsive`, in
// .github/workflows/ci.yml's `web` job, after `npm run prove:absence-states` (reuses the browser
// that job already installed for prove:fonts -- zero incremental provisioning cost).
//
// Server: reuses the SAME `.next` build the earlier `npm run build` (inside
// insight/verify_web.py's CHECKS, run earlier in this job) already produced -- no rebuild. Same
// server-lifecycle pattern as prove-absence-primitives-render.mjs.
//
// Proves done-when 1 ("every page uses [the shell]", checked per-page, existence-first) and
// done-when 2 ("reflows at 1440/1024/768 with no horizontal scroll; wide content scrolls inside
// its own container") against the two pages that exist today:
//   - "/"                    -- the one production route.
//   - "/dev/absence-states"  -- reachable here because INSIGHT_DEV_ROUTES=1 is set at job level
//                               (ci.yml) before the build this script's server reuses; carries a
//                               deliberately-wide fixture element
//                               (data-testid="dev-wide-fixture", see that page's own comment)
//                               whose sole job is giving this script something wide to test
//                               containment against.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

import { loadNavItems } from "./prove-nav-items.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

const WIDTHS = [1440, 1024, 768]; // exactly the three widths named in issue #305's done-when 2
const HEIGHT = 900; // arbitrary but fixed -- only width is under test
const TOLERANCE_PX = 1; // absorbs benign subpixel rounding; never used to hide a real overflow
const PAGES = ["/", "/dev/absence-states"];

// ---- server lifecycle (identical pattern to prove-absence-primitives-render.mjs) ---------------

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
  return { proc, baseUrl };
}

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

async function assertShellPresent(page, pageLabel) {
  for (const testid of ["shell-masthead", "shell-nav", "shell-content"]) {
    const locator = page.locator(`[data-testid="${testid}"]`);
    const count = await locator.count();
    assert.equal(
      count, 1,
      `${pageLabel}: expected exactly one [data-testid="${testid}"], found ${count}`,
    );
  }
}

async function assertNoPageOverflow(page, pageLabel, width) {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  assert.ok(
    scrollWidth <= clientWidth + TOLERANCE_PX,
    `${pageLabel} @ ${width}px: page must not scroll horizontally, got scrollWidth=${scrollWidth} ` +
    `clientWidth=${clientWidth}`,
  );
  console.log(
    `OK: ${pageLabel} @ ${width}px -- no page-level horizontal scroll (scrollWidth=${scrollWidth}, ` +
    `clientWidth=${clientWidth})`,
  );
}

// done-when 1 says the shell composes NAVIGATION -- not that a nav element exists. An empty
// <nav data-testid="shell-nav"> passes assertShellPresent identically to a correct one, so the
// count of rendered items is checked against the real NAV_ITEMS (imported, not hardcoded): a
// .map() that throws, renders zero, or truncates is caught here and nowhere else.
async function assertNavRendered(page, pageLabel, navItems) {
  const rendered = page.locator(
    '[data-testid="shell-nav-link"], [data-testid="shell-nav-placeholder"]',
  );
  const count = await rendered.count();
  assert.equal(
    count, navItems.length,
    `${pageLabel}: expected ${navItems.length} rendered nav items (one per NAV_ITEMS entry), ` +
    `found ${count}`,
  );

  const links = page.locator('[data-testid="shell-nav-link"]');
  assert.equal(await links.count(), 1, `${pageLabel}: expected exactly one linked nav item`);
  const href = await links.first().getAttribute("href");
  assert.equal(href, "/", `${pageLabel}: the one linked nav item must point at "/", got "${href}"`);

  console.log(`OK: ${pageLabel} -- ${count} nav items rendered, 1 linked to "/"`);
}

async function assertWideContentContained(page, width) {
  const shellContent = page.locator('[data-testid="shell-content"]');
  const { scrollWidth, clientWidth } = await shellContent.evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  }));
  assert.ok(
    scrollWidth > clientWidth,
    `/dev/absence-states @ ${width}px: shell-content must overflow internally (its wide fixture ` +
    `child is the point) -- got scrollWidth=${scrollWidth} clientWidth=${clientWidth}, meaning the ` +
    `fixture did not exercise containment at this width`,
  );
  console.log(
    `OK: /dev/absence-states @ ${width}px -- wide fixture overflows inside shell-content only ` +
    `(scrollWidth=${scrollWidth} > clientWidth=${clientWidth})`,
  );
}

async function main() {
  const t0 = Date.now();
  const navItems = await loadNavItems();
  const { proc, baseUrl } = await startNext();
  let browser;
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: HEIGHT });
      for (const pagePath of PAGES) {
        await page.goto(`${baseUrl}${pagePath}`);
        await assertShellPresent(page, `${pagePath} @ ${width}px`);
        await assertNavRendered(page, `${pagePath} @ ${width}px`, navItems);
        await assertNoPageOverflow(page, pagePath, width);
        if (pagePath === "/dev/absence-states") {
          await assertWideContentContained(page, width);
        }
      }
    }

    // ---- negative control ---------------------------------------------------------------------
    // Proves the page-level "no horizontal scroll" assertion is not a tautology: injects a
    // 3000px-wide element straight into <body>, OUTSIDE shell-content's overflow boundary, and
    // confirms the SAME measurement this script relies on above now reports overflow. The probe is
    // removed in a finally so a failing control assertion cannot leave it behind for a later step.
    await page.goto(`${baseUrl}/`);
    await page.setViewportSize({ width: 1440, height: HEIGHT });
    const before = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    assert.ok(
      before.scrollWidth <= before.clientWidth + TOLERANCE_PX,
      "negative control precondition: page must start with no horizontal scroll",
    );

    await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.setAttribute("data-testid", "negative-control-overflow-probe");
      probe.style.width = "3000px";
      probe.style.height = "1px";
      document.body.appendChild(probe);
    });

    try {
      const after = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      assert.ok(
        after.scrollWidth > after.clientWidth + TOLERANCE_PX,
        `negative control: injecting a 3000px element into <body> must produce page-level ` +
        `horizontal overflow the comparator detects -- got scrollWidth=${after.scrollWidth} ` +
        `clientWidth=${after.clientWidth}, meaning the comparator cannot actually fail`,
      );
      console.log(
        `OK: negative control -- comparator correctly reports overflow after injecting a wide ` +
        `element outside shell-content (scrollWidth ${before.scrollWidth}->${after.scrollWidth})`,
      );
    } finally {
      await page.evaluate(() => {
        document.querySelector('[data-testid="negative-control-overflow-probe"]')?.remove();
      });
    }

    await page.close();
  } finally {
    if (browser) await browser.close();
    proc.kill();
  }

  console.log(`\nOK: prove-shell-responsive-frame (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-shell-responsive-frame");
  console.error(err);
  process.exit(1);
});
