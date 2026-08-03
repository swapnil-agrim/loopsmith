# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Layer 2 of the collector adapter (issue #100, E1.S2): schema-keyed registry + persistence
into fact_collector_pack. See .sdlc/plans/100.md §A/§E for why this is split from collectors.py
and how an unrecognised schema is handled. No `import duckdb` here — conn is passed in already
open (insight/ingest/store.py owns the schema and the duckdb import)."""
import hashlib
import json
import re
import subprocess

from insight.ingest import collectors

#: A real SSH host alias for this org's own `Host github.com-<alias>` convention (see
#: _normalize_remote_url's own docstring) is a single label -- letters/digits/underscore/hyphen,
#: no further host boundary -- never itself a dotted DNS-style hostname. `github.com-work` and
#: `github.com-mirror` match; `github.com-mirror.example.net` (a genuinely different, real
#: domain that merely starts with the same 11 characters) does not, because it contains a
#: literal '.'. BLOCKING finding from independent PR review: a bare `str.startswith
#: ("github.com-")` check (this function's own first-draft shape) does not make this
#: distinction and silently collapsed the two onto the same project_id -- reproduced live, this
#: session, with two real fixture repos. See .sdlc/plans/106.md Design decision B.
_GITHUB_ALIAS_RE = re.compile(r"^github\.com-[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?$")

#: Mirrors collectors.py/git_reader.py/gh_reader.py's own constant of the same name.
_TIMEOUT_SECS = 300

#: Lands in fact_collector_pack.degraded_adapter when payload carries a schema string with no
#: registered normaliser below. Never raised — see ingest_collectors.
ADAPTER_UNKNOWN_SCHEMA = "adapter_unknown_schema"
#: The catch-all: something in this adapter itself broke on one source. Distinct from every
#: other code, which describes the COLLECTOR's behaviour — this one accuses our own code, so a
#: reader can tell "the collector misbehaved" from "we have a bug".
ADAPTER_INTERNAL_ERROR = "adapter_internal_error"

_EMPTY_WINDOW = {
    "window_since_days": None, "window_oldest_sha": None, "window_oldest_date": None,
    "window_newest_sha": None, "window_newest_date": None, "window_commit_count": None,
    "window_merge_count": None,  # issue #103 -- only git-facts/v1 ever populates this
    "window_pr_count": None, "window_review_event_count": None,
    "window_check_row_count": None,  # issue #104 -- only gh-facts/v1 ever populates these
}


def _as_list(value):
    return value if isinstance(value, list) else []


def _normalize_alignment_collect(payload):
    window = payload.get("window") or {}
    oldest = window.get("oldest") or {}
    newest = window.get("newest") or {}
    return {
        "window_since_days": window.get("since_days"),
        "window_oldest_sha": oldest.get("sha") or None,
        "window_oldest_date": oldest.get("date") or None,
        "window_newest_sha": newest.get("sha") or None,
        "window_newest_date": newest.get("date") or None,
        "window_commit_count": window.get("commit_count"),
        "window_merge_count": None,  # alignment-collect/v1 has no merge concept -- issue #103
        "window_pr_count": None, "window_review_event_count": None,
        "window_check_row_count": None,  # no PR concept -- issue #104
        # list() on a string explodes it per-character, so a collector regressing to
        # "degraded": "oops" would store four codes named o, o, p, s — no crash, silent
        # garbage. Only a real list is trusted; anything else reads as no codes.
        "degraded_collector": [str(c) for c in _as_list(payload.get("degraded"))],
    }


def _normalize_git_facts(payload):
    """git-facts/v1 (issue #103, E1.S5): git_reader.py's own in-process pack -- same window
    shape as alignment-collect/v1 (oldest/newest sha+date, commit_count) PLUS merge_count, the
    one field no other schema's payload carries. See .sdlc/plans/103.md §A/§C."""
    window = payload.get("window") or {}
    oldest = window.get("oldest") or {}
    newest = window.get("newest") or {}
    return {
        "window_since_days": window.get("since_days"),
        "window_oldest_sha": oldest.get("sha") or None,
        "window_oldest_date": oldest.get("date") or None,
        "window_newest_sha": newest.get("sha") or None,
        "window_newest_date": newest.get("date") or None,
        "window_commit_count": window.get("commit_count"),
        "window_merge_count": window.get("merge_count"),
        "window_pr_count": None, "window_review_event_count": None,
        "window_check_row_count": None,  # no PR concept -- issue #104
        "degraded_collector": [str(c) for c in _as_list(payload.get("degraded"))],
    }


def _normalize_gh_facts(payload):
    """gh-facts/v1 (issue #104, E1.S6): gh_reader.py's own in-process pack -- the record of
    whether `gh` was reachable at all (degraded_collector carries a gh_* code on failure -- see
    .sdlc/plans/104.md Design decision B) plus summary counts of what was actually written to
    fact_pr_review/fact_pr_check this run. No commit/merge concept -- those stay NULL."""
    window = payload.get("window") or {}
    oldest = window.get("oldest") or {}
    newest = window.get("newest") or {}
    return {
        "window_since_days": window.get("since_days"),
        "window_oldest_sha": oldest.get("sha") or None,
        "window_oldest_date": oldest.get("date") or None,
        "window_newest_sha": newest.get("sha") or None,
        "window_newest_date": newest.get("date") or None,
        "window_commit_count": None, "window_merge_count": None,
        "window_pr_count": window.get("pr_count"),
        "window_review_event_count": window.get("review_event_count"),
        "window_check_row_count": window.get("check_row_count"),
        "degraded_collector": [str(c) for c in _as_list(payload.get("degraded"))],
    }


def _normalize_windowless(payload):
    """discovery-scan/v1 and pipeline-card/v1: neither has a window or degraded[] concept —
    verified by reading both sources (discovery-scan.sh's only two emit points, and
    build_card()'s return keys). Always NULL window, always empty degraded_collector."""
    out = dict(_EMPTY_WINDOW)
    out["degraded_collector"] = []
    return out


def _normalize_claude_analytics(payload):
    """claude-analytics/v1 (issue #146, E7.S3): insight/ingest/analytics_reader.py's own
    in-process pack -- a NEW HYBRID shape, not a copy of either existing candidate, verified by
    reading both directly this session (.sdlc/plans/146.md Design decision 11):

    - It has no commit/PR-window concept at all (no oldest/newest sha+date, no commit_count,
      no PR/review/check counts) -- matching _normalize_windowless's own reasoning for
      discovery-scan/v1 and pipeline-card/v1, so all six window_* fields are NULL here too.
    - UNLIKE _normalize_windowless, it does NOT hardcode degraded_collector = [] -- that
      function never reads its payload at all (verified: no payload.get(...) call anywhere in
      it), so if this normalizer copied it literally, every claude-analytics/v1 pack row would
      report "nothing wrong" forever regardless of what ingest_analytics_reader actually
      degraded to -- a normalizer that always says "nothing wrong" would make insight/gaps/
      coverage_degraded_collector.sql (which filters on len(degraded_collector) > 0)
      permanently blind to this collector's real state, the exact failure mode the
      no-fabricated-signal doctrine forbids. (--claude-analytics defaults OFF, and a
      non-adopter's `insight ingest` run now writes NO claude-analytics/v1 row at all rather
      than one claiming "disabled" -- see insight.ingest.analytics_reader's own module
      docstring and insight.__main__._ingest_one_repo for the caller-side gate; this
      normalizer only ever runs when the reader itself was actually called.)
    - Instead, degraded_collector is read FOR REAL from the payload, exactly like
      _normalize_gh_facts does (payload.get("degraded"), guarded by _as_list against a
      collector that regresses to a bare string instead of a list -- see _as_list's own
      docstring: list("oops") explodes per-character with no crash, silent garbage, which only
      a real-list check prevents)."""
    out = dict(_EMPTY_WINDOW)
    out["degraded_collector"] = [str(c) for c in _as_list(payload.get("degraded"))]
    return out


_NORMALIZERS = {
    "alignment-collect/v1": _normalize_alignment_collect,
    "discovery-scan/v1": _normalize_windowless,
    "pipeline-card/v1": _normalize_windowless,
    "git-facts/v1": _normalize_git_facts,
    "gh-facts/v1": _normalize_gh_facts,
    "claude-analytics/v1": _normalize_claude_analytics,
}


def normalize(schema, payload):
    """(fields_dict, extra_adapter_codes) for one already-run result. payload may be None (the
    collector never produced JSON — see collectors.run_source); an unrecognised schema is
    recorded, never raised. fields_dict always has all six window_* keys plus
    'degraded_collector'."""
    if payload is None:
        return dict(_EMPTY_WINDOW, degraded_collector=[]), []
    fn = _NORMALIZERS.get(schema)
    if fn is None:
        return dict(_EMPTY_WINDOW, degraded_collector=[]), [ADAPTER_UNKNOWN_SCHEMA]
    return fn(payload), []


_INSERT_SQL = """
    INSERT INTO fact_collector_pack
      (project_id, schema, collected_ts, window_since_days, window_oldest_sha,
       window_oldest_date, window_newest_sha, window_newest_date, window_commit_count,
       window_merge_count, window_pr_count, window_review_event_count,
       window_check_row_count, degraded_collector, degraded_adapter, raw_payload)
    VALUES (?, ?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def write_pack(conn, project_id, schema, fields, degraded_adapter, raw_payload):
    """One INSERT, one row. degraded_collector/degraded_adapter are always explicit lists --
    never None. window_merge_count is None except for git-facts/v1 (#103); window_pr_count/
    window_review_event_count/window_check_row_count are None except for gh-facts/v1 (#104)."""
    conn.execute(_INSERT_SQL, [
        project_id, schema,
        fields["window_since_days"], fields["window_oldest_sha"], fields["window_oldest_date"],
        fields["window_newest_sha"], fields["window_newest_date"], fields["window_commit_count"],
        fields["window_merge_count"], fields["window_pr_count"],
        fields["window_review_event_count"], fields["window_check_row_count"],
        list(fields["degraded_collector"]), list(degraded_adapter), raw_payload,
    ])


def _git_remote_url(project_root, timeout=_TIMEOUT_SECS):
    """One `git -C <project_root> remote get-url origin` call -> stripped stdout, or None on
    ANY failure. A dedicated, PRIVATE, single-purpose wrapper -- NOT a promotion of
    git_reader.py's own _run_git, because packs.py cannot import git_reader.py without a
    circular import (git_reader.py already imports project_id_for FROM packs.py). Every other
    module in this codebase (collectors.py, git_reader.py, gh_reader.py) already owns its own
    small subprocess wrapper rather than sharing one -- this follows that same, already
    established convention, not a new one."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _normalize_remote_url(url):
    """Canonicalize a git remote URL for hashing: strip protocol/userinfo, ssh scp-syntax
    colon -> slash (ONLY when no scheme was present -- a scheme'd URL's colon is a PORT, not a
    path start), an explicit port (dropped -- see the port-drop paragraph below for why this is
    an ACCEPTED, bounded residue rather than a silent oversight), this org's own
    github.com-<alias> SSH host-alias convention (the SAME one skills/sdlc-setup/scripts/
    setup.py:detect_repo() already special-cases -- reimplemented, never imported, per the
    plugin/product boundary; see _GITHUB_ALIAS_RE for why the match is a real alias LABEL, not
    a bare string-prefix test), trailing .git.

    THE GOVERNING PRINCIPLE, applied to every transform in this function, not only the one
    branch that first prompted writing it down: collapsing two GENUINELY DIFFERENT remotes onto
    the same normalized string is SILENT, undetectable cross-project data corruption --
    strictly worse than failing to collapse two representations of the true SAME remote (which
    only produces a visible, duplicate dim_project row). Every transform below is audited
    against that asymmetry, not assumed safe by convenience:

    - Case-folding is HOST-ONLY. A first-draft version of this function lowercased the entire
      result (host AND path) -- non-blocking finding from independent code review, fixed. DNS
      hostnames are case-insensitive by spec (RFC 3986 section 3.2.2), so folding host case is
      always correct. The PATH is a different claim: GitHub/GitLab happen to treat an
      owner/repo path as effectively case-insensitive, but nothing in the git protocol
      guarantees that for every host, and a case-sensitive self-hosted server could have two
      genuinely different repositories whose paths differ only in case. Preserving path case
      is the safer side of the asymmetry above.
    - The github.com-<alias> fold is gated on `_GITHUB_ALIAS_RE`, NOT a bare
      `str.startswith("github.com-")` -- BLOCKING finding from independent PR review,
      reproduced live (two real fixture repos, no clone/network) then fixed: a bare prefix test
      folded `github.com-mirror.example.net` (a genuinely different, real self-hosted domain
      that merely starts with the same 11 characters) onto `github.com`, producing an IDENTICAL
      project_id for two unrelated repos. A real SSH alias for this convention is a single
      label with no further host boundary (no dots) -- `_GITHUB_ALIAS_RE` requires exactly that.
    - The explicit-port drop (below) is the ONE place this function still accepts the
      corruption-side of the asymmetry, DELIBERATELY, not silently: two genuinely different
      services hosted on the same hostname at different non-standard ports (e.g. two separate
      self-hosted git instances on git.example.com:2222 and git.example.com:3333) normalize to
      the SAME string. Accepted because this is a rare topology, and getting the COMMON case
      right (an explicit default port vs. an omitted one for the SAME service) matters more
      than guarding a rare one -- see .sdlc/plans/106.md Design decision B, where this was
      first named as a residue, not an oversight.
    - Scheme-prefix stripping, userinfo-stripping (last `@`, matching URL-authority syntax),
      and the SCP-shorthand detection (`"/" not in u.split(":", 1)[0]`, matching git's own real
      parsing rule for when scp-like syntax is recognized) all operate on syntax that provably
      means the same thing regardless of which form was used -- none of them fold two
      DIFFERENT authorities together, so none carry this same risk.

    file:// and bare local filesystem paths (e.g. `git remote add origin /srv/repos/x.git`) are
    explicitly OUT OF SCOPE -- returns None. Such a 'remote' is itself just another path on
    disk, with the exact same non-portability problem project_id_for's own path-hash FALLBACK
    already has; hashing it would buy no more stability than the fallback the caller already
    falls through to when this returns None. See .sdlc/plans/106.md Design decision B
    (non-blocking item 3).

    A query-string/fragment remote (e.g. `https://github.com/o/r.git?token=x`, not a shape
    `git remote get-url` ever actually emits) leaves the trailing `.git` unstripped --
    `github.com/o/r.git?token=x` rather than `github.com/o/r`. Deliberately not handled:
    this is an UNDER-collision (two representations of the true same repo fail to collapse),
    the safe direction per the asymmetry above, for an input shape this function's real caller
    (git itself) does not produce. See this issue's own PR review, non-blocking item 2."""
    u = url.strip()
    if u.startswith("file://") or u.startswith("/"):
        return None
    had_scheme = False
    for prefix in ("https://", "http://", "ssh://", "git://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            had_scheme = True
            break
    if not had_scheme and ":" in u and "/" not in u.split(":", 1)[0]:
        # SCP-style shorthand ([user@]host:path) -- ONLY valid with no scheme. Rewrite
        # host:path -> host/path so the rest of this function treats every form uniformly.
        host_part, _, path_part = u.partition(":")
        u = host_part + "/" + path_part
    # From here on, u is always "authority/path...", whether from a real scheme, the SCP
    # rewrite above, or a bare host/path input with no colon at all -- so authority (and any
    # userinfo/port within it) can be isolated the SAME way regardless of origin form.
    authority, _, rest = u.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    if ":" in authority:
        # only reachable when had_scheme (the SCP branch above already removed any
        # authority-level colon for the no-scheme case) -- a port number, e.g. ssh://host:22/...
        authority = authority.split(":", 1)[0]
    # Fold BEFORE rejoining with `rest` (the path) -- host case only, never the path. Also
    # folds BEFORE the github.com-<alias> check below, so an aliased host typed in any case
    # (e.g. "GitHub.com-work") still matches the "github.com-" prefix test.
    authority = authority.lower()
    u = authority + ("/" + rest if rest else "")
    if u.endswith(".git"):
        u = u[:-4]
    parts = u.split("/", 1)
    if len(parts) == 2 and _GITHUB_ALIAS_RE.match(parts[0]):
        u = "github.com/" + parts[1]
    return u.rstrip("/")


def remote_identity_for(project_root, timeout=_TIMEOUT_SECS):
    """(repo, remote_url_sha256) for project_root's `origin` remote -- (None, None) when there
    is no remote, project_root is not a git repo, git itself is unusable, or the call times out.
    `repo` is the NORMALIZED remote string (e.g. "github.com/owner/repo"), for dim_project.repo;
    remote_url_sha256 is its full 64-hex-char sha256, for dim_project.remote_url_sha256 --
    spec's own long-NULL column, populated for real by this story."""
    raw = _git_remote_url(project_root, timeout=timeout)
    if not raw:
        return None, None
    normalized = _normalize_remote_url(raw)
    if not normalized:
        return None, None
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def project_id_for(project_root):
    """UNCHANGED SIGNATURE, NEW derivation (issue #106): sha256(remote_url_sha256)[:16] when
    project_root's `origin` remote resolves; else the EXACT PRE-#106 FORMULA,
    sha256(str(project_root.resolve()))[:16] -- preserved byte-for-byte, unprefixed, as the
    fallback. See .sdlc/plans/106.md's answers to Research Q2 (this fallback) and Q3 (why
    keeping it identical means a remote-less repo's project_id does NOT change across this
    story's boundary -- only a repo WITH a remote gets a new id)."""
    _, remote_url_sha256 = remote_identity_for(project_root)
    if remote_url_sha256:
        return remote_url_sha256[:16]
    return hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]


def ingest_collectors(conn, project_root, collectors_root=None):
    """Run every collectors.SOURCES entry against project_root, normalise, and persist ONE
    fact_collector_pack row per source — always, even on total failure. Raises for nothing a
    COLLECTOR can do; a store connection that cannot accept a row of constants is still fatal,
    because there would be nowhere left to record the fact. Returns a
    list of {'name', 'schema', 'degraded_collector', 'degraded_adapter'} for CLI printing.
    Repeated calls APPEND rows (this is a fact/log table, not upserted) — see
    test_ingest_collectors_appends_not_upserts_on_repeated_runs."""
    project_id = project_id_for(project_root)
    resolved_root = collectors.resolve_collectors_root(collectors_root)
    results = []
    for source in collectors.SOURCES:
        # "Never fatal" is a promise about EVERY malformed thing a collector can emit, not
        # only the ones enumerated below — and a collector is a subprocess whose output this
        # code does not control. Without this guard one bad source aborts the loop and NO
        # source gets a row, which is strictly worse than the unknown-schema case the story
        # asks us to survive. Caught once, here, rather than re-guarded at every call site.
        #
        # The WRITE is inside the try on purpose. Normalising cannot type-check every field a
        # collector invents, so a well-typed `schema` with, say, an object where window.since_days
        # should be an int reaches DuckDB and raises ConversionException at INSERT — after the
        # earlier guard, and just as fatal. A failed INSERT writes no row, so the fallback below
        # cannot duplicate one.
        try:
            run = collectors.run_source(source, project_root, resolved_root)
            fields, extra_adapter_codes = normalize(run["schema"], run["payload"])
            schema = run["schema"]
            degraded_adapter = list(run["degraded_adapter"]) + extra_adapter_codes
            raw_payload = json.dumps(run["payload"]) if run["payload"] is not None else None
            write_pack(conn, project_id, schema, fields, degraded_adapter, raw_payload)
        except Exception:
            # Every value here is a constant, so this write cannot fail for any reason the first
            # one did — only a broken store connection, which no degraded code could record.
            schema = source.expected_schema
            fields = dict(_EMPTY_WINDOW, degraded_collector=[])
            degraded_adapter = [ADAPTER_INTERNAL_ERROR]
            write_pack(conn, project_id, schema, fields, degraded_adapter, None)
        results.append({
            "name": source.name, "schema": schema,
            "degraded_collector": fields["degraded_collector"],
            "degraded_adapter": degraded_adapter,
        })
    return results
