#!/usr/bin/env python3
"""sdlc-doctor: a setup check-up. Audit only what THIS project's config makes relevant — github board
-> gh auth + project scope; KG enabled -> the builder; vision-first -> the north-star; always -> the
.sdlc layer — and report each check with the exact one-line fix. The command runner is injectable so
the logic is hermetically testable. Zero-dep."""
import sys, json, pathlib, re

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # doesn't garble to '?' or
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # crash on a non-UTF-8 console
except Exception:       # (the Windows cp1252 default); a stream without reconfigure is left as-is
    pass


def _real_run(args):
    import subprocess
    try:
        p = subprocess.run(args, capture_output=True, text=True)
        return (p.stdout + p.stderr) if p.returncode == 0 else ""
    except Exception:
        return ""


def _cfg(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        return {}


def _chk(name, ok, fix):
    return {"name": name, "ok": bool(ok), "fix": "" if ok else fix}


def _board_dup_risk(gh_cfg, run):
    """Board mirroring on, but NO `project.number` pinned, and the owner already has board(s): loopsmith
    resolves the board by TITLE, and if none matches its auto-title it CREATES a new one on first use -
    silently duplicating a board the loop then manages instead of the adopter's. Returns a one-line fix
    when that risk is present, else None. NOT gated on `number` (it fires precisely when number is
    unset); read-only; a can't-read returns None (no false alarm). Catches #9 at setup."""
    proj = gh_cfg.get("project") or {}
    if proj.get("number"):                     # a pinned number resolves directly - no create path
        return None
    repo = gh_cfg.get("repo") or ""
    owner = proj.get("owner") or (repo.split("/")[0] if "/" in repo else "@me")   # mirror sources._proj_owner
    raw = run(["gh", "project", "list", "--owner", owner, "--format", "json", "--limit", "100"])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    boards = (data.get("projects") if isinstance(data, dict) else data) or []
    if not boards:                             # nothing to duplicate - a fresh create is safe
        return None
    name = repo.split("/")[-1] if "/" in repo else (repo or "project")
    target = proj.get("title") or f"{name} — SDLC"   # mirror sources._proj_title (em-dash: it byte-matches)
    if any(b.get("title") == target for b in boards):
        return None                            # loopsmith's title already resolves - reused, not duplicated
    titles = ", ".join(b.get("title", "") for b in boards[:4] if b.get("title"))
    return (f"board mirroring is on with NO project.number, and {owner} already has board(s) "
            f"({titles}) - loopsmith resolves by title and will CREATE a new board if none matches "
            "its auto-title, silently duplicating one. Set discovery.github.project.number.")


def _unmapped_board_fields(gh_cfg, run):
    """Single-select board fields — beyond the Status field loopsmith drives, and beyond what
    project.custom_fields already maps — that an issue the loop CREATES (a hand-off) would be left
    blank on while every human-made issue carries them. Returns the unmapped names, [] when every
    field is covered, or None when the board can't be read (no number yet, no `project` scope, an API
    error) — so a can't-tell never reports a false all-clear. The one silent-data-loss trap doctor
    can catch before it fires."""
    proj = gh_cfg.get("project") or {}
    repo = gh_cfg.get("repo") or ""
    owner = proj.get("owner") or (repo.split("/")[0] if "/" in repo else "")
    number = proj.get("number")
    if not owner or not number:
        return None
    raw = run(["gh", "project", "field-list", str(number), "--owner", owner, "--format", "json", "--limit", "100"])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    fields = (data.get("fields") if isinstance(data, dict) else data) or []
    status_field = proj.get("status_field") or "Status"
    cf = proj.get("custom_fields")                    # a malformed (non-dict) value must not crash the run
    mapped = set(cf.keys()) if isinstance(cf, dict) else set()
    return [f.get("name") for f in fields
            if f.get("options")                       # single-select fields are the ones that carry options
            and f.get("name") != status_field
            and f.get("name") not in mapped]


def check(sdlc_dir=".sdlc", run=None):
    """Return the setup checks relevant to this project's config; each is {name, ok, fix}."""
    run = run or _real_run
    base = pathlib.Path(sdlc_dir)
    cfg = _cfg(sdlc_dir)
    disc = cfg.get("discovery") or {}
    kg = cfg.get("knowledge_graph") or {}
    out = [_chk("project layer", (base / "config.json").exists(), "run /sdlc-init to scaffold .sdlc/")]

    if disc.get("source") == "github":
        auth = run(["gh", "auth", "status"])
        out.append(_chk("gh auth", bool(auth), "run: gh auth login"))
        if ((disc.get("github") or {}).get("project") or {}).get("enabled"):
            out.append(_chk("gh project scope", bool(auth) and "project" in auth,
                            "run: gh auth refresh -s project"))
            dup = _board_dup_risk(disc.get("github") or {}, run)
            if dup:
                out.append(_chk("project.number pinned (no duplicate-board risk)", False, dup))
            unmapped = _unmapped_board_fields(disc.get("github") or {}, run)
            if unmapped is not None:
                out.append(_chk("board custom fields mapped", not unmapped,
                                "the board has single-select field(s) loopsmith won't set on issues it "
                                "creates: " + ", ".join(n for n in unmapped if n) + " - map them in "
                                "discovery.github.project.custom_fields (field -> option), or backfill by "
                                "hand, else a loop-created (hand-off) issue is left blank on them."))

    if kg.get("enabled") is True:
        builder = kg.get("builder", "graphify")
        ok = bool(run([builder, "--version"]))
        fix = "run: pip install graphifyy" if builder == "graphify" else f"install the '{builder}' graph builder"
        out.append(_chk(f"{builder} installed", ok, fix))

    ns = base / "context" / "north-star.md"
    if ns.exists():
        filled = "<the change you want" not in ns.read_text(encoding="utf-8")
        out.append(_chk("north-star filled", filled, "run /sdlc-vision to fill the tiers"))

    # The ledger is switched on in config but the ops branch has to be created + pushed once per
    # clone; before that a teammate's `init` finds nothing to fetch. Flag it as a real setup gap with
    # the one command that fixes it — `/sdlc-ledger` runs `sync.py bootstrap` (create + seed + push).
    if (cfg.get("ledger") or {}).get("enabled") is True:
        out.append(_chk("team ledger initialized", (base / "ledger" / ".git").exists(),
                        "run /sdlc-ledger — one command creates the ops branch, seeds your file + TEAM.md, and pushes"))

    # The permanent-refusal trap: verify.enforce on with no command refuses EVERY `done` forever, and
    # it looks like a working gate, not a misconfig. Flag it (a per-goal `verify_command` also satisfies).
    verify = cfg.get("verify") or {}
    if verify.get("enforce") is True:
        out.append(_chk("verify command present (enforce is on)",
                        bool(verify.get("command")) or _any_goal_verify_command(base),
                        "verify.enforce is on but no verify.command (and no goal sets verify_command) — "
                        "every `done` is refused. Set verify.command, or turn enforce off."))

    # A backlog cross-check whose park_threshold sits BELOW its candidate threshold parks EVERYTHING it
    # finds — the opposite of "confident hits only". Flag it (only when the feature is actually on).
    bchk = cfg.get("backlog_check")
    bchk = bchk if isinstance(bchk, dict) else {}       # a non-dict block reads as off, never crashes
    if bchk.get("enabled") is True:
        dup, park = bchk.get("dup_threshold", 0.72), bchk.get("park_threshold", 0.80)
        try:
            sane = float(park) >= float(dup)
        except (TypeError, ValueError):
            sane = True                 # a non-numeric threshold falls back to the default at runtime
        out.append(_chk("backlog cross-check thresholds sane",
                        sane,
                        "backlog_check.park_threshold (%r) < dup_threshold (%r): every candidate becomes "
                        "a confident PARK. Set park_threshold >= dup_threshold." % (park, dup)))
        # The dense/embedding layer switched on but with no embedder command silently runs lexical-only.
        embed = bchk.get("embed")
        embed = embed if isinstance(embed, dict) else {}
        if embed.get("enabled") is True or bchk.get("similarity") == "hybrid":
            out.append(_chk("backlog cross-check embedder configured",
                            bool((embed.get("command") or "").strip()),
                            "backlog_check.embed is on but embed.command is empty — the dense layer "
                            "silently falls back to lexical-only. Set embed.command (an embedder that "
                            "reads text on stdin and prints a JSON vector), or turn embed.enabled off."))

    # With work.enabled, verify runs in a FRESH worktree that has none of your installed deps — a
    # relative interpreter path (.venv/bin/python3, node_modules/.bin) fails exit=127 on the first
    # real per-goal run. Flag it before it bites.
    vcmd = verify.get("command") or ""
    if (cfg.get("work") or {}).get("enabled") is True and vcmd:
        out.append(_chk("verify.command resolves in the goal worktree",
                        not _WORKTREE_DEP.search(vcmd),
                        "verify.command has a RELATIVE .venv/venv/node_modules path — but work.enabled "
                        "runs it in a fresh worktree with NONE of your installed deps (fails exit=127). "
                        "Use an absolute interpreter path, a venv activated on PATH, or a wrapper script."))

    # companions (optional): superpowers + code-review power phases 1/3/5/6 when present; LoopSmith's
    # portable sdlc-* executors are the absent-safe fallback everywhere else — absent is never a failure.
    if (cfg.get("companions") or "auto") != "off":
        plugins = run(["claude", "plugin", "list"]) or ""
        for comp in ("superpowers", "code-review"):
            here = comp in plugins
            out.append(_chk(f"{comp}: {'present' if here else 'absent — portable executor used'}", True, ""))
    return out


#: A cited path worth checking: backticked, has a separator, and is concrete. Anything with a glob or
#: a <placeholder> is a pattern, not a reference — flagging those would cry wolf, and a check nobody
#: trusts gets ignored along with the true positives.
_CITED = re.compile(r"`([^`\s]*/[^`\s]*)`")
_MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_ABSTRACT = re.compile(r"[*?<>{}\[\]]|NNNN|YYYY|\.\.\.")
#: A RELATIVE .venv/venv/node_modules path in verify.command — a worktree footgun once work.enabled.
#: The lookbehind excludes a preceding `/` or `.` so an ABSOLUTE path (/x/.venv/…) is never flagged.
_WORKTREE_DEP = re.compile(r"(?<![\w./])(?:\.venv|venv|node_modules)/")


def _standing_docs(base):
    """The docs that describe the project and therefore rot when the project moves: the north-star
    tiers and project.md. Goals and plans are transient by design — not scanned."""
    docs = [base / "project.md"]
    docs += sorted((base / "context").glob("*.md")) if (base / "context").is_dir() else []
    return [d for d in docs if d.is_file()]


def _stale_paths(text, repo_root):
    out = []
    for ref in _CITED.findall(text):
        if _ABSTRACT.search(ref) or "://" in ref or ref.startswith(("-", "$")):
            continue
        if not (repo_root / ref.rstrip("/")).exists():
            out.append(ref)
    return out


def _dangling_links(text, doc_dir):
    out = []
    for target in _MDLINK.findall(text):
        target = target.split()[0].split("#")[0].strip()      # drop a title and any anchor
        if not target or "://" in target or target.startswith(("#", "mailto:")):
            continue
        if _ABSTRACT.search(target) or not (doc_dir / target).exists():
            out.append(target)
    return out


def hygiene(sdlc_dir=".sdlc", repo_root="."):
    """Content-rot over the standing docs: references that no longer resolve. Read-only, binary, and
    mechanical — the half of context maintenance a script can settle. The judgment half (demoting a
    rule that CI now enforces, archiving a superseded plan) belongs to `sdlc-retro`, because it
    changes files and needs approval. Returns [] when there are no standing docs to scan, so a
    drop-in project sees nothing new."""
    base, root = pathlib.Path(sdlc_dir), pathlib.Path(repo_root)
    docs = _standing_docs(base)
    if not docs:
        return []
    stale, dangling = {}, {}
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError:                                        # fail-open: unreadable != rotten
            continue
        if bad := _stale_paths(text, root):
            stale[doc.name] = bad
        if bad := _dangling_links(text, doc.parent):
            dangling[doc.name] = bad
    return [
        _chk("standing docs: cited paths resolve", not stale, _detail(stale, "moved or deleted")),
        _chk("standing docs: links resolve", not dangling, _detail(dangling, "no such file")),
    ]


def _detail(found, why):
    """One fix line naming the offenders. Capped — a wall of paths is a report nobody reads; the
    first few are enough to start, and re-running shows the rest."""
    parts = [f"{doc}: {', '.join(refs[:3])}" + (f" (+{len(refs) - 3} more)" if len(refs) > 3 else "")
             for doc, refs in sorted(found.items())]
    return f"{why} — {'; '.join(parts)}. Update the reference or drop it."


def _ledger_entries(base):
    """Count committed ledger lines. Read-only and fail-open — the dashboard never breaks on a
    half-written file."""
    return _count_jsonl_lines(pathlib.Path(base) / "ledger" / "entries")


def _count_jsonl_lines(directory):
    """Non-blank lines across a directory's *.jsonl, or 0 if it isn't there. `errors="replace"`
    is load-bearing, not defensive dressing: a process killed mid-append truncates a multi-byte
    UTF-8 sequence, and the resulting UnicodeDecodeError is a ValueError, NOT an OSError, so it
    would sail past the catch and take the WHOLE dashboard down — every other row with it — on
    the next run. A half-written file is exactly what this is here to survive."""
    total = 0
    if not directory.exists():
        return 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def _any_goal_verify_command(base):
    """True if any local goal declares its own `verify_command` in frontmatter — that satisfies
    verify.enforce even when the config command is empty, so it isn't the refusal trap."""
    goals = pathlib.Path(base) / "goals"
    if not goals.is_dir():
        return False
    for path in goals.glob("*.md"):
        try:
            if "verify_command:" in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _ignore_mechanism(repo_root):
    """Which git mechanism ignores the machine-written `.sdlc/` runtime dirs — the shared `.gitignore`,
    the local `.git/info/exclude`, or neither. Reported so an adopter catches a mismatch with intent
    (a local-only experiment shouldn't be editing the tracked .gitignore)."""
    root = pathlib.Path(repo_root)

    def covers(path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for raw in text.splitlines():
            line = raw.strip().strip("/")
            if line and not line.startswith("#") and (line == ".sdlc" or line.startswith(".sdlc/")):
                return True
        return False

    if covers(root / ".gitignore"):
        return "tracked .gitignore"
    if covers(root / ".git" / "info" / "exclude"):
        return "local .git/info/exclude (untracked — nothing the team sees)"
    return "NOT ignored — runtime dirs may get committed (run /sdlc-setup, or setup.py ignore .)"


def _ledger_feature_state(base, cfg):
    """Dashboard line for the ledger. 'enabled' alone isn't 'working' — the ops branch still has to be
    created + pushed once, so an enabled ledger with NOTHING yet (no worktree and no entries) reports
    the gap and its one-command fix instead of a count that would imply it's live. Once it has a
    worktree or any entries, it's in use — show the count."""
    if (cfg.get("ledger") or {}).get("enabled") is not True:
        return "off (nothing is recorded)"
    base = pathlib.Path(base)
    n = _ledger_entries(base)
    if n == 0 and not (base / "ledger" / ".git").exists():
        return "ON but NOT set up — run /sdlc-ledger to create + push the ops branch"
    return "ON — %d entr%s in .sdlc/ledger/entries/" % (n, "y" if n == 1 else "ies")


def _telemetry_events_count(base):
    """Committed+local event lines under .sdlc/ledger/events/. Shares _ledger_entries' counter, so
    the fail-open behaviour can't drift between the two rows."""
    return _count_jsonl_lines(pathlib.Path(base) / "ledger" / "events")


def _telemetry_feature_state(base, cfg):
    """'is on' alone doesn't say where events land or whether share is actually honored yet — see
    plan .sdlc/plans/138.md Decision 3/4. A non-dict telemetry value degrades to off (fail-open),
    same convention as every other block reader in this file."""
    t = cfg.get("telemetry")
    t = t if isinstance(t, dict) else {}
    if t.get("enabled") is not True:
        return "off (nothing is recorded)"
    n = _telemetry_events_count(base)
    where = "%d event%s in .sdlc/ledger/events/" % (n, "" if n == 1 else "s")
    if t.get("share") is False:
        return (f"ON, share:false — NOT YET HONORED: still lands in .sdlc/ledger/events/ and still "
                f"publishes with the ledger, same as share:true — {where}")
    return f"ON, share:true — {where}, publishes to the ops branch alongside ledger entries"


def _backlog_check_state(cfg):
    """OFF unless backlog_check.enabled is strictly True (a truthy string must not switch a pick-path
    behavior on). A non-dict block degrades to off — same convention as every other reader here."""
    b = cfg.get("backlog_check")
    b = b if isinstance(b, dict) else {}
    if b.get("enabled") is not True:
        return "off (a picked goal is worked without a backlog cross-check)"
    how = ("parks a confident duplicate/obsolete/blocked goal (with proof), else annotates"
           if b.get("action", "park") == "park" else "only annotates (flag mode — never parks)")
    return "ON — pre-work cross-check " + how


def _decision_gate_state(base, cfg):
    """Count the ACTIVE decisions, not the entries. A registry whose decisions are all superseded
    enforces nothing, and reporting it as ON would be exactly the false assurance this gate exists
    to remove."""
    reg = pathlib.Path(base) / "decisions.json"
    if not reg.exists():
        return "off (no registry — nothing is enforced)"
    if ((cfg.get("gates") or {}).get("decision_gate") or {}).get("enabled") is False:
        return "DISABLED by config (registry present but not enforced)"
    try:
        decisions = json.loads(reg.read_text(encoding="utf-8")).get("decisions") or []
    except Exception:
        return "registry present but UNREADABLE — the gate fails open, so nothing is enforced"
    active = [d for d in decisions if isinstance(d, dict) and d.get("status", "active") == "active"]
    inv = sum(1 for d in active if d.get("class") == "invariant")
    if not active:
        return "registry present but NO active decisions — nothing is enforced"
    return f"ON — {inv} invariant(s) deny, {len(active) - inv} recipe(s) ask"


def _automerge_state(wk):
    """Mirrors work.policy() without importing it — doctor stays standalone, and a dashboard that
    lied about which merge policy is live would be worse than no dashboard."""
    if wk.get("enabled") is not True:
        return "off (per-goal worktrees are off)"
    value = wk.get("auto_merge")
    chosen = "always" if value is True else (str(value).strip().lower() if value else "off")
    method = wk.get("merge_method") or "squash"
    return {
        "always": "ALWAYS (%s) — merges even where nothing is enforced on the base" % method,
        "protected": "PROTECTED (%s) — merges only where the base REQUIRES checks/reviews" % method,
    }.get(chosen, "off (a clean, safe PR is left for a human)")


def _review_gate_state(wk):
    """Mirrors work.review_mode() without importing it. A REAL PR-review gate independent of branch
    protection — worth showing because it's the difference between 'auto-merge respects a human's
    Request-changes' and 'it merges straight over it' on an unprotected base."""
    value = wk.get("require_review")
    mode = "approval" if value is True else (str(value).strip().lower() if value else "off")
    return {
        "changes": "ON (changes) — parks on CHANGES_REQUESTED, an unresolved thread, or a `loopsmith:block`",
        "approval": ("ON (approval) — the loop reviews its own PR and posts loopsmith:approve/block "
                     "(work.py post-review); merges only an approved PR. A human can use the markers too"),
    }.get(mode, "off — auto-merge only respects reviews the base branch's protection REQUIRES")


def _review_independence_state(cfg):
    """Is the maker kept out of its own review? Worth showing because the failure is silent: a maker
    that reviews its own plan/diff reads as "reviewed", and on a lower tier it rubber-stamps."""
    if (cfg.get("review") or {}).get("independent") is False:
        return "off (INLINE — the maker reviews its own work; a fresh reviewer is not spawned)"
    return "ON — a fresh, author-blind reviewer per gate, grounded in the north-star + whole repo"


def features(sdlc_dir=".sdlc"):
    """The capability dashboard: every optional feature, its CURRENT state, and the one-line
    enable. Informational (never a failure) — the answer to "what is on right now?"."""
    import os
    cfg = _cfg(sdlc_dir)
    base = pathlib.Path(sdlc_dir)
    budget = cfg.get("budget") or {}
    verify = cfg.get("verify") or {}
    gate = (cfg.get("gates") or {}).get("hard_plan_gate") or {}
    sg = (cfg.get("gates") or {}).get("stop_gate") or {}
    par = cfg.get("parallel") or {}
    wk = cfg.get("work") or {}
    rows = [
        ("model+effort auto-selection",
         "AUTO (per-goal `resolve` + per-step `resolve-step`)"
         if (cfg.get("model_selection") or "off") == "auto" else "off",
         'config: "model_selection": "auto"'),
        ("machine-checked done (verify.enforce)",
         "ON — `record done` refused without fresh `loop.py verify` evidence"
         if verify.get("enforce") is True else "off (prose gate only)",
         'config: "verify": {"enforce": true}'),
        ("hard plan-gate (deny source edits w/o fresh plan)",
         f"ON ({gate.get('plan_freshness_hours', 24)}h window)" if gate.get("enabled") is True else "off (prompt-gate reminder only)",
         'config: "gates": {"hard_plan_gate": {"enabled": true}}'),
        ("Stop gate (refuse to end a session with unplanned source)",
         f"ON ({sg.get('plan_freshness_hours', 24)}h window)" if sg.get("enabled") is True else "off",
         'config: "gates": {"stop_gate": {"enabled": true}}'),
        ("decision gate (deny edits that break a registered invariant)",
         _decision_gate_state(base, cfg),
         "author .sdlc/decisions.json (see /sdlc-decide) — authoring it IS the opt-in"),
        ("pipeline report card + propose",
         "DECLARED (.sdlc/pipeline.json present)" if (base / "pipeline.json").exists() else "not declared",
         "declare stages in .sdlc/pipeline.json, then: pipeline.py card .sdlc"),
        ("budgets",
         "iterations=%s minutes=%s tokens=%s" % (
             budget.get("max_iterations", 20),
             budget.get("max_minutes") or "off",
             ("%s (host-reported via loop.py spend)" % budget["max_tokens"]) if budget.get("max_tokens") else "off"),
         'config: "budget": {"max_minutes": N, "max_tokens": N}'),
        ("prompt-gate scope",
         "GLOBAL (env override)" if os.environ.get("LOOPSMITH_GATE_GLOBAL") == "1"
         else "repo-scoped (speaks only where .sdlc/ exists)",
         "env LOOPSMITH_GATE_GLOBAL=1 restores always-on"),
        ("SessionStart policy brief",
         "ON — injects the SDLC brief + install self-check at session start"
         if (cfg.get("session_start") or {}).get("enabled") is True else "off",
         'config: "session_start": {"enabled": true}'),
        ("knowledge graph",
         "enabled" if (cfg.get("knowledge_graph") or {}).get("enabled") is True else "off",
         'config: "knowledge_graph": {"enabled": true}'),
        ("backlog source",
         (cfg.get("discovery") or {}).get("source") or "local-goals",
         'config: "discovery": {"source": "github"}'),
        ("team ledger",
         _ledger_feature_state(base, cfg),
         'config: "ledger": {"enabled": true}, then /sdlc-ledger to create + push it'),
        ("telemetry (agent-emitted events)",
         _telemetry_feature_state(base, cfg),
         'config: "telemetry": {"enabled": true, "share": true|false}'),
        ("pre-work backlog cross-check",
         _backlog_check_state(cfg),
         'config: "backlog_check": {"enabled": true}'),
        ("slice parallelism",
         ("ON — up to %s concurrent slices per wave" % par.get("max_concurrent", 3))
         if par.get("enabled") is True else "off (a goal's slices run one after another)",
         'config: "parallel": {"enabled": true, "max_concurrent": 3}'),
        ("per-goal worktree + PR",
         "ON — a worktree/branch/PR per goal; verify runs in it"
         if wk.get("enabled") is True else
         "off — the loop writes NOTHING to git: a done goal's changes stay in your working tree, no PR",
         'config: "work": {"enabled": true}  (or run /sdlc-setup)'),
        ("runtime dirs ignored via",
         _ignore_mechanism(pathlib.Path(base).parent),
         "run /sdlc-setup (or setup.py ignore .) — never clobbers an ignore rule you already set"),
        ("auto-merge a clean AND safe PR",
         _automerge_state(wk),
         'config: "work": {"auto_merge": "protected"}  (off | protected | always)'),
        ("PR review gate (independent of branch protection)",
         _review_gate_state(wk),
         'config: "work": {"require_review": "approval"}  (off | changes | approval)'),
        ("independent review (maker is never the checker)",
         _review_independence_state(cfg),
         'config: "review": {"independent": true, "context": "project"}'),
        ("skill selection vs platform built-ins",
         "advisory — a plugin can't disable a built-in; LoopSmith prefers its own skills via sharp "
         "descriptions + per-skill resolution headers (no runtime API to detect a live conflict)",
         'if a standalone built-in shadows a LoopSmith skill: settings.json "skillOverrides": '
         '{"<name>": "off"}; if it is a plugin: /plugin disable <plugin>'),
    ]
    return rows


def main(argv):
    if len(argv) >= 2 and argv[1] == "features":
        for name, state, enable in features(argv[2] if len(argv) > 2 else ".sdlc"):
            print(f"  {name}: {state}\n      enable/change: {enable}")
        return 0
    if len(argv) >= 2 and argv[1] == "check":
        checks = check(argv[2] if len(argv) > 2 else ".sdlc")
        gaps = [c for c in checks if not c["ok"]]
        for c in checks:
            print(f"  [{'OK ' if c['ok'] else 'MISSING'}] {c['name']}" + ("" if c["ok"] else f"  ->  {c['fix']}"))
        print(f"\nsdlc-doctor: {len(checks) - len(gaps)}/{len(checks)} ready"
              + ("." if not gaps else f"; {len(gaps)} need the one-liner shown above."))
        print("\nfeatures (doctor.py features for the enable one-liners):")
        for name, state, _ in features(argv[2] if len(argv) > 2 else ".sdlc"):
            print(f"  {name}: {state}")
        _print_hygiene(argv[2] if len(argv) > 2 else ".sdlc")
        return 0
    if len(argv) >= 2 and argv[1] == "hygiene":
        rows = hygiene(argv[2] if len(argv) > 2 else ".sdlc", argv[3] if len(argv) > 3 else ".")
        if not rows:
            print("  no standing docs to scan (.sdlc/project.md, .sdlc/context/*.md)")
            return 0
        for c in rows:
            print(f"  [{'OK  ' if c['ok'] else 'STALE'}] {c['name']}" + ("" if c["ok"] else f"\n      {c['fix']}"))
        return 0
    print("usage: doctor.py check [sdlc_dir] | features [sdlc_dir] | hygiene [sdlc_dir] [repo_root]",
          file=sys.stderr)
    return 2


def _print_hygiene(sdlc_dir):
    """Surfaced inside `check` so the rot scan actually runs — a maintenance command nobody
    remembers to type is the failure mode this exists to avoid. Kept in its own section: setup
    readiness and content rot are different questions and must not share a score."""
    rot = [c for c in hygiene(sdlc_dir) if not c["ok"]]
    if rot:
        print("\nstanding-doc hygiene (doctor.py hygiene for detail):")
        for c in rot:
            print(f"  [STALE] {c['name']}\n      {c['fix']}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
