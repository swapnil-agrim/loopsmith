#!/usr/bin/env python3
"""Deterministic, idempotent scaffolder for the per-project .sdlc/ layer.
Copies templates/**/<x>.tmpl -> <target>/.sdlc/<x>, skip-if-exists. Zero deps."""
import sys, json, pathlib

# script is at skills/sdlc-init/scripts/sdlc_init.py; templates sit beside scripts/
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"
GITHUB_TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "github-templates"


def scaffold(target_dir):
    target = pathlib.Path(target_dir)
    sdlc = target / ".sdlc"
    project_name = target.resolve().name
    created, skipped = [], []
    for tmpl in sorted(TEMPLATES.rglob("*.tmpl")):
        rel = tmpl.relative_to(TEMPLATES).with_name(tmpl.name[:-len(".tmpl")])  # strip literal .tmpl
        dest = sdlc / rel
        if dest.exists():
            skipped.append(str(rel))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(tmpl.read_text(encoding="utf-8").replace("{{PROJECT_NAME}}", project_name), encoding="utf-8")
        created.append(str(rel))
    return created, skipped


_DEMO_GOAL = """---
id: 0000
title: "Demo - write a LoopSmith hello note"
lane: auto
done_when: "loopsmith-demo.md exists with a one-line note"
auto_ok: true
status: pending
---

A throwaway demo goal so you can watch the SDLC run end to end. Create
`loopsmith-demo.md` containing a single line noting that LoopSmith ran this goal
through Goal -> Research -> Plan -> Plan-Review -> Implement -> Review. Delete this
goal file once you've seen it work.
"""


def scaffold_demo(target_dir):
    """Queue a small, safe, runnable demo goal so `/sdlc-loop` shows the SDLC immediately.
    Returns True if written, False if it already exists (never clobbered)."""
    dest = pathlib.Path(target_dir) / ".sdlc" / "goals" / "0000-demo.md"
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_DEMO_GOAL, encoding="utf-8")
    return True


_NORTH_STAR = """# {{PROJECT_NAME}} - North Star

The product context that grounds every goal. Fill the tiers top-down and keep each short - this is
direction, not a spec. `/sdlc-context` recalls this first; `sdlc-plan-review` checks plans against it.

## Vision (why this exists, for whom)
<the change you want to make in the world, and who it's for>

## Strategy (what we're building now)
- Priorities: <the few things that matter this cycle>
- Non-goals: <what we are deliberately NOT doing - the alignment gate uses these>

## Design (how the product should feel)
<the experience + the principles a change must respect>

## Architecture (how it's built + the rules we develop by)
<the shape of the system - the stack itself lives in project.md. Then the **rules** that govern changes
as a NUMBERED, checkable list: plan-review enforces these (a plan that violates one is blocked). Unlike
the tiers above, this tier can be AI-drafted from the codebase and user-approved.>
1. <e.g. the UI layer holds no business logic>
2. <e.g. dependencies point inward; no sibling imports across modules>
"""


def scaffold_vision(target_dir):
    """Scaffold the opt-in vision-first north-star (.sdlc/context/north-star.md), skip-if-exists.
    Opt-in via --vision so plain /sdlc-init stays drop-in. Returns True if written, False if present."""
    target = pathlib.Path(target_dir)
    dest = target / ".sdlc" / "context" / "north-star.md"
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_NORTH_STAR.replace("{{PROJECT_NAME}}", target.resolve().name), encoding="utf-8")
    return True


_CURSOR_RULE = """---
description: Goal-Based SDLC - standing discipline for every change (LoopSmith)
globs:
alwaysApply: true
---

# Goal-Based SDLC (LoopSmith)

Cursor has no UserPromptSubmit hook, so this always-applied rule is the standing policy. For any
non-trivial or implementation task, do NOT jump straight to coding - follow the phases and state which
one you are on:

1. **Goal** - restate the objective as one concrete, checkable goal.
2. **Research** - blast radius: affected files, existing patterns, constraints.
3. **Plan** - steps, files, tests, definition-of-done.
4. **Plan-Review** - adversarially review the plan BEFORE implementing. Never skip.
5. **Implement** - test-first (red -> green -> refactor); minimal code to pass.
6. **Review** - evidence before claims: run the checks, paste the output; a KPI + qualitative scan.
7. **Retrospective** - capture lessons.

**Intent-aware:** trivial / conversational / read-only requests may be answered directly - but say so
explicitly. The moment it turns into a code change, switch to the spine.

**Never run an irreversible or expensive action** (deploy, delete, overwrite, spend, migrate)
unattended - stop and ask.

**Executors (portable):** this host has no `superpowers` / `code-review` companion, so each phase runs
via LoopSmith's portable executor - the disciplines in `skills/sdlc-*/SKILL.md` (`sdlc-brainstorm` ->
Goal, `sdlc-plan` -> Plan, `sdlc-implement` -> Implement, `sdlc-review` + `sdlc-verify` -> Review).
Same discipline as the Claude companions, portable.

**Backlog + helpers (optional):** the loop, model-selection, status and KG helpers are plain, zero-dep
`python3` - run them from your LoopSmith checkout via Cursor's terminal, e.g.
`python3 <loopsmith>/skills/sdlc-loop/scripts/loop.py next .sdlc` or
`python3 <loopsmith>/skills/sdlc-model/scripts/predict.py "<goal>"`.
"""


def scaffold_cursor(target_dir):
    """Scaffold the Cursor host adapter: an always-apply rule at .cursor/rules/sdlc.mdc carrying the
    SDLC discipline (Cursor's analog of the Claude UserPromptSubmit hook). Opt-in via --cursor,
    skip-if-exists. Returns True if written, False if present."""
    dest = pathlib.Path(target_dir) / ".cursor" / "rules" / "sdlc.mdc"
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_CURSOR_RULE, encoding="utf-8")
    return True


def scaffold_github(target_dir):
    """Materialize the GitHub PM scaffolding (issue templates, auto-add workflow, label rule, the
    critical-insight template) into <target>/.github/, skip-if-exists. Opt-in via the --github flag."""
    target = pathlib.Path(target_dir)
    project_name = target.resolve().name
    created, skipped = [], []
    for tmpl in sorted(GITHUB_TEMPLATES.rglob("*.tmpl")):
        rel = tmpl.relative_to(GITHUB_TEMPLATES).with_name(tmpl.name[:-len(".tmpl")])
        dest = target / ".github" / rel
        if dest.exists():
            skipped.append(str(rel))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(tmpl.read_text(encoding="utf-8").replace("{{PROJECT_NAME}}", project_name), encoding="utf-8")
        created.append(str(rel))
    return created, skipped


def main(argv):
    flags = {a for a in argv[1:] if a.startswith("--")}
    pos = [a for a in argv[1:] if not a.startswith("--")]
    target = pos[0] if pos else "."
    if not pathlib.Path(target).is_dir():
        print(f"sdlc-init: target directory does not exist: {target}", file=sys.stderr)
        return 1
    created, skipped = scaffold(target)
    root = pathlib.Path(target).resolve()
    print(f"sdlc-init: {len(created)} created, {len(skipped)} skipped (target: {root})")
    for c in created:
        print(f"  + .sdlc/{c}")
    for s in skipped:
        print(f"  = .sdlc/{s} (exists, kept)")
    if created:
        print("\nTip: commit .sdlc/goals/, .sdlc/project.md and .sdlc/config.json. The machine-written "
              "dirs ('.sdlc/state/', '.sdlc/ledger/', '.sdlc/work/') must be git-ignored: run "
              "/sdlc-setup, or `setup.py ignore .` (it never clobbers an ignore rule you already set, "
              "and can target .git/info/exclude for a local-only adoption). This script edits no "
              "ignore files itself.")
    if "--github" in flags:
        gcreated, gskipped = scaffold_github(target)
        print(f"\nsdlc-init: GitHub PM scaffolding - {len(gcreated)} created, {len(gskipped)} skipped")
        for c in gcreated:
            print(f"  + .github/{c}")
        for s in gskipped:
            print(f"  = .github/{s} (exists, kept)")
        if gcreated:
            print("\nGitHub Projects board: create the board, then set repo variable SDLC_PROJECT_URL "
                  "(your board URL) and secret ADD_TO_PROJECT_PAT (a PAT with project write scope) to "
                  "enable auto-add of new issues to the Backlog.")
    if "--demo" in flags:
        if scaffold_demo(target):
            print("\nsdlc-init: demo goal queued - `.sdlc/goals/0000-demo.md`. Run `/sdlc-loop` to watch "
                  "the SDLC run it end to end (Goal -> Research -> ... -> Review).")
            if "--github" in flags:
                print("  github mode: file it as an issue - `gh issue create --label sdlc:goal "
                      "--title \"[Demo] LoopSmith\" --body \"<paste the demo goal body>\"` - then `/sdlc-loop` "
                      "creates the board and moves the card Backlog -> ... -> Done.")
        else:
            print("\nsdlc-init: demo goal already present (kept).")
    if "--vision" in flags:
        if scaffold_vision(target):
            print("\nsdlc-init: vision-first north-star queued - `.sdlc/context/north-star.md`. Run "
                  "`/sdlc-vision` to fill the tiers (Vision -> Strategy -> Design -> Architecture); "
                  "`/sdlc-context` then grounds every goal in it.")
        else:
            print("\nsdlc-init: north-star already present (kept).")
    if "--cursor" in flags:
        wrote = scaffold_cursor(target)
        # Cursor never has the superpowers/code-review companions - pin companions:off so the portable
        # executors are used without a pointless `claude plugin list` probe (fail-open if config's odd).
        try:
            cfgp = pathlib.Path(target) / ".sdlc" / "config.json"
            cfg = json.loads(cfgp.read_text(encoding="utf-8"))
            if cfg.get("companions") != "off":
                cfg["companions"] = "off"
                cfgp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        if wrote:
            print("\nsdlc-init: Cursor adapter queued - `.cursor/rules/sdlc.mdc` (always-applied SDLC "
                  "discipline, the hook analog). companions pinned off (portable executors). The zero-dep "
                  "python helpers run from your LoopSmith checkout via Cursor's terminal.")
        else:
            print("\nsdlc-init: Cursor rule already present (kept).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
