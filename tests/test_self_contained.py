import json, pathlib, subprocess, sys, tempfile, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_example_has_valid_sdlc():
    ex = ROOT / "examples" / "hello-sdlc"
    cfg = json.loads((ex / ".sdlc" / "config.json").read_text())   # valid JSON
    assert "budget" in cfg
    goals = list((ex / ".sdlc" / "goals").glob("[0-9]*.md"))
    assert goals, "example needs at least one numbered goal"


def test_example_loop_mechanics_run():
    """The example's loop runs end-to-end with a stub run_goal (mechanics, not the agent).
    Runs on a COPY so the committed example state isn't mutated."""
    ex = ROOT / "examples" / "hello-sdlc"
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(ex / ".sdlc", pathlib.Path(d) / ".sdlc")
        code = (
            "import importlib.util;"
            f"spec=importlib.util.spec_from_file_location('loop', r'{ROOT}/skills/sdlc-loop/scripts/loop.py');"
            "lp=importlib.util.module_from_spec(spec);spec.loader.exec_module(lp);"
            f"r=lp.run_loop(r'{d}/.sdlc', lambda g:('done',''));"
            "print(r);assert r['done']==1 and r['stopped']=='backlog-empty'"   # consumes the goal, not vacuous
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_no_onshot_specifics_in_shipped_files():
    banned = ("media-orch", "OnShot", "Temporal", "RunPod", "/services/", "onshot")
    # include .tmpl: templates materialize into every user's repo, so they're shipped surface too
    scan_suffixes = (".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".tmpl")
    # Exclude tests/ + caches: test files legitimately NAME the banned words as leakage guards
    # (test_hook.py, test_packaging_slice4.py) — that's not host-project coupling in shipped logic.
    # A virtualenv or egg-info tree is NOT shipped surface — it is third-party code sitting in the
    # working directory. Excluding it does not weaken this guard, which is about what THIS REPO
    # ships. It became necessary the moment insight/ turned pip-installable (#95): `pip install -e
    # insight/` materialises duckdb and pygments, both of which contain the banned substring
    # "Temporal" in unrelated contexts, so without this the documented install command reddens an
    # unrelated test. `*.egg-info` is matched by suffix because its name carries the package name.
    skip_dirs = {"tests", "__pycache__", ".pytest_cache", ".venv", "venv", "build", "dist"}
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in scan_suffixes:
            continue
        rel = p.relative_to(ROOT)
        # the repo's own root .sdlc/ is gitignored dogfood loop scratch, not shipped plugin surface;
        # examples/**/.sdlc/ (the committed worked example) is still scanned.
        if rel.parts and rel.parts[0] == ".sdlc":
            continue
        if skip_dirs & set(rel.parts) or any(q.endswith(".egg-info") for q in rel.parts):
            continue
        text = p.read_text(errors="ignore")
        offenders += [f"{p.relative_to(ROOT)}: {b}" for b in banned if b in text]
    assert not offenders, "host-project leakage in shipped files:\n" + "\n".join(offenders)
