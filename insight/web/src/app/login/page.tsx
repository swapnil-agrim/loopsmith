// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md. Done-when 1: composed from E17's TOKEN UTILITIES
// (globals.css's @theme block) -- there is no form/input/button PRIMITIVE anywhere in this repo
// yet (dossier §1.2), so this is necessarily ad hoc Tailwind, not a shared component. E19+ may
// want to extract one; not this story's job (ponytail: the ceiling here is a real <Input>/
// <Button> component pair once a second form exists to justify factoring one out).
//
// Reads `searchParams` -- this alone opts the page into dynamic rendering (Next.js's own rule for
// any page consuming that prop), so `next build` never tries to statically prerender a page that
// needs a real request (dossier risk 7).
import { loginAction } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const hasError = typeof params.error === "string";
  const callbackUrl = typeof params.callbackUrl === "string" ? params.callbackUrl : "/";

  return (
    <div className="flex min-h-screen items-center justify-center bg-panel-ground">
      <form
        action={loginAction}
        className="w-full max-w-sm rounded border border-panel-rule bg-panel-panel p-8"
      >
        <h1 className="mb-6 text-lg font-semibold text-panel-bone">LoopSmith Insight</h1>
        <input type="hidden" name="callbackUrl" value={callbackUrl} />
        {hasError ? (
          <p className="mb-4 text-sm text-panel-red">Invalid username or password.</p>
        ) : null}
        <label className="mb-1 block text-sm text-panel-dim" htmlFor="username">
          Username
        </label>
        <input
          id="username"
          name="username"
          type="text"
          required
          autoComplete="username"
          className="mb-4 w-full rounded border border-panel-rule bg-panel-raised px-3 py-2 text-panel-bone"
        />
        <label className="mb-1 block text-sm text-panel-dim" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          className="mb-6 w-full rounded border border-panel-rule bg-panel-raised px-3 py-2 text-panel-bone"
        />
        <button
          type="submit"
          className="w-full rounded bg-panel-cyan px-3 py-2 font-medium text-panel-void-ink"
        >
          Sign in
        </button>
      </form>
    </div>
  );
}
