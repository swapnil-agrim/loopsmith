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
- **`prove-session-epoch-store.mjs`** (part of `test`, added **E18.S3** / #308) is the offline
  proof of `src/lib/auth/sessionEpoch.ts`, the per-account session-epoch store that server-side
  logout revocation is built on (`.sdlc/plans/308.md` Decision 1/2). Compiles the real file with
  the local `tsc` into a scratch dir (same pattern as `prove-python-bridge-exit-codes.mjs`), then
  drives it with no server, no browser: a missing sessions file reads as epoch 0; `bumpEpoch()`
  then `getEpoch()` in the same module instance observes the write; the on-disk JSON shape is
  asserted directly (`{"version":1,"epochs":{"<username>":<int>}}`); a **fresh, separately
  compiled/imported module instance** reads exactly what an earlier instance persisted — the
  literal durability property Decision 2's cache-free design depends on, since `proxy.ts` and the
  sign-out Route Handler load this file through two distinct `require()`s in a real deployment;
  overlapping `bumpEpoch()` calls against one account don't lose an increment (the in-process
  write-serialization queue actually serializes); and a corrupted sessions file makes `getEpoch()`
  throw rather than silently default to epoch 0, which would be the fail-*open* direction this
  file's whole design exists to avoid.
- **`prove-session-revocation-and-expiry.mjs`** (part of `test`, added **E18.S3** / #308) is the
  browser-free, sleep-free smoke proof for done-when 1 ("logout invalidates the session
  server-side — a test replays the old cookie and is refused") and done-when 2 ("sessions expire;
  an expired session is refused and redirects to login"). Reuses
  `scripts/lib/proof-session.mjs`'s `proofServerEnv()`/`SESSION_COOKIE_NAME` (from #307) to mint
  real Auth.js session cookies with next-auth's own `encode()`, then, in one server boot: (1)
  positive control — a freshly-minted valid cookie reaches `/`; (2) done-when 1 — `GET
  /api/auth/csrf` for a CSRF token/cookie, `POST /api/auth/signout` with that token plus the
  scenario-1 session cookie (the real `events.signOut` → `bumpEpoch()` path, no stub), then
  replay the **original, unmodified** cookie and assert it now redirects to `/login`; (3)
  done-when 2 — a token minted with a negative `maxAge` (an already-past `exp`, via `encode()`
  directly — never produced by waiting) is refused the same way; (4) negative control — a fresh
  cookie for a third, never-touched username is still accepted after (2) and (3) ran, proving
  those refusals are specific to the revoked/expired token rather than "every cookie is now
  refused." Per `.sdlc/plans/308.md` Decision 8, this script runs its **own** scoped `next build`
  before `next start` (into the same `.next/` output the `build` `CHECKS` step also produces, so
  that step's later build is a warm incremental rebuild, not a second cold one) and prints the
  measured build time — deliberately not reordering `insight/verify_web.py`'s `CHECKS`
  (`typecheck`/`lint`/`test`/`build`, `test` before `build`), which stays a repo-wide contract this
  goal does not touch. See the PR description for the measured `verify.command` wall-clock delta
  this decision was made contingent on.

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
- `AUTH_URL` **or** `AUTH_TRUST_HOST` — one of these is **required in any self-hosted production
  deployment**, and getting it wrong does not produce an error. `@auth/core`'s `setEnvDefaults`
  (`lib/utils/env.js:40-44`) defaults `trustHost` to
  `!!(AUTH_URL ?? AUTH_TRUST_HOST ?? VERCEL ?? CF_PAGES ?? NODE_ENV !== "production")`. `next start`
  sets `NODE_ENV=production`, so on a plain self-hosted server with none of these set `trustHost` is
  **false**, every session lookup returns `UntrustedHost`, and next-auth's `parseSessionResponse`
  turns that non-OK response into "no session" — deliberately fail-closed. The symptom is therefore
  not a crash or a log line: it is a **silent, permanent redirect to `/login` for every user**,
  including one who just signed in with correct credentials. Found by #307's CI browser proofs,
  which hit exactly this against `next start`; `scripts/lib/proof-session.mjs` sets
  `AUTH_TRUST_HOST=1` for the same reason.
- `INSIGHT_ACCOUNTS_PATH` — absolute path to `insight-accounts.json`, read by
  `src/lib/auth/pythonBridge.ts`. Required in any deployment where the Node process's CWD does not
  happen to be two directories below the repo root (i.e. always required outside plain local dev)
  — see `.sdlc/plans/307.md` Decision 1.
- `INSIGHT_SESSIONS_PATH` — absolute path to the per-account session-epoch store (default
  `.sdlc/insight-web-sessions.json`, resolved the same CWD-relative way `INSIGHT_ACCOUNTS_PATH`'s
  own default is), read/written by `src/lib/auth/sessionEpoch.ts`. This is a **separate** file
  from `INSIGHT_ACCOUNTS_PATH`'s — Node owns it exclusively (sign-out bumps it directly, no
  `python3` shell-out on that path), and Python never touches it (`.sdlc/plans/308.md` Decision
  2). Same CWD caveat as `INSIGHT_ACCOUNTS_PATH`: set it explicitly in any deployment where the
  Node process's CWD is not `insight/web/`.
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

## Session lifetime, logout, and per-account throttling (E18.S3, issue #308)

`src/auth.ts`'s `session` config now sets **`maxAge: 60 * 60 * 8`** (8 hours) and
**`updateAge: 60 * 60 * 2`** (2 hours) explicitly, replacing Auth.js's own silent 30-day default
(`@auth/core/src/lib/init.ts:71`). Neither the issue nor the design spec names a specific number —
these are `.sdlc/plans/308.md` Decision 9's own reasonable defaults, short enough to be a
meaningful improvement over 30 days without inventing a compliance requirement this app does not
have; revisit if a real one shows up.

**`maxAge` is NOT an absolute ceiling on a token's total lifetime — it is an inactivity timeout**
(corrected after #308's own security review; an earlier version of this section, and of a comment
in `src/auth.ts`, both claimed otherwise). Verified against `@auth/core@0.41.3`'s own `session()`
action (`node_modules/@auth/core/src/lib/actions/session.ts:56-79`): for the JWT strategy this app
uses, **every** session check unconditionally re-signs the token with a fresh `maxAge`-out expiry
and pushes a new cookie — there is no `updateAge`-gated conditional in that code path (unlike the
database-strategy branch immediately below it in the same file, which does check
`sessionUpdateAge` before bothering to write). Since `proxy.ts` calls `auth()` — which reaches this
same code — on every guarded request, **a stolen cookie that gets replayed at least once per
`maxAge` window never expires**: each replay resets its own clock. `updateAge` is therefore not
"how often the ceiling resets" (there is no ceiling for it to reset); it is only meaningful for the
database-strategy branch this app does not use. A true absolute ceiling — expiring a token after a
fixed lifetime regardless of activity — would need a separate mechanism: an `issuedAt` timestamp
stamped onto the token at sign-in, compared against the current time on every read, and rejected
once that difference exceeds the intended lifetime. This app does not implement that today; the
session-epoch revocation described below (done-when 1) is the only mechanism that can end a
still-`maxAge`-fresh session before it goes inactive, and only in response to an explicit sign-out.

**Logout is a server-side revocation, not just a cleared cookie** (done-when 1). `signOut`
(exported from `auth.ts`, wired to a "Sign out" button in `src/components/Shell.tsx`'s masthead
whenever a session exists) and a raw POST to Auth.js's own `/api/auth/signout` both fire
`events.signOut`, which bumps that account's entry in the per-account **session-epoch** store
(`src/lib/auth/sessionEpoch.ts`, `INSIGHT_SESSIONS_PATH` above). Every subsequent request's
`callbacks.jwt` compares the epoch stamped on the presented token against the current on-disk
value; a mismatch makes the callback return `null`, which Auth.js already treats as "no session."
One integer per account, not a `jti` denylist — coarser (it revokes every session for that
account, not one specific cookie) but needs no pruning and no additional framework plumbing; see
`.sdlc/plans/308.md` Decision 1/2 for the alternatives considered and rejected.

**Per-account throttling of failed login attempts** (done-when 3) lives entirely on the Python
side — `insight/accounts/store.py`'s `verify_user` locks an account for 15 minutes after 5
consecutive failed attempts, full reset (not sliding) once the window passes, and the locked-out
response is byte-identical to an ordinary wrong-password response (no new message, no new exit
code) so this Node app needs no changes to consume it — `src/lib/auth/pythonBridge.ts`'s existing
`InvalidCredentialsError` handling already covers it unchanged. See
`insight/accounts/store.py`'s own module docstring and `_locked_accounts`/`verify_user`
docstrings for the throttle policy's full reasoning (`.sdlc/plans/308.md` Decision 3/4/5/6).
