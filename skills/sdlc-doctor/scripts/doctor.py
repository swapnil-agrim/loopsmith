#!/usr/bin/env python3
"""sdlc-doctor: a setup check-up. Audit only what THIS project's config makes relevant — github board
-> gh auth + project scope; KG enabled -> the builder; vision-first -> the north-star; always -> the
.sdlc layer — and report each check with the exact one-line fix. The command runner is injectable so
the logic is hermetically testable. Zero-dep."""
import sys, json, pathlib


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

    if kg.get("enabled") is True:
        builder = kg.get("builder", "graphify")
        ok = bool(run([builder, "--version"]))
        fix = "run: pip install graphifyy" if builder == "graphify" else f"install the '{builder}' graph builder"
        out.append(_chk(f"{builder} installed", ok, fix))

    ns = base / "context" / "north-star.md"
    if ns.exists():
        filled = "<the change you want" not in ns.read_text(encoding="utf-8")
        out.append(_chk("north-star filled", filled, "run /sdlc-vision to fill the tiers"))

    # companions (optional): superpowers + code-review power phases 1/3/5/6 when present; LoopSmith's
    # portable sdlc-* executors are the absent-safe fallback everywhere else — absent is never a failure.
    if (cfg.get("companions") or "auto") != "off":
        plugins = run(["claude", "plugin", "list"]) or ""
        for comp in ("superpowers", "code-review"):
            here = comp in plugins
            out.append(_chk(f"{comp}: {'present' if here else 'absent — portable executor used'}", True, ""))
    return out


def _ledger_entries(base):
    """Count committed ledger lines. Read-only and fail-open — the dashboard never breaks on a
    half-written file."""
    total = 0
    entries = pathlib.Path(base) / "ledger" / "entries"
    if not entries.exists():
        return 0
    for path in sorted(entries.glob("*.jsonl")):
        try:
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            continue
    return total


def features(sdlc_dir=".sdlc"):
    """The capability dashboard: every optional feature, its CURRENT state, and the one-line
    enable. Informational (never a failure) — the answer to "what is on right now?"."""
    import os
    cfg = _cfg(sdlc_dir)
    base = pathlib.Path(sdlc_dir)
    budget = cfg.get("budget") or {}
    verify = cfg.get("verify") or {}
    gate = (cfg.get("gates") or {}).get("hard_plan_gate") or {}
    par = cfg.get("parallel") or {}
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
        ("knowledge graph",
         "enabled" if (cfg.get("knowledge_graph") or {}).get("enabled") is True else "off",
         'config: "knowledge_graph": {"enabled": true}'),
        ("backlog source",
         (cfg.get("discovery") or {}).get("source") or "local-goals",
         'config: "discovery": {"source": "github"}'),
        ("team ledger",
         ("ON — %d entr%s in .sdlc/ledger/entries/" % (_ledger_entries(base), "y" if _ledger_entries(base) == 1 else "ies"))
         if (cfg.get("ledger") or {}).get("enabled") is True else "off (nothing is recorded)",
         'config: "ledger": {"enabled": true}'),
        ("slice parallelism",
         ("ON — up to %s concurrent slices per wave" % par.get("max_concurrent", 3))
         if par.get("enabled") is True else "off (a goal's slices run one after another)",
         'config: "parallel": {"enabled": true, "max_concurrent": 3}'),
        ("PR review pipeline",
         ("ON — merges %s" % ("auto (auto_merge on)" if (cfg.get("review") or {}).get("auto_merge") is True
                              else "parked for a human (auto_merge off)"))
         if (cfg.get("review") or {}).get("enabled") is True else "off (PRs are not managed by the loop)",
         'config: "review": {"enabled": true, "base": "<branch>"}'),
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
        return 0
    print("usage: doctor.py check [sdlc_dir] | features [sdlc_dir]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
