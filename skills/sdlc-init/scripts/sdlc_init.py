#!/usr/bin/env python3
"""Deterministic, idempotent scaffolder for the per-project .sdlc/ layer.
Copies templates/**/<x>.tmpl -> <target>/.sdlc/<x>, skip-if-exists. Zero deps."""
import sys, pathlib

# script is at skills/sdlc-init/scripts/sdlc_init.py; templates sit beside scripts/
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"


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


def main(argv):
    target = argv[1] if len(argv) > 1 else "."
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
