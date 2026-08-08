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
import hashlib, importlib.util, json, math, pathlib, re, subprocess
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
sources = _load("sources")
goal_size = _load("goal_size")     # #521: shared decomposition-marker constants -- single source of
                                   # truth with loop.py's decompose_check guard

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


def _lex_weight(tokens, idf, mode="tfidf", avgdl=0.0, k1=1.2, b=0.75):
    """The lexical term-weight vector. `tfidf` (default) is IDENTICAL to _vector — so the default path
    is byte-identical to pre-0.9.23. `bm25` uses BM25 saturation + length normalization (the stronger
    bug-dedup baseline, Q1's opt-in), as symmetric BM25-weighted cosine."""
    if mode != "bm25":
        return {t: c * idf.get(t, 0.0) for t, c in tokens.items()}
    dl = sum(tokens.values()) or 1
    norm = (1 - b + b * dl / avgdl) if avgdl else 1.0
    return {t: idf.get(t, 0.0) * (c * (k1 + 1)) / (c + k1 * norm) for t, c in tokens.items()}


def _list_cosine(a, b):
    """Cosine over two dense (embedding) vectors — plain lists of equal length."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    if dot <= 0:
        return 0.0
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


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
    """Returns (docs, degraded). doc = {ref,title,raw,body,tokens,open,completed,closed_at,source}.
    `open` = a live dedup candidate; `completed` = finished work that can obsolete the goal. `body` is
    the scrubbed body ALONE (unlike `raw`, which is `title + "\\n" + body`) -- #521's decomposition-
    marker exemption needs the body's own first line, and `raw.splitlines()[0]` is always the TITLE,
    never the marker."""
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
                         "body": body, "tokens": _doc_tokens(title, body), "open": state == "open",
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
        # #544: NOT text.split("---", 2) -- a bare '---' substring inside a frontmatter VALUE (e.g. a
        # title like "Fix the A---B connector bug") counts as a split point ahead of the real closing
        # fence, under-stripping the body (a title fragment + the real fence get prepended to it).
        # fm.strip() reuses the same line-anchored _FENCE regex fm.parse() already applies above.
        body = scrub(fm.strip(text))                    # local bodies aren't pre-scrubbed like the mirror
        docs.append({"ref": str(p), "title": scrub(title), "raw": scrub(title) + "\n" + body,
                     "body": body, "tokens": _doc_tokens(scrub(title), body),
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


def _explicit_blockers(goal_doc, docs, extra_text=""):
    """Regex the goal's own text for explicit `blocked by/depends on … #N` edges. High precision: a
    blocker is asserted ONLY when N is a real OPEN issue in the corpus (a closed ref isn't a blocker).

    `extra_text` is scrubbed comment text from `_goal_comment_text()` below — kept as a SEPARATE
    parameter rather than mutated into `goal_doc["raw"]` itself, because `raw` also feeds the
    embedding channel (`_dense_channel`) for EVERY doc in the corpus, not just the goal being
    checked; folding comment text into it would silently change dedup/embedding scoring too, which
    is out of scope here (#389 is about the explicit-blocker fallback only)."""
    open_refs = {d["ref"] for d in docs if d["open"]}
    haystack = goal_doc.get("raw", "") + ("\n" + extra_text if extra_text else "")
    out = []
    for m in _BLOCK_RE.finditer(haystack):
        n = m.group(2)
        if n != goal_doc["ref"] and n in open_refs:
            out.append(_finding("blocked-by", n, 1.0, "explicit", [scrub(m.group(1).lower())], True))
    return out


def _goal_comment_text(sdlc_dir, config, goal_doc, run=None):
    """Comments for ONLY `goal_doc["ref"]` — the one issue actually being considered, right before
    `precheck()` would spend a real token (never corpus-wide; `mirror.py`'s own bulk fetch stays
    title+body only, unchanged — see its module docstring). A human commenting a dependency directly
    via the GitHub UI, bypassing `handoff.hand_off()` entirely, only ever leaves that marker on ONE
    issue at a time, so a bounded, single-issue fetch is enough to catch it.

    Scrubbed identically to how title/body already are (`scrub.scrub()`, the same call
    `_build_corpus` already makes) and capped the same way the mirror caps a body excerpt
    (`mirror._EXCERPT_CHARS`) — comment text reaching local state gets no weaker treatment than the
    board mirror gives issue bodies.

    A no-op outside github mode: comments aren't a concept for local goal files at all, so this never
    attempts a network call it has no way to satisfy. FAIL-OPEN, independently of `cross_check()`'s
    own outer try/except: a comment-fetch failure degrades to "no extra blocker evidence from
    comments this run", never to an empty pack (`sources.fetch_comments` already fails open to `[]`
    on its own; this second try/except is defense in depth around the scrub/join step too)."""
    if not mirror.is_github_mode(config):
        return ""
    try:
        comments = sources.fetch_comments(config, goal_doc["ref"], run=run)
        return "\n".join(scrub(c["body"])[:mirror._EXCERPT_CHARS] for c in comments)
    except Exception:
        return ""


def _ledger_signals(sdlc_dir, goal_doc, idf, doc_by_ref, dup_th, now, exempt=False, config=None):
    """Team-wide signals from the shared ledger (read-only, no `enabled` needed): a SIMILAR goal a
    teammate is claiming right now (the race the exact-goal lease can't see), and an outstanding
    hand-off this goal FILED (a real, recorded blocker — it is waiting on the target it handed off,
    which is why the finding's `ref` is that target and not this goal).

    The similarity branch reads the claim lease through the SAME TTL every other consumer uses
    (#535): a claim the loop is already free to re-take cannot simultaneously be grounds for parking
    a paraphrase of it — that would be an internal contradiction, and an abandoned claim would park
    similar goals forever.

    `exempt` (#521): `goal_doc` is a decomposition child/meta-goal (see `cross_check`'s own
    precomputed `exempt`). Applies ONLY to the in-flight-elsewhere SIMILARITY branch below -- parallel
    sibling execution is exactly what decomposition creates, the same false-positive family as
    duplicate/obsoleted-by, just at this branch's lower (unconditional) bar. Never the recorded-
    hand-off branch further down: a hand-off is an explicit, human-recorded blocker, not a similarity
    guess, and applies to a marked child in full."""
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
        # Inside the try on purpose: lease_ttl_seconds RAISES on a malformed `ttl_hours`, and that
        # must degrade THIS channel only — outside, cross_check's own catch-all would empty the pack.
        ttl = ledger.lease_ttl_seconds(config)
        for cg, _actor in ledger.open_claims(entries, now=now, ttl_seconds=ttl).items():
            cg = str(cg)
            d = doc_by_ref.get(cg)
            if cg == goal_ref or not d:
                continue
            s = _cosine(gvec, _vector(d["tokens"], idf))
            if s >= dup_th:                             # a paraphrase of this goal already in flight
                out.append(_finding("in-flight-elsewhere", cg, s, "ledger",
                                     _shared_terms(goal_doc, d, idf), not exempt))
    except Exception:
        pass
    try:
        for h in ledger.outstanding(entries):
            # The entry's `goal` is the side that RECORDED "I am blocked"; its `issue` is the target
            # it opened in the owner's area. Matching on `handoff_key` (issue-or-goal) parked the
            # TARGET against itself and let the genuinely-blocked filer walk straight into the work
            # it was waiting on (#532). `handoff_key` itself stays exactly as it is — pairing a
            # hand-off with its `ack` answers is a different question, and that conversation really
            # does live on the target issue.
            if str(h.get("goal")) != goal_ref:
                continue
            key = ledger.handoff_key(h)
            # A closed target is not a blocker — the same precision rule `_explicit_blockers()`
            # applies to its own `#N` refs. An `ack` is skippable (the recipient's normal path is
            # merge-and-close) and `outstanding()` settles only on ack resolved/declined, so without
            # this an unacked hand-off would outlive the issue it waited on and park the filer
            # forever. A target absent from the corpus still blocks: unknown is not finished.
            d = doc_by_ref.get(key)
            if d and d["completed"]:
                continue
            # `ref` is the TARGET that must land first — the same the-other-item meaning `ref`
            # carries in the duplicate/obsoleted-by findings, instead of the self-reference the
            # pre-fix branch emitted. In local mode `handoff_key` falls back to the goal itself:
            # there is no separate target artifact to point at, and the `[handoff]` term still says
            # what kind of block it is.
            out.append(_finding("blocked-by", key, 1.0, "ledger", ["handoff"], True))
    except Exception:
        pass
    return out


_EMBED_CACHE_REL = "state/embeddings.json"      # gitignored (.sdlc/state/), content-hash keyed


def _embed_enabled(config):
    # `embed.enabled` is the SOLE switch for the dense channel — `similarity` only picks the lexical
    # scorer (tfidf | bm25). One switch, one meaning; no second, undocumented path.
    return ((config.get("backlog_check") or {}).get("embed") or {}).get("enabled") is True


def _run_embedder(text, command):
    """Run the provider-agnostic embedder: `command` reads the text on stdin and prints a JSON array
    (the vector). Returns a list[float], or None on any failure (missing tool, bad JSON, non-zero exit,
    timeout) so the dense channel fails open to lexical-only."""
    try:
        proc = subprocess.run(command, shell=True, input=text or "", capture_output=True,
                              text=True, timeout=30)
        if proc.returncode != 0:
            return None
        v = json.loads(proc.stdout)
        return [float(x) for x in v] if isinstance(v, list) and v else None
    except Exception:
        return None


def _embedder_from_config(config):
    command = ((config.get("backlog_check") or {}).get("embed") or {}).get("command") or ""
    command = command.strip()
    return (lambda text: _run_embedder(text, command)) if command else None


def _dense_channel(sdlc_dir, config, docs, goal_ref, embed_fn):
    """Build {ref: embedding} for the corpus, cached by content-hash in gitignored .sdlc/state/ and
    computed INCREMENTALLY (only new/changed texts are embedded). Returns (vectors, weight) when the
    dense channel is usable, else (None, 0.0) — not enabled, no embedder configured, or the goal's own
    text couldn't be embedded (can't fuse without it). Fully fail-open; never raises."""
    if not _embed_enabled(config):
        return None, 0.0
    fn = embed_fn or _embedder_from_config(config)
    if fn is None:
        return None, 0.0                                     # enabled but no command -> lexical-only
    embed_cfg = (config.get("backlog_check") or {}).get("embed") or {}
    weight = _num(embed_cfg, "weight", 0.5)
    # #542: the cache key used to be sha256(text) ALONE -- nothing tied a cached vector to the
    # embedder that produced it, so swapping `embed.command` (a provider/model change) while the
    # corpus text stayed the same silently served the OLD embedder's vectors into the NEW
    # embedder's vector space (a meaningless cross-space cosine). Folding the command string into
    # the key makes a swap self-invalidating: every doc misses the cache once under the new key and
    # gets genuinely re-embedded, no separate "clear the cache" step required. `embed_fn` (test/CLI
    # injection) has no command string of its own to identify it BY DESIGN -- the config's own
    # `embed.command` is the caller-visible identity either way, exactly the value #542's fix sketch
    # names ("sha of embed.command"), so callers that inject a function still get real invalidation
    # as long as they set `command` to something meaningful for their embedder.
    identity = (embed_cfg.get("command") or "").strip()
    try:
        cache_path = pathlib.Path(sdlc_dir) / _EMBED_CACHE_REL
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            cache = cache if isinstance(cache, dict) else {}
        except Exception:
            cache = {}
        vecs, dirty = {}, False
        for d in docs:
            key = hashlib.sha256(
                (identity + "\x00" + (d.get("raw") or "")).encode("utf-8")).hexdigest()[:16]
            v = cache.get(key)
            if v is None:
                v = fn(d.get("raw") or "")
                if v is None:
                    continue                                 # skip a doc that won't embed; keep the rest
                cache[key], dirty = v, True
            vecs[d["ref"]] = v
        if dirty:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
            except Exception:
                pass                                         # a read-only .sdlc must not break the check
        return (vecs, weight) if goal_ref in vecs else (None, 0.0)
    except Exception:
        return None, 0.0


def cross_check(sdlc_dir, goal, config=None, run=None, now=None, velocity_measure=None, embed_fn=None):
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
        sim_mode = (config.get("backlog_check") or {}).get("similarity", "tfidf")
        avgdl = (sum(sum(d["tokens"].values()) for d in docs) / len(docs)) if docs else 0.0
        gvec = _lex_weight(goal_doc["tokens"], idf, sim_mode, avgdl)
        gterms = set(goal_doc["tokens"])
        # #521: a first-line `loopsmith:decomposed-from=`/`loopsmith:decompose-of=` marker means this
        # goal IS a deliberately authored decomposition child/meta-goal (constants shared with loop.py's
        # decompose_check guard via goal_size.py, so the two checks can't drift apart). The duplicate
        # path's `_earlier()` rule always parks the NEWER of a similar pair, and a freshly created child
        # is always the newest -- so a child similar to its own parent/siblings would always be the one
        # parked; the obsoleted-by path has no `_earlier()` gate but compares against closed work a
        # fresh child can also spuriously resemble; the in-flight-elsewhere ledger-similarity branch
        # shares the same false-positive family at a lower bar (parallel sibling execution is exactly
        # what decomposition creates). Exemption only downgrades `confident` -- the finding is still
        # EMITTED (module contract: evidence, never a verdict) -- and NEVER reaches `_explicit_blockers`
        # or the recorded-hand-off branch: an explicit blocker or a recorded hand-off still fully
        # applies to a marked child.
        #
        # "First line" here means the first NON-BLANK line of the (already-scrubbed) body excerpt.
        # local-mode bodies do NOT always start with a blank line (#544: `_build_corpus` strips the
        # frontmatter fence itself via `frontmatter.strip()`, not an extra artifact newline) -- but a
        # goal file AUTHORED with a blank line right after the closing fence (a common, legitimate
        # markdown style) still produces one, since that blank line is real content the fence-stripping
        # never touches. `lstrip()` before `splitlines()` therefore stays mandatory for that still-live
        # case -- a deliberate divergence from loop.py's own guard, which reads the raw, unstripped body
        # straight from the source and has no such leading blank line to strip. CRLF-tolerant via
        # `splitlines()`, same as loop.py's guard. `.get("body")` because some callers (tests) hand-build
        # a partial doc.
        first_line = (goal_doc.get("body") or "").lstrip().splitlines()[:1]
        first_line = first_line[0] if first_line else ""
        exempt = (goal_size.DECOMPOSED_FROM_MARKER in first_line
                  or goal_size.DECOMPOSE_OF_MARKER in first_line)
        window = _closed_window_days(config, run=run, velocity_measure=velocity_measure)

        # Opt-in dense/embedding channel (Q5): catches paraphrases that share NO lexical term. Fail-open
        # to lexical-only. When it's off (the default), `dense` is None and every line below reduces to
        # the exact pre-0.9.23 lexical path — byte-identical.
        dense, dweight = _dense_channel(sdlc_dir, config, docs, goal_ref, embed_fn)
        if dense is None and _embed_enabled(config):
            degraded.append("no_embedder")
        gden = dense.get(goal_ref) if dense else None

        scored = []
        for d in docs:
            if d["ref"] == goal_ref:
                continue
            shares = bool(gterms & set(d["tokens"]))
            if not shares and dense is None:
                continue                                # lexical-only: candidate-gen = docs sharing ≥1 term
            lex = _cosine(gvec, _lex_weight(d["tokens"], idf, sim_mode, avgdl)) if shares else 0.0
            den = dweight * _list_cosine(gden, dense.get(d["ref"])) if dense else 0.0
            s = max(lex, den)                           # fuse: the stronger of the two channels
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: (-x[0], _ref_key(x[1]["ref"])))   # earliest-ref tiebreak, not lexical

        findings = []
        for s, d in scored[:top_k]:
            ev = _shared_terms(goal_doc, d, idf)
            if d["completed"] and s >= obs_th and _within_window(d, window, now):
                findings.append(_finding("obsoleted-by", d["ref"], s, d["source"], ev,
                                          s >= park_th and not exempt))
            elif d["open"] and s >= dup_th:
                # Of a duplicate PAIR, park only the LATER goal: a `duplicate` is park-confident only when
                # the matched open issue is EARLIER in pick order (lower number / earlier filename). Else
                # both #1↔#2 would park each other and neither would ever be worked — the first-filed
                # survives and is worked; later duplicates park against it. (Reported either way.)
                confident = s >= park_th and _earlier(d["ref"], goal_ref) and not exempt
                findings.append(_finding("duplicate", d["ref"], s, d["source"], ev, confident))

        comment_text = _goal_comment_text(sdlc_dir, config, goal_doc, run=run)
        findings += _explicit_blockers(goal_doc, docs, comment_text)
        doc_by_ref = {d["ref"]: d for d in docs}
        findings += _ledger_signals(sdlc_dir, goal_doc, idf, doc_by_ref, dup_th, now, exempt,
                                     config=config)
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


def _stem(ref):
    return pathlib.Path(str(ref)).name          # github ref '42' -> '42'; local path -> filename (win-safe)


def _ref_key(ref):
    """Deterministic ref ordering matching next_pending (numeric issue numbers, THEN filename). Used as
    the candidate-truncation tiebreak so the top_k window keeps the EARLIEST issues, not the lexically
    smallest — else in a duplicate cluster larger than top_k, #10..#17 could crowd #2 out of #3's window
    and #3 would find no earlier match and wrongly survive (a second survivor)."""
    try:
        return (0, int(ref), "")
    except (TypeError, ValueError):
        return (1, 0, _stem(ref))


def _earlier(a, b):
    """Pick order (mirrors next_pending's oldest-first): lower issue number, else earlier filename. Used
    so only the LATER goal of a duplicate pair parks — the first-filed survives and gets worked."""
    return _ref_key(a) < _ref_key(b)


def _phrase(f):
    base = _KIND_PHRASE.get(f["kind"], f["kind"] + " #{ref}").format(ref=_stem(f["ref"]))
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
    pack = pack or {}
    findings = pack.get("findings") or []
    if "goal_not_in_corpus" in (pack.get("degraded") or []):
        # The goal itself was absent from the corpus, so NO finding could be computed for it. That
        # is not the same answer as "checked, found nothing", and a bare proceed reported it as if
        # it were. Still PROCEED — there is no evidence to park on, in either action mode — but say
        # so, in the advisory channel the loop already surfaces.
        return {"action": "proceed", "reason": "",
                "note": f"backlog cross-check: skipped — #{pack.get('goal')} is not in the backlog "
                        "mirror, so nothing was compared; refresh the mirror if this repeats (advisory)"}
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
