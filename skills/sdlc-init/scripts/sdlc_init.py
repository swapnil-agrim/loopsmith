#!/usr/bin/env python3
"""Deterministic, idempotent scaffolder for the per-project .sdlc/ layer.
Copies templates/**/<x>.tmpl -> <target>/.sdlc/<x>, skip-if-exists. Zero deps."""
import sys, pathlib

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
        dest.write_text(tmpl.read_text().replace("{{PROJECT_NAME}}", project_name))
        created.append(str(rel))
    return created, skipped


_DEMO_GOAL = """---
id: 0000
title: "Demo — write a LoopSmith hello note"
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
    dest.write_text(_DEMO_GOAL)
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
        dest.write_text(tmpl.read_text().replace("{{PROJECT_NAME}}", project_name))
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
        print("\nTip: commit .sdlc/goals/, .sdlc/project.md and .sdlc/config.json; add "
              "'.sdlc/state/' to .gitignore so machine-written loop state isn't committed "
              "(this script won't edit .gitignore for you).")
    if "--github" in flags:
        gcreated, gskipped = scaffold_github(target)
        print(f"\nsdlc-init: GitHub PM scaffolding — {len(gcreated)} created, {len(gskipped)} skipped")
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
            print("\nsdlc-init: demo goal queued — `.sdlc/goals/0000-demo.md`. Run `/sdlc-loop` to watch "
                  "the SDLC run it end to end (Goal -> Research -> ... -> Review).")
            if "--github" in flags:
                print("  github mode: file it as an issue — `gh issue create --label sdlc:goal "
                      "--title \"[Demo] LoopSmith\" --body \"<paste the demo goal body>\"` — then `/sdlc-loop` "
                      "creates the board and moves the card Backlog -> ... -> Done.")
        else:
            print("\nsdlc-init: demo goal already present (kept).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
