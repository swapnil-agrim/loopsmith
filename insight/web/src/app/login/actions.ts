// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md. Server Action, not a client-side fetch: no client JS
// is needed for a login form, and this keeps the credential path entirely server-side (spec
// §5.1.1: "the web tier owns authentication entirely").
"use server";

import { signIn } from "@/auth";
import { AuthError } from "next-auth";
import { redirect } from "next/navigation";

export async function loginAction(formData: FormData) {
  const callbackUrl = (formData.get("callbackUrl") as string | null) || "/";
  try {
    await signIn("credentials", { ...Object.fromEntries(formData), redirectTo: callbackUrl });
  } catch (error) {
    // Branch on error.type, NOT on `instanceof AuthError` alone (independent code review of #307).
    // Both a wrong password and a broken bridge arrive here as AuthErrors, and collapsing them is
    // the very silent lockout Decision 1 is built to prevent -- see pythonBridge.ts.
    if (error instanceof AuthError && error.type === "CredentialsSignin") {
      // ONE generic message, deliberately not distinguishing "wrong password" from "no such user",
      // matching the Python side's single-message design (store.InvalidCredentials).
      // redirect() signals by throwing (NEXT_REDIRECT), so it must be the last thing here and must
      // not be swallowed -- it propagates out of this catch by design.
      redirect(`/login?error=1&callbackUrl=${encodeURIComponent(callbackUrl)}`);
    }
    // Everything else -- CallbackRouteError wrapping a CredentialCheckUnavailableError (KDF gone,
    // store corrupt, `insight` not importable), a config error, and signIn()'s own NEXT_REDIRECT
    // on SUCCESS -- must keep propagating. Rendering an operator failure as "invalid username or
    // password" would tell every user their correct password was wrong.
    throw error;
  }
}
