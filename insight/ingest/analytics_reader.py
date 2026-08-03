# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Claude Code Analytics API reader (issue #146, E7.S3): an OPTIONAL, opt-in, degrade-only
ingest path for the Analytics/Admin Usage API -- disabled by default, and, per spec section 11
(docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md:649-652), "requires
an Admin API key and an org account; unavailable on Bedrock, Foundry, Vertex, and Claude Platform
on AWS, so it must be optional and degrade silently."

NOT INVOKED AT ALL UNLESS `--claude-analytics` WAS PASSED -- `insight.__main__._ingest_one_repo`
gates the call itself; this module has no `enabled` concept of its own to short-circuit on
(FIXED live, post-#146: an earlier revision had this module accept an `enabled` flag and write
an `analytics_disabled`-degraded row on every run regardless, which permanently dragged every
non-adopter's `insight gaps` verdict from PASS to WARN via insight/gaps/coverage_degraded_
collector.sql -- a collector that was never asked to run has no coverage story to tell, so the
fix is to never write a row for it at all, not to write one that says "disabled"). A non-adopter's
`insight ingest` run therefore never calls this module, never reads the env var, and never writes
a claude-analytics/v1 pack row.

NEVER RAISES. Mirrors insight.ingest.gh_reader.ingest_gh_reader's own contract end to end,
including the same outer try/except Exception defense-in-depth around the fetch call (see that
module's own docstring, citing the #103 incident this pattern exists to prevent). Every failure
mode -- the key being absent, an auth/permission/rate-limit HTTP status, a plain network/DNS
failure, a timeout, a malformed or unexpected-shape payload, or anything else unforeseen --
collapses to a named degrade code on the ONE fact_collector_pack row (schema="claude-analytics/v1")
this module writes EXACTLY ONCE EVERY TIME IT ACTUALLY RUNS (i.e. every time
ingest_analytics_reader is called at all -- see the paragraph above for why that is no longer
"always" at the `insight ingest` level). See .sdlc/plans/146.md Design decision 10 for the full
vocabulary table.

THIS READER IS EXPERIMENTAL AND *NOT VERIFIED END-TO-END* AGAINST THE REAL CLAUDE CODE ANALYTICS
API, per this project's own standing rule against untested support claims. Three specific things
in this file are best-guesses, not confirmed facts, flagged individually below and left honest
about it rather than silently presented as settled:

  1. The env var name for the Admin API key, ANTHROPIC_ADMIN_API_KEY (module default for
     `api_key_env`) -- follows the existing ANTHROPIC_API_KEY naming convention but is NOT
     independently confirmed against Anthropic's own published Admin API docs (grepped this
     repo for any existing reference: zero hits). See .sdlc/plans/146.md Design decision 8.
  2. _USAGE_ENDPOINT below -- a plausible URL shape for an organization-scoped usage report,
     not verified against a real response from Anthropic's Admin API.
  3. The request headers built in fetch_usage (`x-api-key` + `anthropic-version`) -- the
     `x-api-key` header name is the one documented convention for the regular Messages API;
     whether the Admin/Analytics API uses the identical header name and versioning scheme is
     assumed, not confirmed live this session.

A future author landing real verification should update this docstring (and the three
call-sites named above) to say so plainly, rather than deleting this section silently.

Zero DuckDB in this module's transport/classification layer -- conn is passed in already open by
`ingest_analytics_reader`, the one function in this file that touches the store, same convention
as gh_reader.py/git_reader.py/packs.py.

TRANSPORT IS INJECTABLE (`transport=` parameter, default None -> the real urllib-based call) --
mirrors gh_reader.py's own `binary=` DI parameter for tests, adapted from "fake executable" to
"fake callable" because HTTP has no subprocess to stub. A `transport(url, headers, timeout)`
callable returns `(status_code, body_bytes)` on ANY outcome the caller wants to represent as "the
HTTP round trip completed" (including a non-2xx status -- see fetch_usage's own dual-path
handling below for why), or raises the specific exception a genuine transport-level failure would
raise (`TimeoutError`/`socket.timeout`, `urllib.error.URLError`, `urllib.error.HTTPError`). Every
hermetic test in this module's own test suite (insight/tests/test_analytics_reader.py) uses this
injection -- never a real socket. See .sdlc/plans/146.md Design decision 9/12.

HERMETICITY NOTE, worth repeating here because it is easy to get wrong on a real developer
machine: this module reads `os.environ.get(api_key_env)` at CALL TIME, not at import time. A
developer who genuinely exports a real Admin API key in their shell must never have this
module's own test suite silently take the real-transport path -- every "key absent" test in
insight/tests/test_analytics_reader.py explicitly `monkeypatch.delenv(api_key_env, raising=False)`
before the call under test, rather than relying on the ambient environment happening to be clean.
"""
import datetime
import http.client
import json
import os
import pathlib
import socket
import urllib.error
import urllib.parse
import urllib.request

from insight.ingest.packs import ADAPTER_INTERNAL_ERROR, normalize, project_id_for, write_pack

#: HTTP call, not a local subprocess -- gh_reader.py's/collectors.py's shared 300s figure is
#: calibrated for a CLI subprocess that can itself be slow to start (a cold `gh`/`git` process);
#: a REST call over an already-open connection has no comparable startup cost, so a tighter,
#: deliberately DIFFERENT bound is used here rather than copying that constant.
_TIMEOUT_SECS = 30

#: UNVERIFIED GUESS -- see the module docstring's numbered list, item 2. Not confirmed against a
#: real Anthropic Admin API response in this session.
_USAGE_ENDPOINT = "https://api.anthropic.com/v1/organizations/usage_report/messages"

#: UNVERIFIED GUESS -- see the module docstring's numbered list, item 3.
_API_VERSION = "2023-06-01"

#: Best-effort, UNVERIFIED substrings for classifying "this account/key is not on the direct
#: Anthropic API" from a shallow parse of an error response body -- see
#: _looks_non_anthropic_platform's own docstring for why this is classified from the response,
#: never from a pre-flight environment-variable guess about which of Bedrock/Vertex/Foundry/AWS
#: platform is in play. Not verified against any real non-Anthropic-platform response this
#: session; a future author with access to a real one should replace this list with confirmed
#: signal rather than silently trusting it.
_PLATFORM_HINT_SUBSTRINGS = (
    "bedrock", "vertex", "foundry", "not supported on this platform",
    "not available for this account type", "third-party platform", "unsupported platform",
)


def _classify_analytics_response(status, body_text):
    """(status, body_text) -> one degrade code, never raises. Detection order, most-reliable
    first, mirrors gh_reader._classify_gh_failure's own governing principle (an HTTP status is
    the equivalent of gh's own exit-4-is-trustworthy signal; anything the status alone cannot
    disambiguate falls to a body-text match, least-reliable, catch-all last). See .sdlc/plans/
    146.md Design decision 10's table for the full vocabulary and .sdlc/plans/146.md Design
    decision 10's own caveat paragraph for why analytics_non_anthropic_platform is classified
    from the response body rather than from a pre-flight env var guess."""
    if status == 401:
        return "analytics_unauthenticated"
    if status == 403:
        return "analytics_forbidden"
    if status == 429:
        return "analytics_rate_limited"
    if _looks_non_anthropic_platform(body_text):
        return "analytics_non_anthropic_platform"
    return "analytics_http_error"


def _looks_non_anthropic_platform(body_text):
    """A SHALLOW parse of the general Anthropic API error envelope shape
    (`{"type": "error", "error": {"type": ..., "message": ...}}`), checked against
    _PLATFORM_HINT_SUBSTRINGS -- never a crash on a malformed/non-JSON body (falls back to
    matching the raw text). Best-effort and explicitly flagged unverified -- see the module
    docstring and _PLATFORM_HINT_SUBSTRINGS' own comment."""
    try:
        data = json.loads(body_text) if body_text else {}
    except (ValueError, TypeError):
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    error_type = (error.get("type") if isinstance(error, dict) else None) or \
        (data.get("type") if isinstance(data, dict) else None) or ""
    message = (error.get("message") if isinstance(error, dict) else None) or ""
    text = ("%s %s %s" % (error_type, message, body_text or "")).lower()
    return any(hint in text for hint in _PLATFORM_HINT_SUBSTRINGS)


def _read_error_body(http_error):
    """HTTPError.read() -> a decoded str, never raising even if the body is already consumed or
    unreadable (an HTTPError's file-like object can only be read once; a caller that already
    drained it, or a truncated response, must not turn a classification attempt into a second,
    unrelated crash)."""
    try:
        raw = http_error.read()
    except Exception:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


def _http_get(url, headers, timeout):
    """The real transport, used only when `transport=None`. A thin urllib.request.urlopen
    wrapper -- deliberately does NOT catch anything itself; every exception urlopen can raise
    (HTTPError, URLError, TimeoutError/socket.timeout, http.client.HTTPException) propagates to
    fetch_usage, which owns the full classification per Design decision 10. On a real 2xx
    response, returns (status_code, body_bytes)."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read()


def _usage_url(days):
    """Build the usage-report URL for a trailing `days`-day window. Raises OverflowError/
    ValueError/TypeError/OSError for a `days` value that overflows datetime arithmetic --
    mirrors gh_reader._pr_list_args's own unguarded-arithmetic shape exactly (same bug class,
    same fix location one level up in fetch_usage); NOT guarded here, deliberately, so the
    caller can distinguish "the window value itself was bad" the same way gh_reader.fetch_prs
    does. See .sdlc/plans/146.md Design decision 9's own citation of that precedent."""
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    query = urllib.parse.urlencode({"start_date": since.strftime("%Y-%m-%d")})
    return "%s?%s" % (_USAGE_ENDPOINT, query)


def fetch_usage(api_key, days, transport=None):
    """(payload_dict_or_None, degrade_code_or_None). NEVER RAISES -- every branch below either
    returns a real payload with no code, or None with a named code. See .sdlc/plans/146.md
    Design decision 10's table for the full vocabulary this function (together with
    _classify_analytics_response) is responsible for producing.

    Two independent ways a non-2xx outcome can reach this function, BOTH handled: the real
    transport (_http_get, via urlopen) raises urllib.error.HTTPError automatically for any
    non-2xx status, per urllib's own documented behaviour -- caught below. An INJECTED test
    transport, per its own DI contract (module docstring), is free to represent a non-2xx
    outcome either the same way (by raising HTTPError itself) OR by simply returning
    `(status_code, body_bytes)` without raising -- so the success path below also re-checks
    `status` before trusting the body as a real payload, rather than assuming a returned tuple
    always means 2xx. This dual handling is deliberate, not redundant: it lets any correctly
    written transport -- real or fake -- reach the identical classification regardless of which
    of the two equally legitimate shapes it chooses for reporting an HTTP-level failure."""
    try:
        url = _usage_url(days)
    except (OverflowError, ValueError, TypeError, OSError):
        return None, "analytics_invalid_window_days"

    headers = {"x-api-key": api_key, "anthropic-version": _API_VERSION}

    try:
        if transport is not None:
            status, body = transport(url, headers, _TIMEOUT_SECS)
        else:
            status, body = _http_get(url, headers, _TIMEOUT_SECS)
    except urllib.error.HTTPError as e:
        return None, _classify_analytics_response(e.code, _read_error_body(e))
    except (TimeoutError, socket.timeout):
        return None, "adapter_timeout"
    except urllib.error.URLError:
        # Non-HTTP transport failure -- DNS/connection refused/etc. No subprocess analog exists
        # in gh_reader.py for this (a spawned `gh` binary either runs or fails to spawn; there is
        # no "spawned fine, then the network itself broke" state for a local subprocess the way
        # there is for a real socket). See .sdlc/plans/146.md Design decision 10's table.
        return None, "analytics_network_error"
    except http.client.HTTPException:
        # e.g. IncompleteRead -- urlopen does NOT always wrap this into urllib.error.URLError, so
        # it is a real, distinct exception shape a truncated response can raise. Not given its
        # own degrade code (see the module's own vocabulary table); falls through with every
        # other unforeseen failure below. Named here explicitly, not merely caught by the bare
        # `except Exception` beneath it, so the "never raises" claim is honest about every
        # exception class actually observed and reasoned about, not just the ones with bespoke
        # codes -- see .sdlc/plans/146.md Design decision 9.
        return None, ADAPTER_INTERNAL_ERROR
    except Exception:
        # Anything else unforeseen in the transport layer itself.
        return None, ADAPTER_INTERNAL_ERROR

    if status is None or not (200 <= status < 300):
        body_text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) \
            else (body or "")
        return None, _classify_analytics_response(status, body_text)

    try:
        text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) \
            else str(body)
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, "adapter_output_not_json"
    if not isinstance(data, dict):
        # A shallow shape check -- valid JSON that is not an object (e.g. a bare list or number)
        # is not a usable payload either. Mirrors gh_reader.fetch_prs's own identical dual-purpose
        # use of adapter_output_not_json for "not JSON at all" AND "JSON but the wrong shape".
        return None, "adapter_output_not_json"
    return data, None


def analytics_payload(days, degraded):
    """Build the claude-analytics/v1 payload handed to packs.normalize/write_pack -- the ONE row
    that always writes, whether or not the API was ever reached. See
    insight.ingest.packs._normalize_claude_analytics for how `degraded` is read back out of
    this shape (REAL degrade codes, never hardcoded empty -- .sdlc/plans/146.md Design
    decision 11)."""
    return {"schema": "claude-analytics/v1", "window_days": days, "degraded": list(degraded)}


def ingest_analytics_reader(conn, project_root, sdlc_dir=None, days=14,
                             api_key_env="ANTHROPIC_ADMIN_API_KEY", transport=None):
    """The orchestration `insight ingest` calls ONLY when `--claude-analytics` is passed (issue
    #146, E7.S3) -- the caller (insight.__main__._ingest_one_repo) is the gate; this function no
    longer has an `enabled` parameter of its own to short-circuit on, because a non-adopter's
    `insight ingest` run must never so much as call this function, let alone look for a key it
    was never asked to use or write a row claiming a collector that never ran. (FIXED post-#146:
    an earlier revision took an `enabled` flag here and wrote an `analytics_disabled`-degraded
    row on every run regardless of the flag -- see the module docstring's own account of the
    incident that caused, and .sdlc/plans/146.md's follow-up fix, for why the gate moved up to
    the caller instead.)

    NEVER RAISES; ALWAYS writes exactly one fact_collector_pack row (schema="claude-analytics/v1")
    EVERY TIME IT IS CALLED. Checked in this exact order, cheapest/most-certain first, each one
    short-circuiting the rest -- mirrors gh_reader.ingest_gh_reader's own layered contract:

      1. The env var (`api_key_env`, default "ANTHROPIC_ADMIN_API_KEY") -- unset OR blank (empty/
         whitespace-only) degrades to analytics_no_key, before any network call.
      2. fetch_usage itself, inside an outer `try/except Exception` -> adapter_internal_error
         (defense in depth, mirrors gh_reader.ingest_gh_reader's own outer guard around
         fetch_prs, citing the same #103-incident reasoning: fetch_usage already guards its own
         known failure modes, this is deliberately for anything ELSE, unforeseen).

    `sdlc_dir` is accepted for signature symmetry with gh_reader.ingest_gh_reader/git_reader's
    own ingest_* functions (every other reader in this ingest layer takes it, even ones -- like
    this one -- that do not currently read anything from it); unused today, reserved for a
    future per-repo config knob (e.g. a project-level opt-out) without another signature churn.

    sdlc_dir/project_root are NOT currently used to discover a repo-scoped identifier the way
    gh_reader._repo_from_config reads .sdlc/config.json's discovery.github.repo -- the Analytics
    API is ORG-scoped, not repo-scoped (per spec section 11, "requires an Admin API key and an
    org account"), so there is no per-repo config value to look up here."""
    project_root = pathlib.Path(project_root)
    project_id = project_id_for(project_root)

    api_key = os.environ.get(api_key_env)
    if not api_key or not api_key.strip():
        top_code = "analytics_no_key"
    else:
        try:
            _payload, top_code = fetch_usage(api_key, days, transport=transport)
        except Exception:
            # fetch_usage's own docstring claims never-raises; this is defense in depth for
            # anything that claim turns out to be wrong about, mirroring
            # gh_reader.ingest_gh_reader's own outer guard around fetch_prs.
            top_code = ADAPTER_INTERNAL_ERROR

    payload = analytics_payload(days, [top_code] if top_code else [])
    fields, extra_adapter_codes = normalize(payload["schema"], payload)
    write_pack(conn, project_id, payload["schema"], fields, extra_adapter_codes, json.dumps(payload))
    return {
        "schema": payload["schema"],
        "degraded": fields["degraded_collector"] + extra_adapter_codes,
    }
