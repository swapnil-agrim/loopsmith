// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #303 [E17.S2], .sdlc/plans/303.md Decision (d). The real proof for done-when 3:
// "computed font-family of rendered text resolves to the embedded face" -- APPLIED, not
// DECLARED. See panel.py's own historical bug (module docstring below) for exactly why
// `document.fonts.size`/`getComputedStyle().fontFamily` cannot catch this class of defect.
//
// Mechanism: Playwright driving a throwaway `node:http` static server that serves the REAL
// committed insight/web/public/ files plus the REAL committed tokens.generated.css, and reading
// the browser's actually-*resolved* font via Chrome DevTools Protocol's
// `CSS.getPlatformFontsForNode` -- NOT `getComputedStyle`, which only echoes the declared
// cascade value regardless of whether the referenced face ever loaded (see Decision d's full
// reasoning). Browser launch prefers the machine's/runner's own installed Google Chrome
// (`channel: "chrome"` -- no bundled-Chromium download at all, see the Step 1 spike numbers in
// .sdlc/plans/303.md's PR/retro notes) and falls back to Playwright's bundled Chromium if that
// is unavailable, failing loudly (never skipping) if neither launches -- see launchBrowser()'s
// own comment below for the full reasoning.
//
// Three scenarios:
//   GOOD (sans)  -- <body> text (no class needed -- tokens.generated.css's own `body` rule)
//                   must resolve to the real embedded "Atkinson Hyperlegible" face.
//   GOOD (mono)  -- a <code> element must resolve to the real embedded "IBM Plex Mono" face.
//   NEGATIVE     -- a scratch copy of the fixture, with the `body` rule's
//                   `var(--panel-font-sans)` reference replaced by the exact historical bug
//                   shape (`font-family:'Atkinson', sans-serif` -- a name no `@font-face`
//                   declares), must flip `isCustomFont` to `false`. Without this, "we call a CDP
//                   method" is not proof of anything (Decision d's own words).
import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const PUBLIC_DIR = path.join(WEB, "public");
const TOKENS_CSS_PATH = path.join(WEB, "src", "app", "tokens.generated.css");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".woff2": "font/woff2",
};

function fixtureHtml(cssHref) {
  return (
    "<!doctype html><html><head>" +
    `<link rel="stylesheet" href="${cssHref}">` +
    "</head><body>" +
    '<p id="sans">the quick brown fox</p>' +
    '<code id="mono">the quick brown fox</code>' +
    "</body></html>"
  );
}

/** Serves insight/web/public/ verbatim, plus /tokens.generated.css from the REAL committed file
 * (or a scratch override, for the negative-control scenario) and /fixture.html. */
function startServer(tokensCssText) {
  return new Promise((resolve) => {
    const server = createServer(async (req, res) => {
      if (req.url === "/fixture.html") {
        res.writeHead(200, { "content-type": MIME[".html"] });
        res.end(fixtureHtml("/tokens.generated.css"));
        return;
      }
      if (req.url === "/tokens.generated.css") {
        res.writeHead(200, { "content-type": MIME[".css"] });
        res.end(tokensCssText);
        return;
      }
      try {
        const filePath = path.join(PUBLIC_DIR, req.url);
        const body = await readFile(filePath);
        res.writeHead(200, { "content-type": MIME[path.extname(filePath)] || "application/octet-stream" });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end();
      }
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

async function getPlatformFonts(cdp, selector) {
  const { root } = await cdp.send("DOM.getDocument");
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
  return fonts;
}

async function withPage(browser, tokensCssText, fn) {
  const server = await startServer(tokensCssText);
  const { port } = server.address();
  const page = await browser.newPage();
  try {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("DOM.enable");
    await cdp.send("CSS.enable");
    await page.goto(`http://127.0.0.1:${port}/fixture.html`);
    // Wait for font loading to SETTLE before measuring. `page.goto` resolves on `load`, but
    // web fonts are fetched asynchronously and may not be applied yet -- so the measurement
    // below was racing the font load. It won on a warm cache (every local run) and lost
    // intermittently on a cold CI runner, where the same commit passed at 08:36 and failed at
    // 09:07 and 09:19, reporting the fallback face ("Liberation Sans") as if the embedded font
    // had genuinely not applied. A required check that fails on cache temperature blocks real
    // work and, worse, teaches everyone to re-run it -- which is how a guard stops being read.
    //
    // `document.fonts.ready` is the right barrier and deliberately name-agnostic: it awaits
    // whatever faces THIS page actually requested, so the negative-control page (which declares
    // the historical wrong name) is handled by the same line without hardcoding either name and
    // without the barrier ever manufacturing a face the page did not ask for.
    await page.evaluate(() => document.fonts.ready);
    return await fn(cdp);
  } finally {
    await page.close();
    server.close();
  }
}

function isApplied(fonts, familyName) {
  return fonts.some((f) => f.isCustomFont && f.familyName === familyName);
}

/** Both paths exist for a real, measured reason (.sdlc/plans/303.md Step 1's spike): the
 * machine's/runner's own installed Google Chrome (`channel: "chrome"`) launches in ~0.6s with
 * NO Playwright-managed download at all -- the fast path, and the one GitHub's `ubuntu-latest`
 * runner satisfies out of the box (confirmed against actions/runner-images' own software
 * inventory). But `channel: "chrome"` does NOT fall back to Playwright's bundled Chromium by
 * itself if no system Chrome is installed -- it just throws. A Linux dev box, a fresh
 * contributor checkout, or any non-GitHub CI container may have neither Chrome nor a
 * `playwright install`'d Chromium yet, and this script sits in `npm run test` -> `verify_web.py`
 * -> the repo-wide `verify.command` -- so a hard requirement on system Chrome alone would park
 * every goal, not just web ones, on such a machine. So: try the fast path first, fall back to
 * the bundled browser (`chromium.launch()`, no channel) if that fails, and if NEITHER launches,
 * fail loudly naming the fix -- never skip, never exit 0. A check that quietly does not run when
 * its browser is missing is the exact class of defect issue #303 exists to fix. */
async function launchBrowser() {
  let chromeErr;
  try {
    const browser = await chromium.launch({ channel: "chrome" });
    console.log('browser: system Google Chrome (channel:"chrome") -- no download needed');
    return browser;
  } catch (err) {
    chromeErr = err;
    console.log(
      `channel:"chrome" launch failed (${String(err.message).split("\n")[0]}) -- falling back ` +
      "to Playwright's bundled Chromium...",
    );
  }
  try {
    const browser = await chromium.launch();
    console.log("browser: Playwright's bundled Chromium (fallback -- system Chrome unavailable)");
    return browser;
  } catch (chromiumErr) {
    throw new Error(
      "no usable browser found -- neither the system's Google Chrome (channel:\"chrome\") nor " +
      "Playwright's bundled Chromium could be launched.\n" +
      `  channel:"chrome" error:   ${String(chromeErr.message).split("\n")[0]}\n` +
      `  bundled chromium error:   ${String(chromiumErr.message).split("\n")[0]}\n` +
      "Fix: run `npx playwright install chromium` (from insight/web/), or install Google " +
      "Chrome, then re-run `npm run test`. This check does not skip when a browser is missing " +
      "-- done-when 3 (issue #303) requires a real, applied-font proof, not a vacuous pass.",
    );
  }
}

async function main() {
  const t0 = Date.now();
  const realTokensCss = readFileSync(TOKENS_CSS_PATH, "utf-8");

  const browser = await launchBrowser();
  try {
    // ---- GOOD: the real, committed tokens.generated.css -----------------------------------
    await withPage(browser, realTokensCss, async (cdp) => {
      const sansFonts = await getPlatformFonts(cdp, "#sans");
      assert.ok(
        isApplied(sansFonts, "Atkinson Hyperlegible"),
        `expected #sans to resolve to the embedded "Atkinson Hyperlegible" face, got: ` +
        `${JSON.stringify(sansFonts)}`,
      );
      console.log("OK: body text resolves to the embedded Atkinson Hyperlegible face " +
        `(${JSON.stringify(sansFonts)})`);

      const monoFonts = await getPlatformFonts(cdp, "#mono");
      assert.ok(
        isApplied(monoFonts, "IBM Plex Mono"),
        `expected #mono to resolve to the embedded "IBM Plex Mono" face, got: ` +
        `${JSON.stringify(monoFonts)}`,
      );
      console.log("OK: code text resolves to the embedded IBM Plex Mono face " +
        `(${JSON.stringify(monoFonts)})`);
    });

    // ---- NEGATIVE CONTROL: the exact historical bug shape ---------------------------------
    // Not shipped CSS -- a scratch copy of the REAL committed tokens.generated.css with the
    // `body` rule's `var(--panel-font-sans)` reference replaced by a literal, mismatched family
    // name (the exact shape of the historical `font-family:'Atkinson'` vs. the declared
    // `"Atkinson Hyperlegible"` `@font-face` bug -- see this file's own module docstring).
    const mutatedTokensCss = realTokensCss.replace(
      "body { font-family: var(--panel-font-sans); }",
      "body { font-family: 'Atkinson', sans-serif; }",
    );
    assert.notEqual(mutatedTokensCss, realTokensCss, "negative-control fixture regressed: substitution did not land");

    await withPage(browser, mutatedTokensCss, async (cdp) => {
      const sansFonts = await getPlatformFonts(cdp, "#sans");
      assert.ok(
        !isApplied(sansFonts, "Atkinson Hyperlegible"),
        "negative control did not trip: #sans still resolved to the embedded face even with " +
        `the historical mismatched-name bug reproduced, got: ${JSON.stringify(sansFonts)}`,
      );
      console.log("OK: negative control -- the historical 'Atkinson' vs \"Atkinson " +
        `Hyperlegible\" mismatch correctly flips isCustomFont to false (${JSON.stringify(sansFonts)})`);
    });
  } finally {
    await browser.close();
  }

  console.log(`\nOK: prove-fonts-actually-apply (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-fonts-actually-apply");
  console.error(err);
  process.exit(1);
});
