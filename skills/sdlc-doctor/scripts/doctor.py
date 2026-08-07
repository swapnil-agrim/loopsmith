#!/usr/bin/env python3
"""sdlc-doctor: a setup check-up. Audit only what THIS project's config makes relevant — github board
-> gh auth + project scope; KG enabled -> the builder; vision-first -> the north-star; always -> the
.sdlc layer — and report each check with the exact one-line fix. The command runner is injectable so
the logic is hermetically testable. Zero-dep."""
import sys, json, pathlib, re, importlib.util

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # doesn't garble to '?' or
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # crash on a non-UTF-8 console
except Exception:       # (the Windows cp1252 default); a stream without reconfigure is left as-is
    pass

_HERE = pathlib.Path(__file__).resolve().parent


def _real_run(args):
    import subprocess
    try:
        p = subprocess.run(args, capture_output=True, text=True)
        return (p.stdout + p.stderr) if p.returncode == 0 else ""
    except Exception:
        return ""


def _cfg(sdlc_dir):
    try:
        data = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}   # a non-dict config.json reads as empty, never crashes


def _block(cfg, name):
    """A config BLOCK read that can't crash on a shape typo. `cfg.get(name)` alone is not enough: a
    truthy NON-dict value (e.g. `{"verify": "pytest"}`) sails past `or {}` unchanged, and every
    `.get()` a caller then does on it raises `AttributeError` — in the one tool an adopter runs
    *because* their config is wrong. Every block reader in this file goes through this (including
    nested ones — pass the parent's OWN `_block()` result back in), so a malformed block anywhere
    degrades to reading as off/absent instead of aborting the whole check/features/dashboard run.
    Guards `cfg` itself too (not just the extracted value) — a caller holding a non-dict `cfg` (e.g.
    a board-helper's `gh_cfg` reached some other way) gets `{}` instead of an AttributeError on the
    `.get(name)` call, so this composes safely with itself at any nesting depth."""
    if not isinstance(cfg, dict):
        return {}
    value = cfg.get(name)
    return value if isinstance(value, dict) else {}


def _chk(name, ok, fix):
    return {"name": name, "ok": bool(ok), "fix": "" if ok else fix}


def _enforce_enabled(verify):
    """`verify.enforce` as a bool, read generously — intentionally duplicated from loop.py's own
    `_enforce_enabled` (F17/#342) rather than imported: doctor.py is a standalone diagnostic script
    with no cross-skill import (every other helper here is self-contained too), and this check has
    to keep working even if something ELSE in the loop is broken. A strict `is True` let `enforce: 1`
    or `enforce: "true"` (easy JSON typos, both plainly meant as true) read as off HERE too — doctor
    would then correctly stay silent on a gate that isn't really enforcing... except once loop.py
    reads the SAME value generously (as it now does), the two sides go from "consistently wrong
    together" to doctor actively lying that the gate is off while it is genuinely on and refusing
    every `done`. Keep this in lockstep with loop.py's copy — a parity test in test_doctor.py pins
    them to the same truth table so a future edit to one alone fails loudly, not silently."""
    value = verify.get("enforce")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


def _board_dup_risk(gh_cfg, run):
    """Board mirroring on, but NO `project.number` pinned, and the owner already has board(s): loopsmith
    resolves the board by TITLE, and if none matches its auto-title it CREATES a new one on first use -
    silently duplicating a board the loop then manages instead of the adopter's. Returns a one-line fix
    when that risk is present, else None. NOT gated on `number` (it fires precisely when number is
    unset); read-only; a can't-read returns None (no false alarm). Catches #9 at setup."""
    proj = _block(gh_cfg, "project")
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
    proj = _block(gh_cfg, "project")
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


_DEFAULT_DOCTOR_MAX_ISSUES = 10            # backlog_check.doctor_scan.max_issues (R6, see docstring below)
_DEFAULT_DOCTOR_MAX_COMMENTS = 20          # backlog_check.doctor_scan.max_comments (= sources.DEFAULT_COMMENT_LIMIT)


def _int_cfg(block, key, default):
    """doctor.py has no shared numeric-coercion helper today (backlog_check.py's own `_num` is
    private to that module, and doctor.py's existing convention -- see the dup/park threshold check
    further down in check() -- is a local try/except per call site, not a shared utility). A garbage
    value (a hand-edited `max_issues: "all"`) falls back to the default rather than crashing the
    whole doctor run, matching that existing convention exactly."""
    try:
        return int(block.get(key, default))
    except (TypeError, ValueError):
        return default


def _load_loop_script(name):
    """Cross-load a script from the sibling sdlc-loop skill (skills/sdlc-doctor/scripts/doctor.py ->
    skills/sdlc-loop/scripts/<name>.py, mirroring backlog_check.py's own `_load_velocity()` cross-skill
    idiom -- two `.parent`s up from this file's own scripts/ dir, then back down into sdlc-loop/scripts).

    A DELIBERATE, narrow exception to this file's usual no-cross-skill-import convention (see
    `_enforce_enabled`'s docstring above for that convention stated in full): `_dependency_marker_scan`
    below reuses `sources.fetch_comments` (the shared, already-tested comment-read primitive) and
    `backlog_check._BLOCK_RE` (the exact pattern `_explicit_blockers()` itself gates auto-skip on)
    VERBATIM, rather than a doctor-local reimplementation of either. A doctor-local copy of `_BLOCK_RE`
    would itself be the hardened-sibling-divergence bug class this plugin already tracks (scrub.py's
    docstring flags an existing, accepted instance) -- flagging something `precheck()` would never
    have honored, or missing something it would, as the two patterns drifted apart over time.

    Never raises -- the caller (`_dependency_marker_scan`) treats a load failure exactly like any
    other hard failure (no gh, network down): "could not run this check", reported as silent/ok
    rather than a false alarm."""
    path = _HERE.parent.parent / "sdlc-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _gh_runner(doctor_run):
    """Adapts doctor.py's own `run(full_argv_incl_binary)` convention (`_real_run`:
    `subprocess.run(args, ...)`; every call site in this file passes the binary, e.g.
    `_board_dup_risk`'s `run(["gh", "project", "list", ...])`) to `sources.fetch_comments`'s
    `run(args_excluding_binary)` convention (matching `sources._run_gh`/`ledger._run_gh`'s own shared
    convention, which prepends `"gh"` internally). The two are NOT interchangeable -- handing one
    directly to the other would double-prefix or mis-invoke `gh` -- confirmed by reading both
    conventions side by side, not assumed."""
    return lambda args: doctor_run(["gh", *args])


def _dependency_marker_scan(gh_cfg, bchk_cfg, run):
    """An issue with a comment matching `backlog_check._BLOCK_RE` ("blocked by #N" / "depends on #N"
    / ...) but NO matching marker in its own body is likely a human-authored dependency, left via the
    GitHub UI (bypassing `handoff.hand_off()` entirely), that `precheck()`'s auto-skip silently never
    sees -- mirror.py's own corpus fetch is title+body only, by design (comments are never fetched
    corpus-wide, for cost + secret-surface reasons). This is the doctor-side nudge for that blind
    spot: advisory only, nothing auto-parks from it.

    Returns (flagged: [issue numbers], scanned: int, total: int), or None on any hard failure (no
    gh, network down, sibling scripts unavailable) -- None means "could not run this check", reported
    as ok=True/silent rather than a false alarm, same fail-open convention as `_board_dup_risk` above.

    Cost design (R6, deliberate, not left to chance): candidates are issues WITHOUT a body marker --
    nearly every open goal issue in a real backlog, so `max_issues` bounds the TYPICAL per-run cost,
    not a rare worst case. Measured against a real repo, `gh issue view --json comments` averages
    ~0.62s/call; the ORIGINAL draft default of 30 would add ~18.5s to a routine `/sdlc-doctor` run (a
    4-7x regression in what's supposed to be a fast setup check, since it's hit on nearly every real
    backlog). `_DEFAULT_DOCTOR_MAX_ISSUES = 10` keeps the typical added cost to ~6s, while
    `backlog_check.doctor_scan.max_issues` stays configurable for a repo that wants a wider scan and
    is willing to pay for it. The bound is embedded in the check's own `name` (`_chk()` always prints
    `name`, pass or fail) so it is visible on every run, never silently applied.

    The LIST call itself is capped at 200 (not `max_issues`) -- matching `mirror.py`'s own
    `_OPEN_LIMIT = 200` ceiling for the identical query shape: `total` is reported against THIS
    number, so capping the list call at the same small number as the expensive per-issue comment-fetch
    loop would make `total` itself silently truncated, exactly what "no silent truncation" is about.
    A backlog bigger than 200 open goal issues still under-reports `total` -- documented, not solved
    (matching mirror.py's own accepted ceiling), rather than silently assumed complete.

    Deliberately simpler than `_explicit_blockers()`'s own body-scan: this does NOT cross-check that a
    comment's `#N` reference is to a currently-open issue -- it flags on any `_BLOCK_RE` match in a
    comment, full stop. Doctor is advisory (nothing auto-parks from this), a false positive costs a
    human one glance to dismiss, and the extra precision would mean re-deriving the doctor's own
    "which issues are open" set from the same `gh issue list` call already being made (cheap, and
    worth doing if this proves noisy in practice -- not required for #389's acceptance criteria)."""
    try:
        sources = _load_loop_script("sources")
        block_re = _load_loop_script("backlog_check")._BLOCK_RE
    except Exception:
        return None
    scan_cfg = _block(bchk_cfg, "doctor_scan")
    max_issues = _int_cfg(scan_cfg, "max_issues", _DEFAULT_DOCTOR_MAX_ISSUES)
    max_comments = _int_cfg(scan_cfg, "max_comments", _DEFAULT_DOCTOR_MAX_COMMENTS)
    repo_args = ["--repo", gh_cfg["repo"]] if gh_cfg.get("repo") else []
    goal_label = gh_cfg.get("goal_label", "sdlc:goal")
    # 200, not max_issues, for the LIST call -- see the docstring above for why.
    raw = run(["gh", "issue", "list", *repo_args, "--label", goal_label, "--state", "open",
               "--search", "sort:updated-desc", "--json", "number,body", "--limit", "200"])
    if not raw:
        return None
    try:
        issues = json.loads(raw)
    except Exception:
        return None
    if not isinstance(issues, list):
        return None
    total = len(issues)
    candidates = [i for i in issues if not block_re.search(i.get("body") or "")][:max_issues]
    flagged = []
    for i in candidates:
        n = i.get("number")
        comments = sources.fetch_comments({"discovery": {"github": gh_cfg}}, n,
                                          run=_gh_runner(run), limit=max_comments)
        if any(block_re.search(c["body"]) for c in comments):
            flagged.append(n)
    return flagged, len(candidates), total


def _version_tuple(v):
    """Parse a plain dotted-integer version ("0.9.23") into a comparable tuple, or None for
    anything that doesn't parse cleanly — never raises."""
    try:
        return tuple(int(p) for p in str(v).strip().split("."))
    except (TypeError, ValueError):
        return None


def _plugin_versions(run):
    """(installed, latest) version tuples for the loopsmith plugin itself, or None for either side
    that could not be determined — never raises; a can't-tell must never read as a false alarm (nor
    a false all-clear — see `check()`, which adds no entry at all unless BOTH sides resolve).

    `installed` comes from `claude plugin list --json` (the CLI's own record of what's actually on
    disk right now), matched by the id's plugin-name prefix (e.g. "loopsmith@loopsmith" -> the part
    before "@"). `latest` comes from the marketplace repo's OWN current marketplace.json on its
    default branch — a plain, unauthenticated, read-only fetch of a public repo (F10.5-5/#378): no
    Claude Code API exposes "the latest available version" any other way — the CLI can report what a
    plugin's version field resolved to at install/update time, never what the marketplace currently
    offers without installing it. Auto-update itself is OFF by default for a non-Anthropic
    marketplace like this one, and even ON, it only fires on its own schedule at the next session
    launch — so a stale install can otherwise persist silently indefinitely; this is the awareness
    nudge that doesn't need auto-update to be on at all."""
    installed = None
    try:
        for entry in json.loads(run(["claude", "plugin", "list", "--json"]) or "[]"):
            if isinstance(entry, dict) and str(entry.get("id", "")).split("@")[0] == "loopsmith":
                installed = _version_tuple(entry.get("version"))
                break
    except Exception:
        pass
    latest = None
    try:
        raw = run(["curl", "-fsSL", "--max-time", "5",
                   "https://raw.githubusercontent.com/swapnil-agrim/loopsmith/main/"
                   ".claude-plugin/marketplace.json"])
        for entry in (json.loads(raw or "{}").get("plugins") or []):
            if isinstance(entry, dict) and entry.get("name") == "loopsmith":
                latest = _version_tuple(entry.get("version"))
                break
    except Exception:
        pass
    return installed, latest


#: One placeholder per north-star tier, from the scaffolded template (sdlc_init.py's `_NORTH_STAR`) —
#: short, distinctive prefixes (not the full strings) so a later wording tweak to the trailing text
#: doesn't silently break the check. "filled" must clear EVERY tier (F33/#358): a north-star with only
#: Vision written up used to read as done while Strategy/Design/Architecture still held placeholder text.
_NORTH_STAR_TIERS = (
    ("Vision", "<the change you want"),
    ("Strategy", "<the few things that matter"),
    ("Design", "<the experience + the principles"),
    ("Architecture", "<the shape of the system"),
)


def check(sdlc_dir=".sdlc", run=None):
    """Return the setup checks relevant to this project's config; each is {name, ok, fix}."""
    run = run or _real_run
    base = pathlib.Path(sdlc_dir)
    cfg = _cfg(sdlc_dir)
    disc = _block(cfg, "discovery")
    kg = _block(cfg, "knowledge_graph")
    bchk = _block(cfg, "backlog_check")
    out = [_chk("project layer", (base / "config.json").exists(), "run /sdlc-init to scaffold .sdlc/")]

    if disc.get("source") == "github":
        auth = run(["gh", "auth", "status"])
        out.append(_chk("gh auth", bool(auth), "run: gh auth login"))
        gh_disc = _block(disc, "github")
        if _block(gh_disc, "project").get("enabled"):
            out.append(_chk("gh project scope", bool(auth) and "project" in auth,
                            "run: gh auth refresh -s project"))
            dup = _board_dup_risk(gh_disc, run)
            if dup:
                out.append(_chk("project.number pinned (no duplicate-board risk)", False, dup))
            unmapped = _unmapped_board_fields(gh_disc, run)
            if unmapped is not None:
                out.append(_chk("board custom fields mapped", not unmapped,
                                "the board has single-select field(s) loopsmith won't set on issues it "
                                "creates: " + ", ".join(n for n in unmapped if n) + " - map them in "
                                "discovery.github.project.custom_fields (field -> option), or backfill by "
                                "hand, else a loop-created (hand-off) issue is left blank on them."))

        # #389: NOT gated on backlog_check.enabled (nor on the `project` block above) -- a repo with
        # backlog_check OFF has zero auto-skip happening at all, so this is arguably more useful
        # there (a nudge toward turning it on); a repo with it ON benefits from catching the exact
        # blind spot that would otherwise waste a token on a goal that should have been parked.
        dm = _dependency_marker_scan(gh_disc, bchk, run)
        if dm is not None:
            flagged, scanned, total = dm
            dm_name = f"dependency markers: comments checked against body ({scanned}/{total} open goal(s))"
            dm_fix = ("comment-only dependency marker(s), no body marker, likely silently ignored by "
                      "precheck(): #" + ", #".join(str(n) for n in flagged[:10])
                      + (f" (+{len(flagged) - 10} more)" if len(flagged) > 10 else "")
                      + " -- re-file the dependency via handoff.py, or manually add a `Blocked by #N` "
                      "line to the issue body.")
            if bchk.get("enabled") is not True:
                # C1 (PR #480 review): this check is deliberately NOT gated on backlog_check.enabled
                # (see the comment above) -- but while it's off, precheck() returns "OFF" before ever
                # reaching cross_check(), so the advice above (re-file, or add a body marker) does
                # NOTHING yet: a body marker is ignored exactly as much as a comment-only one is. Say
                # so, or the fix text points at an action that fixes nothing.
                dm_fix += (' Also: backlog_check.enabled is off, so even a body marker won\'t '
                           'currently be honored by precheck() -- set `backlog_check: {"enabled": '
                           'true}` to fix that too.')
            out.append(_chk(dm_name, not flagged, dm_fix))

    if kg.get("enabled") is True:
        builder = kg.get("builder", "graphify")
        ok = bool(run([builder, "--version"]))
        fix = "run: pip install graphifyy" if builder == "graphify" else f"install the '{builder}' graph builder"
        out.append(_chk(f"{builder} installed", ok, fix))

    ns = base / "context" / "north-star.md"
    if ns.exists():
        text = ns.read_text(encoding="utf-8")
        unfilled = next((tier for tier, placeholder in _NORTH_STAR_TIERS if placeholder in text), None)
        out.append(_chk("north-star filled", unfilled is None,
                        f"{unfilled} tier still has placeholder text — run /sdlc-vision to fill the tiers"))

    # The ledger is switched on in config but the ops branch has to be created + pushed once per
    # clone; before that a teammate's `init` finds nothing to fetch. Flag it as a real setup gap with
    # the one command that fixes it — `/sdlc-ledger` runs `sync.py bootstrap` (create + seed + push).
    if _block(cfg, "ledger").get("enabled") is True:
        out.append(_chk("team ledger initialized", (base / "ledger" / ".git").exists(),
                        "run /sdlc-ledger — one command creates the ops branch, seeds your file + TEAM.md, and pushes"))

    # The permanent-refusal trap: verify.enforce on with no command refuses EVERY `done` forever, and
    # it looks like a working gate, not a misconfig. Flag it (a per-goal `verify_command` also satisfies).
    verify = _block(cfg, "verify")
    if _enforce_enabled(verify):
        out.append(_chk("verify command present (enforce is on)",
                        bool(verify.get("command")) or _any_goal_verify_command(base),
                        "verify.enforce is on but no verify.command (and no goal sets verify_command) — "
                        "every `done` is refused. Set verify.command, or turn enforce off."))

    # A backlog cross-check whose park_threshold sits BELOW its candidate threshold parks EVERYTHING it
    # finds — the opposite of "confident hits only". Flag it (only when the feature is actually on).
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
        embed = _block(bchk, "embed")
        if embed.get("enabled") is True:
            out.append(_chk("backlog cross-check embedder configured",
                            bool((embed.get("command") or "").strip()),
                            "backlog_check.embed is on but embed.command is empty — the dense layer "
                            "silently falls back to lexical-only. Set embed.command (an embedder that "
                            "reads text on stdin and prints a JSON vector), or turn embed.enabled off."))

    # With work.enabled, verify runs in a FRESH worktree that has none of your installed deps — a
    # relative interpreter path (.venv/bin/python3, node_modules/.bin) fails exit=127 on the first
    # real per-goal run. Flag it before it bites.
    vcmd = verify.get("command") or ""
    if _block(cfg, "work").get("enabled") is True and vcmd:
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

    # Awareness nudge (F10.5-5/#378): auto-update is off by default for a non-Anthropic marketplace
    # like this one, so a stale install can otherwise persist silently forever. Only reported when
    # BOTH versions actually resolve — an unreachable network or an unrecognized CLI output must
    # never show as either a false alarm or a false all-clear.
    installed, latest = _plugin_versions(run)
    if installed is not None and latest is not None:
        inst_s, latest_s = ".".join(map(str, installed)), ".".join(map(str, latest))
        out.append(_chk(f"loopsmith up to date (installed {inst_s})",
                        installed >= latest,
                        f"{latest_s} is available (you're on {inst_s}) — run: claude plugin update loopsmith"))
    return out


#: A cited path worth checking: backticked, has a separator, and is concrete. Anything with a glob or
#: a <placeholder> is a pattern, not a reference — flagging those would cry wolf, and a check nobody
#: trusts gets ignored along with the true positives.
_CITED = re.compile(r"`([^`\s]*/[^`\s]*)`")
_MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_ABSTRACT = re.compile(r"[*?<>{}\[\]]|NNNN|YYYY|\.\.\.")
#: A RELATIVE .venv/venv/node_modules path in verify.command — a worktree footgun once work.enabled.
#: An explicit `./` or `../` (repeatable) relative prefix is consumed before the dep name so
#: `./node_modules/…` and `../venv/…` are flagged too. The lookbehind still excludes a preceding
#: `/` or `.` so an ABSOLUTE path (/x/.venv/…) is never flagged.
_WORKTREE_DEP = re.compile(r"(?<![\w./])(?:\.\.?/)*(?:\.venv|venv|node_modules)/")


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
    if _block(cfg, "ledger").get("enabled") is not True:
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
    t = _block(cfg, "telemetry")
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
    b = _block(cfg, "backlog_check")
    if b.get("enabled") is not True:
        return "off (a picked goal is worked without a backlog cross-check)"
    how = ("parks a confident duplicate/obsolete/blocked goal (with proof), else annotates"
           if b.get("action", "park") == "park" else "only annotates (flag mode — never parks)")
    return "ON — pre-work cross-check " + how


def _goal_decompose_state(cfg):
    """OFF unless goal_decompose.enabled is strictly True (same truthy-string guard as
    _backlog_check_state above — a pick-path behavior must not switch on a non-bool truthy value).
    A non-dict block degrades to off; an unrecognized mode string mirrors decompose_check's own
    fallback to 'log' (loop.py) rather than reporting a state the guard itself would never take."""
    g = _block(cfg, "goal_decompose")
    if g.get("enabled") is not True:
        return "off (a picked goal is never size-checked)"
    mode = g.get("mode") or "log"
    if mode not in ("log", "park", "file"):
        mode = "log"
    how = {
        "park": "parks an oversized goal for a human to split",
        "file": 'parks an oversized goal AND files one idempotency-guarded "Decompose #N" meta-issue',
    }.get(mode, "only annotates (log mode — never parks)")
    return "ON — pre-work size classifier " + how


def _decision_gate_state(base, cfg):
    """Count the ACTIVE decisions, not the entries. A registry whose decisions are all superseded
    enforces nothing, and reporting it as ON would be exactly the false assurance this gate exists
    to remove."""
    reg = pathlib.Path(base) / "decisions.json"
    if not reg.exists():
        return "off (no registry — nothing is enforced)"
    if _block(_block(cfg, "gates"), "decision_gate").get("enabled") is False:
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
    if _block(cfg, "review").get("independent") is False:
        return "off (INLINE — the maker reviews its own work; a fresh reviewer is not spawned)"
    return "ON — a fresh, author-blind reviewer per gate, grounded in the north-star + whole repo"


def features(sdlc_dir=".sdlc"):
    """The capability dashboard: every optional feature, its CURRENT state, and the one-line
    enable. Informational (never a failure) — the answer to "what is on right now?"."""
    import os
    cfg = _cfg(sdlc_dir)
    base = pathlib.Path(sdlc_dir)
    budget = _block(cfg, "budget")
    verify = _block(cfg, "verify")
    gates = _block(cfg, "gates")
    gate = _block(gates, "hard_plan_gate")
    sg = _block(gates, "stop_gate")
    par = _block(cfg, "parallel")
    wk = _block(cfg, "work")
    rows = [
        ("model+effort auto-selection",
         "AUTO (per-goal `resolve` + per-step `resolve-step`)"
         if (cfg.get("model_selection") or "off") == "auto" else "off",
         'config: "model_selection": "auto"'),
        ("machine-checked done (verify.enforce)",
         "ON — `record done` refused without fresh `loop.py verify` evidence"
         if _enforce_enabled(verify) else "off (prose gate only)",
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
         if _block(cfg, "session_start").get("enabled") is True else "off",
         'config: "session_start": {"enabled": true}'),
        ("knowledge graph",
         "enabled" if _block(cfg, "knowledge_graph").get("enabled") is True else "off",
         'config: "knowledge_graph": {"enabled": true}'),
        ("backlog source",
         _block(cfg, "discovery").get("source") or "local-goals",
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
        ("pre-work oversized-goal classifier",
         _goal_decompose_state(cfg),
         'config: "goal_decompose": {"enabled": true}'),
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
