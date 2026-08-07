// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #308 [E18.S3], .sdlc/plans/308.md Decision 2, Task 4. Compiles the real sessionEpoch.ts
// with the local tsc (same tsc-scratch.mjs pattern prove-python-bridge-exit-codes.mjs already
// established) and drives it directly -- no server, no browser, no next build.
//
// The one property worth proving that a naive test would not catch: DURABILITY ACROSS A FRESH
// MODULE INSTANCE. Decision 2's whole amendment is that this file must never rely on in-memory
// state surviving between two separately-loaded copies of itself (proxy.ts and the sign-out route
// handler get exactly that in the real app, via Next's standalone server loading each through its
// own `require()`). This script simulates "two separate module instances" the same way: importing
// the compiled output twice under two different specifiers (a `?instance=` query-busts Node's ESM
// module cache, which keys strictly on the resolved specifier string) and asserting the SECOND
// instance reads what the FIRST instance persisted, with nothing shared between them but the file
// on disk.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync } from "./lib/tsc-scratch.mjs";

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

function compileSessionEpoch(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  const src = path.join(WEB, "src", "lib", "auth", "sessionEpoch.ts");
  writeFileSync(path.join(dir, "sessionEpoch.ts"), readFileSync(src, "utf-8"));
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `sessionEpoch.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "sessionEpoch.js");
}

/** Query-bust Node's ESM module cache so this import re-executes the module's top level and gets
 * its own bindings -- see this file's own header for why that matters here specifically. */
let importCounter = 0;
async function freshImport(emittedPath) {
  importCounter += 1;
  return import(`${pathToFileURL(emittedPath).href}?instance=${importCounter}`);
}

async function main() {
  // stateDir OUTLIVES the tsc scratch dir below (that one is torn down by
  // runScenarioInWebAsync's own `finally` the instant its callback returns) -- so the compiled
  // module is COPIED here, as a `.mjs` file (forcing ESM parsing regardless of any nearby
  // package.json's "type" field, since stateDir has none of its own), before the scratch dir
  // disappears. Both freshImport() calls below import from this persistent copy, which is what
  // lets scenario 4 exercise a genuinely separate module instance reading state scenario 1-3's
  // instance wrote, exactly like proxy.ts and the sign-out route handler do in the real app.
  const stateDir = mkdtempSync(path.join(tmpdir(), "insight-web-session-epoch-proof-"));
  const modulePath = path.join(stateDir, "sessionEpoch.mjs");
  const sessionsPath = path.join(stateDir, "sessions.json");
  process.env.INSIGHT_SESSIONS_PATH = sessionsPath;

  await runScenarioInWebAsync(".session-epoch-proof-scratch-", async (dir) => {
    const emittedPath = compileSessionEpoch(dir);
    writeFileSync(modulePath, readFileSync(emittedPath, "utf-8"));
  });

  try {
    // ---- Scenario 1: missing file reads as epoch 0 ---------------------------------------------
    const mod1 = await freshImport(modulePath);
    assert.equal(
      mod1.getEpoch("alice"), 0,
      "getEpoch() on a username with no sessions file at all must default to 0",
    );
    console.log("OK: getEpoch() with no sessions file on disk defaults to 0");

    // ---- Scenario 2: bumpEpoch then getEpoch, same in-process module instance ------------------
    await mod1.bumpEpoch("alice");
    assert.equal(
      mod1.getEpoch("alice"), 1,
      "getEpoch() must observe a bumpEpoch() this same module instance just performed",
    );
    console.log("OK: bumpEpoch() then getEpoch() in the same module instance returns 1");

    // A second, un-bumped username stays at 0 -- the write must be scoped to one username, not a
    // global counter.
    assert.equal(mod1.getEpoch("bob"), 0, "bumpEpoch('alice') must not affect a different username");
    console.log("OK: bumpEpoch() for one username does not affect a different username's epoch");

    // ---- Scenario 3: on-disk shape, asserted directly, not just through behavior ---------------
    const onDisk = JSON.parse(readFileSync(sessionsPath, "utf-8"));
    assert.deepEqual(
      onDisk, { version: 1, epochs: { alice: 1 } },
      "on-disk shape must be exactly {version:1, epochs:{...}} -- a future accidental shape " +
      "drift should fail here, not surface later as a silent auth.ts miscompare",
    );
    console.log("OK: on-disk JSON shape is exactly {version:1, epochs:{alice:1}}");

    // ---- Scenario 4: durability across a FRESH module instance (the real point of this proof) --
    // Decision 2's amendment: proxy.ts and the sign-out route handler are two SEPARATE module
    // instances in the real app (two separate require()s of two separately compiled bundles), so
    // "a second instance reads what the first persisted" is the actual property that makes
    // revocation work at all -- a load-once in-memory Map would fail this exact assertion.
    const mod2 = await freshImport(modulePath);
    assert.equal(
      mod2.getEpoch("alice"), 1,
      "a FRESH module instance (simulating a process restart / a separate require()) must read " +
      "the epoch bumpEpoch() persisted from the FIRST instance, not re-default to 0 -- this is " +
      "the durability half of Decision 2's cache-free design",
    );
    console.log("OK: a fresh module instance reads the persisted epoch, not 0 (durability proven)");

    // ---- Scenario 5: overlapping bumpEpoch() calls do not race each other's read-modify-write --
    // Proves the in-process write-serialization queue (this file's own header) actually serializes
    // instead of merely existing: two bumpEpoch() calls fired without awaiting the first must
    // still both land, not one clobbering the other's read.
    const before = mod2.getEpoch("carol");
    await Promise.all([mod2.bumpEpoch("carol"), mod2.bumpEpoch("carol")]);
    assert.equal(
      mod2.getEpoch("carol"), before + 2,
      "two overlapping bumpEpoch() calls for the same username must both be applied -- a lost " +
      "update here would mean the write-serialization queue is not actually serializing",
    );
    console.log("OK: two overlapping bumpEpoch() calls for the same username both apply (no lost update)");

    // ---- Scenario 6: a corrupt sessions file fails LOUD, not silently as epoch 0 ----------------
    // The fail-closed argument in sessionEpoch.ts's own readSessionsFile() docstring: silently
    // reading a corrupt file as "no epochs" would be the fail-OPEN direction this file exists to
    // avoid (a revoked token's non-zero stamped epoch would then wrongly compare equal to a
    // corrupt-read default of 0).
    writeFileSync(sessionsPath, "{not valid json");
    assert.throws(
      () => mod2.getEpoch("alice"),
      "a corrupt sessions file must make getEpoch() throw, never silently default to 0",
    );
    console.log("OK: a corrupt sessions file makes getEpoch() throw (fail closed, not fail open)");
  } finally {
    delete process.env.INSIGHT_SESSIONS_PATH;
    rmSync(stateDir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error("FAIL: prove-session-epoch-store");
  console.error(err);
  process.exit(1);
});
