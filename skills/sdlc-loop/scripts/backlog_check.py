"""Pre-work backlog cross-check (0.9.21) — the LLM-free stage-1 retrieval that, given a just-picked
goal, surfaces likely DUPLICATES / BLOCKERS / OBSOLETE-BY-completed-work against the rest of the
backlog + in-flight team work, so the loop doesn't spend a full Research/Plan cycle on redundant work.

Zero LLM tokens: a stdlib TF-IDF cosine over a candidate set of issues that share ≥1 term with the goal
(rarity then drives the SCORE via idf, not membership) — exact at a few-hundred-issue scale, no
MinHash/LSH needed — plus an explicit `#N`-reference graph and the team
ledger. It EMITS EVIDENCE, renders no verdict and takes no action — a later slice's loop hook decides
whether to park-with-proof or annotate, and only THAT stage may (optionally) hand the top-K flagged
pairs to an LLM. The lexical index is cheap enough to rebuild each run, so nothing is persisted here;
the vector cache that needs content-hash incrementality arrives with the embedding layer (0.9.23).

Corpus: github mode → the gitignored board mirror (mirror.read_mirror); local mode → the goal files.
Completed work → closed mirror issues (velocity-scaled window) or local `status: done` goals.

SECRET-SAFE: findings carry issue refs + shared index TERMS (scrubbed, capped) — never a title/body/
secret. FAIL-OPEN: no corpus / no git / any error → an empty pack with a machine-readable `degraded[]`,
never a raise (a later slice calls this on the pick hot-path).

    python3 pipeline.py crosscheck <sdlc_dir> <goal>      # prints a backlog-check/v1 JSON pack
"""
import importlib.util, json, math, pathlib, re
from collections import Counter

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _load_velocity():
    vp = _HERE.parent.parent / "sdlc-velocity" / "scripts" / "velocity.py"
    spec = importlib.util.spec_from_file_location("velocity", vp)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


scrub = _load("scrub").scrub
mirror = _load("mirror")

SCHEMA = "backlog-check/v1"
_DEFAULTS = {"dup_threshold": 0.72, "obsolete_threshold": 0.72, "park_threshold": 0.80, "top_k": 8}
_AUTO_TARGET_MERGES = 50            # "auto" window ≈ the span of the last ~50 merges
_AUTO_WINDOW_FALLBACK = 90         # days, when velocity has no history (fresh / non-git repo)
_TITLE_WEIGHT = 3                  # a title term counts 3× a body term (bug-dedup convention)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the of to in on for and or is are be this that it with as at by from we you i he she they "
    "them our your their its it's not no do does did done can will would should could may might must "
    "if then else when while how what which who whom whose why has have had was were been being get "
    "add fix use make set new via per into out up off over under about only also all any each".split())
# blocking phrasing immediately followed by a #N reference — an EXPLICIT, high-precision blocker edge
_BLOCK_RE = re.compile(r"(?i)\b(blocked by|depends on|depends upon|needs|after|requires|waiting on)\b[^\n#]{0,40}?#(\d+)")


def _tokens(text):
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2 and t not in _STOP]


def _doc_tokens(title, body):
    c = Counter()
    for t in _tokens(title):
        c[t] += _TITLE_WEIGHT
    for t in _tokens(body):
        c[t] += 1
    return c


def _idf(docs):
    df = Counter()
    for d in docs:
        for term in d["tokens"]:
            df[term] += 1
    n = len(docs)
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}      # smoothed idf


def _vector(tokens, idf):
    return {t: c * idf.get(t, 0.0) for t, c in tokens.items()}


def _cosine(v1, v2):
    if not v1 or not v2:
        return 0.0
    small, big = (v1, v2) if len(v1) <= len(v2) else (v2, v1)
    dot = sum(w * big.get(t, 0.0) for t, w in small.items())
    if dot <= 0:
        return 0.0
    n1 = math.sqrt(sum(w * w for w in v1.values()))
    n2 = math.sqrt(sum(w * w for w in v2.values()))
    return dot / (n1 * n2) if n1 and n2 else 0.0


def _shared_terms(a, b, idf, k=6):
    shared = set(a["tokens"]) & set(b["tokens"])
    return [scrub(t) for t in sorted(shared, key=lambda t: (-idf.get(t, 0.0), t))[:k]]


def _finding(kind, ref, score, source, evidence, confident):
    return {"kind": kind, "ref": str(ref), "score": round(float(score), 4),
            "source": source, "evidence": list(evidence)[:8], "confident": bool(confident)}


def _load_config(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _num(cfg, key, default):
    """Coerce a config value to the default's numeric type, falling back to the default on anything
    bad (a hand-edited `top_k: "all"` or `dup_threshold: "high"` must degrade to the default — not
    zero out the whole check via the outer catch). `bool` (an int subclass) is treated as unset."""
    v = cfg.get(key, default)
    if isinstance(v, bool):
        return default
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def _build_corpus(sdlc_dir, config):
    """Returns (docs, degraded). doc = {ref,title,raw,tokens,open,completed,closed_at,source}.
    `open` = a live dedup candidate; `completed` = finished work that can obsolete the goal."""
    degraded = []
    if mirror.is_github_mode(config):
        recs = mirror.read_mirror(sdlc_dir)
        if not recs:
            degraded.append("no_mirror")
        docs = []
        for r in recs:
            if not isinstance(r, dict):
                continue                                # a garbage mirror line degrades ONE record, not all
            # the mirror already scrubs, but re-scrub the RAW (case-preserving) text here BEFORE
            # tokenizing: tokens are lowercased, and scrub's shape patterns are case-sensitive, so a
            # secret must be redacted while its case survives — else a lowercased secret token could
            # reach `evidence`. Idempotent on an already-scrubbed mirror; robust to a hand-built one.
            title, body = scrub(r.get("title") or ""), scrub(r.get("body_excerpt") or "")
            state = (r.get("state") or "open").lower()
            docs.append({"ref": str(r.get("number")), "title": title, "raw": title + "\n" + body,
                         "tokens": _doc_tokens(title, body), "open": state == "open",
                         "completed": state == "closed", "closed_at": r.get("closed_at"),
                         "source": "mirror"})
        return docs, degraded
    # local-files mode: the goal files ARE the corpus
    disc, fm = _load("discovery"), _load("frontmatter")
    docs = []
    for p in sorted((pathlib.Path(sdlc_dir) / "goals").glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = fm.parse(text)
        if not meta:
            continue                                    # a README etc. — not a goal
        status = meta.get("status")
        title = meta.get("title") or ""
        body = scrub(text.split("---", 2)[-1])          # local bodies aren't pre-scrubbed like the mirror
        docs.append({"ref": str(p), "title": scrub(title), "raw": scrub(title) + "\n" + body,
                     "tokens": _doc_tokens(scrub(title), body),
                     "open": status not in disc._SKIP, "completed": status == "done",
                     "closed_at": None, "source": "goals"})
    if not docs:
        degraded.append("no_goals")
    return docs, degraded


def _closed_window_days(config, run=None, velocity_measure=None):
    """Velocity-scaled 'recently closed' window (config `backlog_check.closed_window_days`, default
    'auto'): a window that captures ~50 recent merges, so obsolescence tracks the repo's real pace.
    A pinned integer/string wins; 'auto' inverts velocity; a rate-0 (fresh/non-git) repo → fallback."""
    win = (config.get("backlog_check") or {}).get("closed_window_days", "auto")
    if isinstance(win, bool):                           # guard: bool is an int subclass
        win = "auto"
    if isinstance(win, int):
        return max(1, win)
    if isinstance(win, str) and win.isdigit():
        return max(1, int(win))
    try:
        measure = velocity_measure or _load_velocity().measure
        m = measure(days=30, run=run)
        rate = m.get("prs_per_day") or m.get("commits_per_day") or 0
        if rate and rate > 0:
            return max(7, min(180, math.ceil(_AUTO_TARGET_MERGES / rate)))
    except Exception:
        pass
    return _AUTO_WINDOW_FALLBACK


def _within_window(doc, window_days, now):
    """A completed issue counts as obsolescence evidence only if closed within the window. A local
    `done` goal has no closed date → we can't window-filter it, so it always counts (fail-open)."""
    ca = doc.get("closed_at")
    if not ca:
        return True
    try:
        import calendar, time
        t = calendar.timegm(time.strptime(ca.replace("Z", "GMT"), "%Y-%m-%dT%H:%M:%S%Z"))
        ref_now = now if now is not None else time.time()
        return (ref_now - t) <= window_days * 86400
    except Exception:
        return True                                     # unparseable date → don't drop the candidate


def _explicit_blockers(goal_doc, docs):
    """Regex the goal's own text for explicit `blocked by/depends on … #N` edges. High precision: a
    blocker is asserted ONLY when N is a real OPEN issue in the corpus (a closed ref isn't a blocker)."""
    open_refs = {d["ref"] for d in docs if d["open"]}
    out = []
    for m in _BLOCK_RE.finditer(goal_doc.get("raw", "")):
        n = m.group(2)
        if n != goal_doc["ref"] and n in open_refs:
            out.append(_finding("blocked-by", n, 1.0, "explicit", [scrub(m.group(1).lower())], True))
    return out


def _ledger_signals(sdlc_dir, goal_doc, idf, doc_by_ref, dup_th, now):
    """Team-wide signals from the shared ledger (read-only, no `enabled` needed): a SIMILAR goal a
    teammate is claiming right now (the race the exact-goal lease can't see), and an outstanding
    hand-off whose target is this goal (a real, recorded blocker)."""
    try:
        ledger = _load("ledger")
        entries = ledger.read_all(sdlc_dir)
    except Exception:
        return []
    if not entries:
        return []
    goal_ref = goal_doc["ref"]
    gvec = _vector(goal_doc["tokens"], idf)
    out = []
    try:
        for cg, _actor in ledger.open_claims(entries, now=now).items():
            cg = str(cg)
            d = doc_by_ref.get(cg)
            if cg == goal_ref or not d:
                continue
            s = _cosine(gvec, _vector(d["tokens"], idf))
            if s >= dup_th:                             # a paraphrase of this goal already in flight
                out.append(_finding("in-flight-elsewhere", cg, s, "ledger",
                                     _shared_terms(goal_doc, d, idf), True))
    except Exception:
        pass
    try:
        for h in ledger.outstanding(entries):
            if ledger.handoff_key(h) == goal_ref:
                out.append(_finding("blocked-by", goal_ref, 1.0, "ledger", ["handoff"], True))
    except Exception:
        pass
    return out


def cross_check(sdlc_dir, goal, config=None, run=None, now=None, velocity_measure=None):
    """The whole stage-1 pass. FAIL-OPEN: any error → an empty pack + degraded['error']."""
    goal_ref = str(goal)
    try:
        config = config if config is not None else _load_config(sdlc_dir)
        cfg = config.get("backlog_check") or {}
        dup_th = _num(cfg, "dup_threshold", _DEFAULTS["dup_threshold"])
        obs_th = _num(cfg, "obsolete_threshold", _DEFAULTS["obsolete_threshold"])
        park_th = _num(cfg, "park_threshold", _DEFAULTS["park_threshold"])
        top_k = _num(cfg, "top_k", _DEFAULTS["top_k"])

        docs, degraded = _build_corpus(sdlc_dir, config)
        goal_doc = next((d for d in docs if d["ref"] == goal_ref), None)
        if goal_doc is None:
            return _pack(goal_ref, [], degraded + ["goal_not_in_corpus"])

        idf = _idf(docs)
        gvec = _vector(goal_doc["tokens"], idf)
        gterms = set(goal_doc["tokens"])
        window = _closed_window_days(config, run=run, velocity_measure=velocity_measure)

        scored = []
        for d in docs:
            if d["ref"] == goal_ref or not (gterms & set(d["tokens"])):
                continue                                # candidate-gen: only docs sharing ≥1 term
            s = _cosine(gvec, _vector(d["tokens"], idf))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: (-x[0], x[1]["ref"]))

        findings = []
        for s, d in scored[:top_k]:
            ev = _shared_terms(goal_doc, d, idf)
            if d["completed"] and s >= obs_th and _within_window(d, window, now):
                findings.append(_finding("obsoleted-by", d["ref"], s, d["source"], ev, s >= park_th))
            elif d["open"] and s >= dup_th:
                findings.append(_finding("duplicate", d["ref"], s, d["source"], ev, s >= park_th))

        findings += _explicit_blockers(goal_doc, docs)
        doc_by_ref = {d["ref"]: d for d in docs}
        findings += _ledger_signals(sdlc_dir, goal_doc, idf, doc_by_ref, dup_th, now)
        return _pack(goal_ref, _dedup_sort(findings), degraded)
    except Exception:
        return _pack(goal_ref, [], ["error"])


def _dedup_sort(findings):
    best = {}
    for f in findings:
        key = (f["kind"], f["ref"])
        if key not in best or f["score"] > best[key]["score"]:
            best[key] = f
    # confident first, then by score desc, then a stable (kind, ref) tiebreak — fully deterministic
    return sorted(best.values(), key=lambda f: (not f["confident"], -f["score"], f["kind"], f["ref"]))


def _pack(goal_ref, findings, degraded):
    return {"schema": SCHEMA, "goal": str(goal_ref), "findings": findings,
            "degraded": sorted(set(degraded))}


_KIND_PHRASE = {"duplicate": "duplicate of #{ref}", "obsoleted-by": "obsoleted by #{ref}",
                "blocked-by": "blocked by #{ref}", "in-flight-elsewhere": "in flight elsewhere: #{ref}"}


def _phrase(f):
    ref = f["ref"]
    ref = pathlib.Path(ref).name if "/" in ref else ref     # local goals are paths; show the stem
    base = _KIND_PHRASE.get(f["kind"], f["kind"] + " #{ref}").format(ref=ref)
    ev = ", ".join(f.get("evidence") or [])
    return f"{base} ({f['score']}" + (f"; shared: {ev}" if ev else "") + ")"


def summary(findings, limit=3):
    """A compact, secret-safe one-liner over the top findings (refs + scores + already-scrubbed shared
    terms — never a title/body). Used verbatim in the park comment / advisory note."""
    return "; ".join(_phrase(f) for f in findings[:limit])


def decide(pack, config):
    """Pure: turn a pack into the loop hook's action. `backlog_check.action` (default 'park'): a
    CONFIDENT finding parks-with-proof; 'flag' (or only weak findings) annotates and proceeds. Returns
    {action: 'park'|'proceed', reason: <park-comment text>, note: <advisory text>}. Deterministic —
    findings are already sorted confident-first, score desc."""
    findings = (pack or {}).get("findings") or []
    if not findings:
        return {"action": "proceed", "reason": "", "note": ""}
    action = (config.get("backlog_check") or {}).get("action", "park")
    line = "backlog cross-check: " + summary(findings)
    if action == "park" and any(f.get("confident") for f in findings):
        return {"action": "park", "reason": line, "note": ""}
    return {"action": "proceed", "reason": "", "note": line + " (advisory)"}


def main(argv):
    if len(argv) < 3:
        print("usage: backlog_check.py <sdlc_dir> <goal>", file=__import__("sys").stderr)
        return 2
    print(json.dumps(cross_check(argv[1], argv[2]), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
