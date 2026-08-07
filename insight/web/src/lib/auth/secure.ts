// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 5.
//
// Verified directly against the pinned @auth/core@0.41.3's own source
// (src/lib/utils/cookie.ts:59-70, defaultCookies()): the session cookie's httpOnly:true and
// sameSite:"lax" are UNCONDITIONAL; only `secure` is conditional, on a boolean
// (`useSecureCookies`) this file's isSecureRequest() computes. next-auth's own "lazy
// initialization" config form (NextAuth((req) => ({...})), documented in next-auth/index.d.ts)
// is what lets auth.ts compute this PER REQUEST rather than once at process start -- see
// auth.ts's own comment for the wiring.
//
// Pure: a Headers-like object and an optional URL string in, a boolean out. Deliberately takes NO
// next/server import -- `Headers` is a Node/Fetch global, so this is testable with plain node,
// no TLS listener, no browser (dossier risk 3's injectable seam).
//
// SECURITY (independent security review of #307, BLOCKING): this function used to read
// `x-forwarded-proto` UNCONDITIONALLY. That header is client-suppliable -- any hop that does not
// overwrite it (a reverse proxy missing `proxy_set_header X-Forwarded-Proto $scheme;`, or the Next
// process reached directly) lets an attacker send `X-Forwarded-Proto: http` over a real HTTPS
// connection and strip `Secure` off the session cookie, which the browser will then attach to a
// plaintext http:// request. That is done-when 4 defeated by the single signal used to decide it.
//
// Two changes close it, and both are deliberately FAIL-CLOSED -- when we cannot tell, we set
// `Secure` and a genuinely-plaintext deployment breaks VISIBLY, rather than silently shipping a
// stealable cookie:
//   1. The forwarded header is honoured only behind an explicit, SERVER-side opt-in
//      (INSIGHT_TRUST_PROXY_PROTO=1), which is the operator asserting "a proxy I control always
//      overwrites this header". Trust must come from config, never from the request.
//   2. Absent that, only the server's OWN view of the URL counts, and plaintext is accepted as
//      legitimate only on a loopback host -- i.e. local `npm run dev`. Plaintext on any real
//      hostname still gets `Secure`.
// Deployment note in insight/web/README.md.

interface HeadersLike {
  get(name: string): string | null;
}

/** Only the one variable this function reads -- injectable so the proof needs no real env. */
interface EnvLike {
  INSIGHT_TRUST_PROXY_PROTO?: string;
}

// http:// is a legitimately-secure context only here (browsers treat loopback as trustworthy and
// accept `Secure` cookies on it either way, so this exists for dev ergonomics, not for safety).
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function isSecureRequest(input: {
  headers: HeadersLike;
  url?: string;
  env?: EnvLike;
}): boolean {
  const env = input.env ?? process.env;

  if (env.INSIGHT_TRUST_PROXY_PROTO === "1") {
    const forwardedProto = input.headers.get("x-forwarded-proto");
    // A proxy chain appends, so the CLIENT-nearest value is the first one ("https, http").
    if (forwardedProto !== null) {
      return forwardedProto.split(",")[0].trim().toLowerCase() === "https";
    }
  }

  if (input.url) {
    try {
      const parsed = new URL(input.url);
      if (parsed.protocol === "https:") return true;
      return !LOOPBACK_HOSTS.has(parsed.hostname.toLowerCase());
    } catch {
      return true; // unparseable -> cannot tell -> fail closed
    }
  }

  return true; // no signal at all -> fail closed
}
