"""Local board mirror — a token-free, gitignored snapshot of the GitHub backlog for the backlog
cross-check (0.9.20). ONE batched `gh issue list` for OPEN `sdlc:goal` issues + ONE for recently-closed
issues, normalized to the fields the cross-check index needs, written as NDJSON under
`.sdlc/state/board-mirror.ndjson` (which `/sdlc-setup` gitignores — issue titles/bodies can carry client
strings, so the mirror never rides a PR). Body excerpts are scrubbed too (defense in depth).

Read-only against GitHub; reaches it only through an injectable `run` (default `sources._run_gh`), so
tests are hermetic — no network, no `gh`. FAIL-OPEN: not github mode / no `gh` / offline / any error =>
no mirror written, returns None; the cross-check then degrades to the ledger + local goal files.

    python3 pipeline.py mirror .sdlc [--force]      # via the pipeline verb, or run this module directly
"""
import hashlib, importlib.util, json, pathlib, time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


scrub = _load("scrub").scrub

SCHEMA = "board-mirror/v1"
MIRROR_REL = "state/board-mirror.ndjson"          # under state/ => covered by RUNTIME_IGNORES (gitignored)
META_REL = "state/board-mirror.meta.json"
_EXCERPT_CHARS = 500                              # enough for an optional rerank; never the raw body
_FIELDS = "number,title,body,labels,state,closedAt,updatedAt"
_OPEN_LIMIT = 200                                 # mirrors next_pending's 200-issue cap
_DEFAULT_CLOSED_LIMIT = 200
_DEFAULT_TTL_MINUTES = 60


def _content_hash(title, excerpt, updated_at):
    """Change-detection key for the incremental index (slice 0.9.21): identical stored content +
    updated_at => identical hash => the engine skips re-tokenizing/re-embedding that issue."""
    return hashlib.sha256("\x00".join((title or "", excerpt or "", updated_at or "")).encode("utf-8")).hexdigest()[:16]


def normalize_issue(raw):
    """Pure: one `gh issue list --json` object -> the normalized, scrubbed mirror record. Raises
    (TypeError/ValueError) on a row with no usable number, so build_records can skip it."""
    number = int(raw.get("number"))               # raises on missing/garbage -> row dropped upstream
    title = scrub(str(raw.get("title") or ""))
    excerpt = scrub(str(raw.get("body") or ""))[:_EXCERPT_CHARS].strip()
    labels = [l.get("name") for l in (raw.get("labels") or [])
              if isinstance(l, dict) and l.get("name")]
    updated_at = raw.get("updatedAt") or ""
    return {"number": number,
            "title": title,
            "body_excerpt": excerpt,
            "labels": labels,
            "state": str(raw.get("state") or "").lower(),
            "closed_at": raw.get("closedAt") or None,
            "updated_at": updated_at,
            "content_hash": _content_hash(title, excerpt, updated_at)}


def build_records(open_raw, closed_raw):
    """Pure: raw open + closed issue lists -> normalized records, de-duplicated by number (an OPEN
    record wins over a closed one on the rare clash), sorted by number for deterministic output."""
    by_num = {}
    for raw in list(closed_raw or []) + list(open_raw or []):   # open appended last => wins the clash
        try:
            rec = normalize_issue(raw)
        except (TypeError, ValueError, AttributeError):
            continue                        # a non-dict / number-less row (str/None -> AttributeError on
        by_num[rec["number"]] = rec         # .get): skip it, keep the rest
    return [by_num[n] for n in sorted(by_num)]


def _load_config(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_github_mode(config):
    return ((config.get("discovery") or {}).get("source")) == "github"


def _paths(sdlc_dir):
    base = pathlib.Path(sdlc_dir)
    return base / MIRROR_REL, base / META_REL


def is_fresh(sdlc_dir, ttl_minutes, now=None):
    """True if a mirror exists and is younger than the TTL — the loop then skips the refetch."""
    _, meta_path = _paths(sdlc_dir)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(meta.get("mirrored_at", 0))
        return 0 <= age < ttl_minutes * 60
    except Exception:
        return False


def read_mirror(sdlc_dir):
    """Read the mirror back as a list of records (empty on absent/garbage). For the cross-check engine.
    Fail-open per line: a corrupt line is skipped, the rest survive."""
    mirror_path, _ = _paths(sdlc_dir)
    out = []
    try:
        text = mirror_path.read_text(encoding="utf-8")
    except Exception:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _write(sdlc_dir, records, repo, now=None):
    mirror_path, meta_path = _paths(sdlc_dir)
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    mirror_path.write_text(body, encoding="utf-8")
    meta = {"schema": SCHEMA, "repo": repo, "count": len(records),
            "mirrored_at": (time.time() if now is None else now)}
    meta_path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")


def fetch_and_write(sdlc_dir, config=None, run=None, now=None, force=False):
    """FAIL-OPEN orchestrator. Returns the record count written, or None when NO mirror was produced —
    not github mode / fresh-and-not-forced / gh missing / offline / bad config / unwritable .sdlc / ANY
    error. The whole body is guarded: this is a read-only pre-flight, so it must never raise into the
    loop's pick path (a later slice calls it there). A None return means "no mirror this run", never a crash."""
    try:
        config = config if config is not None else _load_config(sdlc_dir)
        if not is_github_mode(config):
            return None                                   # local-files mode: the goals dir IS the corpus
        gh = (config.get("discovery") or {}).get("github") or {}
        mirror_cfg = ((config.get("backlog_check") or {}).get("mirror")) or {}
        ttl = mirror_cfg.get("ttl_minutes", _DEFAULT_TTL_MINUTES)
        if not force and is_fresh(sdlc_dir, ttl, now=now):
            return None                                   # a fresh mirror amortizes the one API call
        run = run or _load("sources")._run_gh
        repo = gh.get("repo") or ""
        goal_label = gh.get("goal_label", "sdlc:goal")
        assignee = gh.get("assignee") or None
        closed_limit = int(mirror_cfg.get("closed_limit", _DEFAULT_CLOSED_LIMIT))   # bad value -> caught below
        repo_args = ["--repo", repo] if repo else []
        # The open query does NOT apply next_pending's extra parked-label exclusion. In practice `park`
        # also strips the goal label, so most parked issues fall out of this --label query anyway; one
        # that is parked-but-still-goal-labelled stays in the corpus as a valid dedup candidate.
        open_args = ["issue", "list", *repo_args, "--label", goal_label, "--state", "open",
                     "--json", _FIELDS, "--limit", str(_OPEN_LIMIT)]
        if assignee:
            open_args += ["--assignee", assignee]
        open_raw = json.loads(run(open_args) or "[]")
        # Closed issues are NOT goal-filtered: work completed in a prior (often manual) session can
        # obsolete an open goal even if it never carried the goal label. Bounded by closed_limit; the
        # `sort:updated-desc` search qualifier yields most-recently-updated-first (smoke-tested against
        # real gh — `--state closed` alone does NOT sort by recency). The engine applies the window later.
        closed_args = ["issue", "list", *repo_args, "--state", "closed",
                       "--search", "sort:updated-desc", "--json", _FIELDS,
                       "--limit", str(closed_limit)]
        closed_raw = json.loads(run(closed_args) or "[]")
        if not isinstance(open_raw, list) or not isinstance(closed_raw, list):
            return None                                   # a gh error object ({"message":...}) is not a
                                                          # backlog — don't clobber a good mirror with junk
        records = build_records(open_raw, closed_raw)
        _write(sdlc_dir, records, repo, now=now)
        return len(records)
    except Exception:
        return None                                       # fail-open backstop: no mirror, never a raise


def main(argv):
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    n = fetch_and_write(sdlc_dir, force=("--force" in argv))
    if n is None:
        print("mirror: skipped (not github mode, fresh, or gh unavailable)")
    else:
        print(f"mirror: wrote {n} issue(s) to {sdlc_dir}/{MIRROR_REL}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
