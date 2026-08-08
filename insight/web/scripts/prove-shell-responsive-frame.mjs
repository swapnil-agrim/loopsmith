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
//
// issue #311 [E19.S3], .sdlc/plans/311.md Task 5: nav is now role-aware (src/lib/nav.ts's
// navItemsFor()), so the width/page loop below authenticates as the unknown-role sentinel "owner"
// (mirrors prove-role-route-matrix.mjs's own edge case: an unknown role is a real, meaningful
// session -- Home-only nav, still reachable on every shared route) instead of the arbitrary
// "admin" default, and a SEPARATE per-role block afterward authenticates as "manager" and "ic"
// (both implemented:true as of issue #313 -- each proves exactly one role item plus Home renders
// and is linked), each asserted against navItemsFor()'s own computed output via
// prove-nav-items.mjs's loadNav() -- the SAME compiled module that file's own offline proofs run
// against, so a rendering assertion here can never hardcode a count that silently drifts from the
// real table.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

import { loadNav } from "./prove-nav-items.mjs";
import { authenticatedContext, proofServerEnv } from "./lib/proof-session.mjs";

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
    // issue #307 [E18.S2]: same AUTH_SECRET the proof mints its session cookie with, or the proxy
    // decodes that cookie to null and 302s every navigation below to /login.
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
// rendered items are checked against navItemsFor()'s own computed output (imported, not
// hardcoded): a .map() that throws, renders zero, truncates, or leaks an unreachable item is
// caught here and nowhere else.
//
// issue #311 [E19.S3]: `shell-nav-placeholder` no longer exists -- navItemsFor() never returns an
// item without an href, so Shell.tsx's placeholder <span> branch was deleted, not kept dormant
// (Task 2). `[data-testid="shell-nav-link"]` is now the ONLY nav testid; asserted by href AND
// count, exactly, against `expectedItems` (the real, live navItemsFor(hasSession, role) output for
// the session under test) so this proof cannot drift from the real table either.
async function assertNavRendered(page, pageLabel, expectedItems) {
  assert.equal(
    await page.locator('[data-testid="shell-nav-placeholder"]').count(), 0,
    `${pageLabel}: shell-nav-placeholder must never render -- navItemsFor() never returns an ` +
    "hrefless item, so Shell.tsx's placeholder branch must be dead code",
  );

  const links = page.locator('[data-testid="shell-nav-link"]');
  const count = await links.count();
  assert.equal(
    count, expectedItems.length,
    `${pageLabel}: expected ${expectedItems.length} rendered nav link(s) (navItemsFor()'s own ` +
    `output), found ${count}`,
  );

  const renderedHrefs = (await links.evaluateAll((els) => els.map((el) => el.getAttribute("href")))).sort();
  const expectedHrefs = expectedItems.map((item) => item.href).sort();
  assert.deepEqual(
    renderedHrefs, expectedHrefs,
    `${pageLabel}: rendered nav hrefs ${JSON.stringify(renderedHrefs)} must exactly match ` +
    `navItemsFor()'s expected hrefs ${JSON.stringify(expectedHrefs)}`,
  );

  console.log(`OK: ${pageLabel} -- ${count} nav item(s) rendered, hrefs exactly ${JSON.stringify(renderedHrefs)}`);
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

// issue #311 [E19.S3] Task 5: one dedicated context per role under test, each asserted against
// navItemsFor()'s own computed output for that exact (hasSession=true, role) pair -- not a
// hardcoded expectation. Reuses assertShellPresent so a role's session is also proven to actually
// reach the shell (not silently redirected to /login).
async function assertRoleNavAtRoot(browser, baseUrl, role, navItemsFor) {
  const context = await authenticatedContext(browser, baseUrl, role);
  try {
    const page = await context.newPage();
    await page.setViewportSize({ width: 1440, height: HEIGHT });
    await page.goto(`${baseUrl}/`);
    assert.equal(
      new URL(page.url()).pathname, "/",
      `role=${role}: expected to land on / with a valid session, got ${page.url()}`,
    );
    const expected = navItemsFor(true, role);
    await assertShellPresent(page, `role=${role}`);
    await assertNavRendered(page, `role=${role}`, expected);
    await page.close();
  } finally {
    await context.close();
  }
}

async function main() {
  const t0 = Date.now();
  const { navItemsFor } = await loadNav();
  const { proc, baseUrl } = await startNext();
  let browser;
  try {
    browser = await launchBrowser();
    // issue #307 [E18.S2]: every page in PAGES is private by default now, so this proof has to
    // arrive with a session -- anonymously it measures the login page at three widths and proves
    // nothing about the shell. See scripts/lib/proof-session.mjs.
    //
    // issue #311 [E19.S3]: "owner" -- the same unknown-role sentinel prove-role-route-matrix.mjs
    // uses -- for the width/page loop below, since nav rendering is now role-aware and this loop's
    // real purpose is responsive layout, not nav content; an unknown role still reaches every
    // shared route (Decision 3), so it exercises the exact same pages the old "admin" fixture did,
    // while also being a real, meaningful nav case (Home-only) asserted below via navItemsFor.
    const context = await authenticatedContext(browser, baseUrl, "owner");
    const page = await context.newPage();
    const ownerNavItems = navItemsFor(true, "owner");

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: HEIGHT });
      for (const pagePath of PAGES) {
        await page.goto(`${baseUrl}${pagePath}`);
        assert.equal(
          new URL(page.url()).pathname, pagePath,
          `expected to land on ${pagePath} with a valid session, got ${page.url()} -- a redirect ` +
          "to /login means the minted session was not accepted, so every assertion below would " +
          "be measuring the login page instead of the shell",
        );
        await assertShellPresent(page, `${pagePath} @ ${width}px`);
        await assertNavRendered(page, `${pagePath} @ ${width}px`, ownerNavItems);
        await assertNoPageOverflow(page, pagePath, width);
        if (pagePath === "/dev/absence-states") {
          await assertWideContentContained(page, width);
        }
      }
    }

    // ---- role-aware nav rendering (issue #311 [E19.S3]) --------------------------------------
    // "manager" and "ic" are both implemented:true (issue #313 flipped /manager) -- each proves
    // exactly one role item plus Home renders, both linked. This computes its expectation live
    // from navItemsFor(), so the assertion itself needed no change when /manager shipped -- only
    // this comment did, to match reality.
    await assertRoleNavAtRoot(browser, baseUrl, "manager", navItemsFor);
    await assertRoleNavAtRoot(browser, baseUrl, "ic", navItemsFor);

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
