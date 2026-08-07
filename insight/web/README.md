# insight/web/

Next.js (App Router) + TypeScript application — the dashboard UI (design spec §4). Scaffold only
(**E17.S1**, #302): App Router, Tailwind v4 wired in, real `typecheck`/`lint`/`test`/`build`, and a
containerised build under `insight/Dockerfile.web` — no design tokens, no auth, no dashboards yet
(those are later stories). See "What's here" below.

## The BUSL marker for `.ts`/`.tsx`

Every `.ts`/`.tsx` file here must open with the marker derived MECHANICALLY from
`insight/HEADER.txt`'s single `#` line — swap the leading `#` for `//`, nothing else changes:

    // SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.

Enforced by `tests/test_licence_boundary.py` (`_ts_marker`), derived from the one stored string,
never retyped a second time.

**Ordering: the marker comes FIRST, before any `"use client"` (or other) directive prologue.**
Verified against the actual checker (`_carries_marker`): marker-then-directive is accepted because
the marker sits on line 1; directive-then-marker is REJECTED, because unlike the `.py` side there
is no shebang/encoding-cookie allowance for `.ts`/`.tsx` that would let the checker look past a
leading string-literal statement. Putting the marker first is also syntactically safe for Next.js's
own directive-prologue detection, which only cares that `"use client"` is the first *statement* —
a leading line comment before it does not disqualify it. E17.S1 is the first story that has to get
this ordering right; there is no fixup once real `"use client"` files exist.

## What's here (E17.S1, #302)

`package.json` + a committed `package-lock.json` (E16.S3, #301) arm `insight/verify_web.py` for
real: every future goal's verify gate runs `npm ci` plus all four of
`typecheck`/`lint`/`test`/`build`, in a fresh worktree, every time. All four are now real —
none is a stub.

- **`typecheck`** is real: `tsc --noEmit` (strict), with a `pretypecheck` hook
  (`scripts/check-schema-fresh.mjs`) that regenerates `src/lib/api/schema.d.ts` from the committed
  `openapi.json` and diffs it byte-for-byte against what's on disk — the enforcement mechanism
  behind "generated, not hand-edited." Regenerate with `node scripts/generate-schema.mjs` (cwd
  `insight/web/`) after re-running `python3 -m insight.api.export_openapi` from the repo root. A
  bare `tsc --noEmit` passes on a fresh checkout with no prior `next build`/`next typegen` —
  verified directly — so `pretypecheck` stays exactly the schema-freshness check E16.S3 built; no
  `next typegen` step was needed.
- **`lint`** is real: `eslint .` against `eslint.config.mjs` (flat config,
  `eslint-config-next/core-web-vitals`). `next lint` no longer exists in Next 16 (removed
  upstream), so this calls ESLint directly. `eslint` is pinned to `9.39.5`, not the registry's own
  `latest` (`10.8.0`) — `eslint-config-next@16.3.0`'s toolchain is not yet compatible with ESLint
  10's internal API (`TypeError: scopeManager.addGlobals is not a function`); re-verify before
  bumping this pin.
- **`test`** is real: `scripts/prove-metric-contract-safety.mjs` is the mechanical proof that
  renaming a Pydantic field and regenerating breaks the frontend type-check (see
  `src/lib/api/metric.consumer.ts`'s `metricLabel`), plus the discriminated-union narrowing proof
  (`measuredValueOrNull`). **`test` does NOT run the font-applied proof** — see `prove:fonts`
  below for why and where that proof actually runs.
- **`prove:fonts`** (`scripts/prove-fonts-actually-apply.mjs`, added **E17.S2** / #303) is the
  done-when-3 proof that the embedded typefaces are actually *applied* (Chrome DevTools Protocol
  `CSS.getPlatformFontsForNode`), not merely declared. **Toolchain prerequisite: a real
  Chromium-family browser.** The script prefers the machine's/runner's own installed Google Chrome
  (`channel: "chrome"` — no download at all, satisfied out of the box on GitHub's `ubuntu-latest`)
  and falls back to Playwright's bundled Chromium if that is unavailable; if NEITHER is present it
  fails loudly naming the fix (`npx playwright install chromium`), rather than skipping. See
  `launchBrowser()` in that script for the full reasoning and `.sdlc/plans/303.md` Step 1 for the
  measured numbers behind the choice.

  This is deliberately **NOT** part of `npm run test`, and so NOT part of `verify_web.py`'s
  `CHECKS` / `.sdlc/config.json`'s repo-wide `verify.command` (an issue #303 [E17.S2] review fix,
  correcting an earlier version of this file that claimed otherwise). That gate runs in a fresh
  worktree for every goal in this repo, with `verify.enforce: true`, on any machine — a hard
  requirement on a real browser there would park every goal, not just web ones, on any box without
  system Chrome or an installed Playwright Chromium. Instead, `npm run prove:fonts` runs as its own
  step in `.github/workflows/ci.yml`'s `web` job, right after the `python3 insight/verify_web.py`
  step (which has already run `npm ci`, so `node_modules` is in place). `ubuntu-latest` ships
  Google Chrome and `web` is one of the five required branch-protection contexts, so the proof
  still hard-gates every merge to main and still never skips — it has just moved out of the
  repo-wide local gate into the one place guaranteed to have a browser.
- **`build`** is real: `next build`, with `next.config.mjs` setting `output: "standalone"` so
  `insight/Dockerfile.web`'s runtime stage can copy a self-contained `.next/standalone/server.js`
  without a second `npm ci`.
- **`prove-nav-items.mjs`** (part of `test`, added **E17.S4** / #305) is the browser-free half of
  the done-when that the nav stays "a placeholder list" (no auth/role model): compiles the real
  `src/lib/nav.ts` with the local `tsc` into a scratch dir, then asserts `NAV_ITEMS` is non-empty,
  exactly one entry has `href="/"`, at least one entry has no `href` (a real placeholder), and no
  entry's `href` contains `"dev"` — a permanent, mechanical guard against ever linking
  `/dev/absence-states` from the nav (see that page's own header for why). Exports `loadNavItems()`
  so `prove:shell-responsive` below can assert the *rendered* nav against this same source of
  truth instead of a hardcoded count.
- **`prove:shell-responsive`** (`scripts/prove-shell-responsive-frame.mjs`, added **E17.S4** /
  #305) is the CI-only behavioral proof of the app shell (`src/components/Shell.tsx`): on both
  pages that exist today (`/` and `/dev/absence-states`), asserts the masthead/nav/content-frame
  each render exactly once, the nav renders one item per `NAV_ITEMS` entry (an empty `<nav>` would
  otherwise pass the presence check vacuously), and — at exactly 1440/1024/768px — that the page
  itself never scrolls horizontally while a deliberately-wide fixture on the dev page overflows
  *inside* `shell-content` specifically. A negative control injects a 3000px element outside
  `shell-content` to prove the overflow comparator can actually fail, not just always pass. Same
  CI-only reasoning as `prove:fonts`/`prove:absence-states` above — a hard browser dependency in
  the always-on local gate would park every goal in the repo on a machine without a Chromium-family
  browser. Runs in `.github/workflows/ci.yml`'s `web` job, right after `prove:absence-states`,
  reusing the browser `prove:fonts`'s install step already provisioned and the `.next` build
  `prove:absence-states` already relies on — zero incremental cost.

The app itself (`src/app/`) is a minimal App Router scaffold: `layout.tsx` renders
`src/components/Shell.tsx` (masthead + role-placeholder nav + content frame, **E17.S4** / #305)
around every route, then `page.tsx`. Tailwind v4 wired in via `globals.css`'s
`@import "tailwindcss"` (CSS-first config — no `tailwind.config.ts` for defaults; most design
tokens are E17.S2, out of scope here). `page.tsx` imports `Metric` from `@/lib/api/metric` and
reads `.label`, so the one real page is already wired to the generated API contract rather than
being inert boilerplate — no live data fetch yet (a later story). `src/lib/nav.ts` holds the shell's
nav data (`NAV_ITEMS`) as plain, browser-free, auth-free static data — see `Shell.tsx`'s and
`nav.ts`'s own header comments and `.sdlc/plans/305.md` for why the shell is a single component
rendered from exactly one place (`layout.tsx`'s root layout), which is what makes "every page uses
the shell" hold structurally rather than by convention, and why `shell-content` is a `<div>`, not a
`<main>` (both existing pages already render their own `<main>`, and a document has exactly one
main landmark).

`insight/Dockerfile.web` (sibling to this directory, not at the repository root — spec §7) is a
multi-stage build proven by a `docker build` step in CI's `web` job, not by the local gate:
`insight/verify_web.py` is deliberately offline-safe, and pulling `node:22-slim` needs network.

`insight/tests/test_verify_web.py`'s machine-checked invariant — `package.json` must have a
sibling `package-lock.json` and declare every `CHECKS` name — continues to enforce for real.

## Environment variables (E18.S2, issue #307)

- `AUTH_SECRET` — required by Auth.js in any real deployment (throws `MissingSecretError` at
  request time if unset; **not** required for `next build`/`next lint`/`next typecheck`, since
  Auth.js only validates config lazily, per request — verified against `@auth/core@0.41.3`'s own
  `assertConfig`, called from `Auth()`, never from `NextAuth()`'s own module-scope call).
- `INSIGHT_ACCOUNTS_PATH` — absolute path to `insight-accounts.json`, read by
  `src/lib/auth/pythonBridge.ts`. Required in any deployment where the Node process's CWD does not
  happen to be two directories below the repo root (i.e. always required outside plain local dev)
  — see `.sdlc/plans/307.md` Decision 1.
- `INSIGHT_TRUST_PROXY_PROTO` — set to `1` **only** when this app sits behind a reverse proxy you
  control that always overwrites `X-Forwarded-Proto` (nginx: `proxy_set_header X-Forwarded-Proto
  $scheme;`). It is what makes `src/lib/auth/secure.ts` believe that header when deciding the
  session cookie's `Secure` flag. Leave it unset anywhere the app can be reached directly: the
  header is client-suppliable, so trusting it unconditionally let anyone strip `Secure` off their
  own session cookie by sending `X-Forwarded-Proto: http` over a real HTTPS connection (found by
  #307's security review). **You need this in exactly the common production topology** where the
  proxy terminates TLS and forwards to `http://127.0.0.1:3000` — without it the app only sees a
  plaintext loopback URL and, per the rule below, would issue a non-`Secure` cookie.

  Everything else about that decision is deliberately fail-closed: with no trusted-proxy opt-in,
  `Secure` is set unless the server itself observes plaintext on a *loopback* host (local
  `npm run dev`). A genuinely plaintext deployment on a real hostname therefore breaks **visibly**
  rather than silently shipping a cookie that a network attacker can lift.
