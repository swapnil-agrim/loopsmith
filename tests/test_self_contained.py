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
    # A virtualenv is NOT shipped surface — it is third-party code sitting in the working
    # directory. It became excludable the moment insight/ turned pip-installable (#95): an in-repo
    # .venv plus `pip install -e insight/` materialises duckdb, which contains the banned substring
    # "Temporal" in an unrelated context, so without this the documented install command reddens an
    # unrelated test. (Verified: duckdb 1.4.5 only. Nothing else that command installs trips it.)
    #
    # ONLY those two names. NOT `build`/`dist`/`*.egg-info`: this set matches at ANY depth, so
    # adding them would silently stop scanning skills/<x>/build/ — and skills/ is shipped twice
    # over, by install.sh and by marketplace.json's `source: "./"`. That is the same any-depth name
    # heuristic .gitignore rejects for /build/ and /dist/, and that test_licence_boundary.py's
    # docstring records as the first hole it had to close. Build residue is untracked and rare;
    # a missed leak in shipped surface is neither. test_leak_under_a_build_dir_is_still_caught pins
    # this — the exclusion must never grow without a test proving the guard still bites.
    skip_dirs = {"tests", "__pycache__", ".pytest_cache", ".venv", "venv"}
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in scan_suffixes:
            continue
        rel = p.relative_to(ROOT)
        # the repo's own root .sdlc/ is gitignored dogfood loop scratch, not shipped plugin surface;
        # examples/**/.sdlc/ (the committed worked example) is still scanned.
        if rel.parts and rel.parts[0] == ".sdlc":
            continue
        if skip_dirs & set(rel.parts):
            continue
        text = p.read_text(errors="ignore")
        offenders += [f"{p.relative_to(ROOT)}: {b}" for b in banned if b in text]
    assert not offenders, "host-project leakage in shipped files:\n" + "\n".join(offenders)


def test_leak_under_a_build_dir_is_still_caught(tmp_path):
    """The exclusion above must never grow to a name that can appear inside shipped surface.

    `skills/` ships via install.sh AND via marketplace.json's `source: "./"`, so a directory named
    `build` or `dist` under it is shipped surface, not residue. An earlier version of this change
    skipped both at any depth and hid a planted leak; this pins the fix. Regression-only — it
    re-implements the scan's matching rule against a fixture tree rather than importing it, because
    the real scan is hard-wired to ROOT.
    """
    skip_dirs = {"tests", "__pycache__", ".pytest_cache", ".venv", "venv"}
    banned = ("media-orch", "OnShot", "Temporal", "RunPod", "/services/", "onshot")
    leak = tmp_path / "skills" / "sdlc-loop" / "build" / "leak.md"
    leak.parent.mkdir(parents=True)
    leak.write_text("see OnShot media-orch\n", encoding="utf-8")

    offenders = []
    for p in tmp_path.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".md", ".json"):
            continue
        if skip_dirs & set(p.relative_to(tmp_path).parts):
            continue
        text = p.read_text(errors="ignore")
        offenders += [b for b in banned if b in text]
    assert offenders, "a leak under skills/<x>/build/ must still be caught — that is shipped surface"
