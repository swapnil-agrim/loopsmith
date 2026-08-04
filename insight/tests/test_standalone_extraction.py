# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #297 [E15.S3]. The proof that makes "insight/ is extractable" a fact checked on every
push instead of a claim discovered false the day someone tries it (spec §2.1.3/§2.1.4). Copies
insight/ ALONE into a fresh temp dir -- no skills/, hooks/, or root tests/ alongside it -- and
runs its full suite there as a child pytest process, then asserts more than "exited 0": that
`insight` resolved from INSIDE the copy (not this repo, not a stale site-packages install), that
the child passed at least a known floor (not silently all-skipped), that every skip it reports is
one of a small named set (a NEW skip reason fails loudly rather than hiding behind this list), and
that the web-check entry point -- which must live under insight/ itself, not the repo root, or it
would not travel with an extracted insight/ at all -- runs inside the copy and reports the exact
expected SKIP, not merely "some exit code".

NO pip install for the copy-and-run half, below. `shutil.copytree` needs no build step, and this
machine's ambient setuptools (58.0.4) is below insight/pyproject.toml's >=77 floor, so even
`pip install --no-deps --no-build-isolation` fails OFFLINE here; plain `--no-deps` uses build
isolation, which fetches setuptools>=77 over the network verify explicitly must not require
(.sdlc/config.json's own verify._command argues this at length for the exact same reason).
`test_packaging_allowlist_matches_what_a_real_install_actually_ships` below is the one exception:
it DOES install, for real, but only when `CI` is set (see that test's own docstring).

RECURSION GUARD: this file itself lives under insight/tests/, so the copy contains a copy of THIS
file too, and the child's own pytest run would otherwise collect it and spawn a grandchild,
forever. `_CHILD_SENTINEL`, an env var this test sets before spawning its child and checks at
import time, breaks the cycle: if already set, this whole module skips itself (one skip,
`allow_module_level=True`) instead of recursing. An `--ignore` of this file's own path was
considered and rejected: renaming this file would silently stop excluding it with no failure
anywhere, whereas the sentinel travels with the file under any name.

CORRECTION TO AN EARLIER DRAFT OF THIS PLAN (issue #297 plan review, A2): `python3 -I` /
`PYTHONNOUSERSITE=1` were considered for the child, to stop this machine's stale ambient
`insight`/`loopsmith-insight` installs (in user site-packages) from satisfying its imports.
Verified directly: on this machine `pytest` itself lives ONLY in user site-packages, so both flags
make `pytest` unimportable in the child -- confirmed with `ModuleNotFoundError: No module named
'pytest'` both ways. A plain `python3 -m pytest` already resolves `insight` from inside the copy.

THE REAL MECHANISM, restated correctly (an earlier draft misattributed this to
`insight/tests/__init__.py` + pytest's "prepend" import mode -- plan review finding A2): invoking
`python3 -m pytest ...` is what puts the current working directory on `sys.path[0]` -- a plain
CPython behaviour of the `-m` flag, nothing pytest-specific and nothing to do with any `__init__.py`
anywhere. The `extracted` fixture below runs the child with `cwd` set to the copy's PARENT (whose
only child is the `insight/` copy itself), so that parent directory lands on `sys.path[0]` ahead of
any site-packages entry, and `import insight` resolves to `<that parent>/insight` -- the copy --
as an ordinary top-level package import. Trusting that this invariant holds forever is exactly the
kind of thing that should be checked, not assumed: `test_child_planted_self_check_ran_and_passed`
below is the real safety net, and it inspects the OUTCOME of the actual child process, via a test
planted inside the copy that runs as part of the same single child invocation (see that test's own
docstring, and the sibling file it inspects,
insight/tests/test_insight_imports_from_the_tree_this_test_file_lives_in.py, for why the resolution
check itself lives in a separate, unguarded file rather than in this one)."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

_CHILD_SENTINEL = "_INSIGHT_STANDALONE_PROOF_CHILD"

if os.environ.get(_CHILD_SENTINEL):
    pytest.skip(
        "already inside the child copy's own pytest run (recursion guard) -- the outer run "
        "already performed this proof",
        allow_module_level=True,
    )

INSIGHT = pathlib.Path(__file__).resolve().parents[1]   # insight/ itself

# ponytail: a MINIMUM, not the exact count. insight/'s suite grows on every unrelated goal that
# adds a metric/gap/dash test, so pinning the exact number here would make this file a merge
# conflict (or worse, a false failure) on PRs that never touch extraction. Raise this floor
# deliberately, in the same PR, when the real count grows well past it; never lower it to turn a
# real regression green. Maintenance: sdlc-retro or a future goal that measures the real count and
# finds this floor is now far below it should raise it -- that drift is a feature (cheap to fix),
# not a bug.
MIN_PASSED = 1090

# The complete set of skip reasons the child run is allowed to report. An unlisted reason means
# something NEW silently started skipping in the copy -- fail loudly, don't let it hide here.
# Not every entry fires on every Python version (tomllib is 3.11+ stdlib, so that skip vanishes
# on the CI matrix's 3.12 leg) -- that's fine, this list only bounds what CAN appear, not what
# MUST.
ALLOWED_SKIP_REASONS = (
    "skills/ not present in this checkout",
    "outer repo's real .sdlc/insight.duckdb not found -- expected in CI",
    ".sdlc/ledger does not exist in this checkout",
    "got empty parameter set for (command, issue)",
    "could not import 'tomllib'",
    "already inside the child copy's own pytest run (recursion guard)",
)

#: The exact node id the child must report PASSED for `test_child_planted_self_check_ran_and_passed`
#: to accept the run -- see insight/tests/test_insight_imports_from_the_tree_this_test_file_lives_in.py.
_PLANTED_SELF_CHECK = (
    "test_insight_imports_from_the_tree_this_test_file_lives_in.py::"
    "test_insight_imports_from_the_tree_this_test_file_lives_in PASSED"
)


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    """One copy, one child pytest run, shared by every assertion below -- the run costs ~44s;
    repeating it per-assertion would multiply verify's wall time for no new signal. Copies
    insight/ AS a directory named `insight` (not its contents flattened into the temp root) --
    several of insight/'s own tests (e.g. test_cli.py's REPO_ROOT) resolve paths via
    `parents[N]` assuming exactly that nesting; flattening produces a confusing, unrelated
    FileNotFoundError instead of the intended proof.

    `-v` rather than `-q` (issue #297 plan review, A2): prints every outcome by name, including
    PASSED, not only the `-rs` skip summary -- so `test_child_planted_self_check_ran_and_passed`
    can read the real child run's own transcript for one specific test's result, instead of
    trusting a second, decoupled subprocess to stand in for it.

    The child's env drops PYTHONPATH and PYTEST_ADDOPTS (issue #297 plan review, A3) before
    spawning: this proof's own safety net rests on `cwd` landing first on `sys.path[0]` (see this
    module's docstring), which is a real but unenforced CPython behaviour -- an ambient PYTHONPATH
    entry pointing at a stale `insight` install, or a PYTEST_ADDOPTS that changes pytest's own
    import mode (e.g. `--import-mode=importlib`), could each independently reintroduce the exact
    shadowing bug this proof exists to catch, silently, from outside this file entirely. Dropping
    both removes the two most likely ways that could happen without a code change here -- on top
    of, not instead of, `test_child_planted_self_check_ran_and_passed`'s own direct check of the
    outcome."""
    dest_root = tmp_path_factory.mktemp("standalone")
    dest = dest_root / "insight"
    shutil.copytree(INSIGHT, dest)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    env[_CHILD_SENTINEL] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(dest / "tests"), "-v", "-rs"],
        cwd=str(dest_root), env=env, capture_output=True, text=True, timeout=180,
    )
    return proc, dest_root, dest


def test_copy_has_no_plugin_dirs(extracted):
    """Clause 1's own precondition, checked rather than assumed: skills/, hooks/, and a root
    tests/ must genuinely be absent, or a pass here proves nothing about extraction."""
    _, dest_root, _ = extracted
    assert not (dest_root / "skills").exists()
    assert not (dest_root / "hooks").exists()
    assert not (dest_root / "tests").exists()


def test_child_exits_zero(extracted):
    proc, _, _ = extracted
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_child_planted_self_check_ran_and_passed(extracted):
    """Decision 4(a), corrected (issue #297 plan review, A2): the real safety net against this
    machine's stale ambient `insight` / `loopsmith-insight` installs is a test PLANTED inside the
    copy (insight/tests/test_insight_imports_from_the_tree_this_test_file_lives_in.py), which runs
    as part of the SAME single child pytest invocation `extracted` already spawned -- never a
    second, separately started process that only shares `cwd` with the real run and proves nothing
    about it. This asserts that exact test reported PASSED in the child's own captured stdout (`-v`
    makes every outcome show up by name), not merely that the child exited 0 overall -- a
    collection-time skip of that one file, for instance, would still leave `returncode == 0` for
    the whole run if nothing else failed."""
    proc, _, _ = extracted
    assert _PLANTED_SELF_CHECK in proc.stdout, (
        "the planted resolution self-check did not report PASSED in the child's own run -- either "
        "it did not execute at all, or `insight` resolved from somewhere other than the copy:\n"
        + proc.stdout[-4000:]
    )


def test_child_web_check_runs_under_insight_and_reports_the_expected_skip(extracted):
    """Clause 5: the standalone proof must exercise the web checks, not just the Python suite --
    and clause 4: the entry point must live under insight/ itself, or it would not even be
    present in this copy to run. Asserts the SPECIFIC skip text, not any exit 0 -- exit 0 is also
    what you get from a script that isn't there at all if you assert it carelessly (see this
    story's own PR body for the captured red when verify_web.py is moved back to the root)."""
    _, _, dest = extracted
    entry = dest / "verify_web.py"
    proc = subprocess.run([sys.executable, str(entry)], cwd=str(dest),
                           capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SKIP" in proc.stdout and "insight/web/package.json" in proc.stdout, proc.stdout


def test_child_passed_at_least_the_known_floor(extracted):
    proc, _, _ = extracted
    m = re.search(r"(\d+) passed", proc.stdout)
    assert m, proc.stdout
    assert int(m.group(1)) >= MIN_PASSED, proc.stdout


def test_child_skips_are_all_named_and_accounted_for(extracted):
    """Decision 4(c): an all-skip run must not read as green. Every SKIPPED line is checked
    against the allowlist; nothing here requires every allowlist entry to have fired."""
    proc, _, _ = extracted
    for line in proc.stdout.splitlines():
        if line.startswith("SKIPPED"):
            assert any(reason in line for reason in ALLOWED_SKIP_REASONS), line


def test_packaging_allowlist_matches_what_a_real_install_actually_ships(extracted):
    """A1 (issue #297 plan review, should-fix): every assertion above proves insight/ COPIES and
    RUNS standalone -- none of them proves clause 1's other verb, "installs it". A
    packages=[...] / package-data allowlist gap in insight/pyproject.toml passes every one of them,
    because `shutil.copytree` preserves every file regardless of what pyproject.toml declares --
    demonstrated: add `insight/newmetric/__init__.py` without adding `"insight.newmetric"` to
    packages=[...] and every assertion above still passes. That is the bug class already recurred
    three times (#108 insight.metrics, #116 insight.gaps, #262 insight.dash), each one a
    package-data glob missing for a package's first non-.py asset. A REAL, non-editable
    `pip install --no-deps --target <tmp>` of the SAME copy the assertions above already made is
    the only way to catch it -- a text comparison against pyproject.toml's own `packages=[...]`
    would only check that the declaration is internally consistent with itself, never that a real
    wheel build actually honours it.

    GATED TO CI, NOT THE LOCAL GATE: build isolation must fetch setuptools>=77 over the network
    (insight/pyproject.toml:2-6's own floor) -- this machine's ambient setuptools is 58.0.4, well
    below it, so even `pip install --no-deps --no-build-isolation` fails OFFLINE here, the exact
    reason `.sdlc/config.json`'s own verify.command runs no install step at all (see that file's
    own `_command` note, and this module's own docstring). `CI` is GitHub Actions' own,
    automatically-set environment variable -- reusing it needs no new config knob, and it is only
    ever set in an environment that already has network access.

    NO tomllib DEPENDENCY, DELIBERATELY: parses pyproject.toml's `[tool.setuptools.package-data]`
    table with a plain regex, the same stdlib-only, cross-version-portable approach
    insight/tests/test_packaging.py's own text-based tests already use for the identical reason
    (tomllib is 3.11+ only, and this machine's own Python is 3.9) -- a `pytest.importorskip
    ("tomllib")` gate here would make this check unable to ever actually RUN on this machine, CI
    env forced or not, which would make issue #297's own required red demonstration (moving
    newmetric/ without declaring it) impossible to produce for real on this session's own machine.

    ponytail: this proves two things a real wheel install could silently drop -- (1) every source
    directory with an `__init__.py` (except `insight.tests`, deliberately excluded from
    packages=[...] -- test code never ships) makes it into the installed tree, and (2) every file
    matching a package-data glob DECLARED in pyproject.toml makes it into the installed tree at the
    same relative path. It does NOT prove the reverse: a brand-new non-.py asset type added under
    an ALREADY-packaged package, with no package-data glob naming it at all, is invisible to this
    check -- it only walks DECLARED globs, never every file under insight/. Catching that would
    mean asserting "every non-.py file under insight/ ships", which false-positives on this repo's
    own real project metadata (pyproject.toml, VERSION, README.md, LICENSE, HEADER.txt) that
    legitimately never ships as per-package data. Not solved here -- the next one of these (a
    #108/#116/#262 for a fourth asset type) still needs a human to notice it, the same way the
    first three were."""
    if not os.environ.get("CI"):
        pytest.skip(
            "packaging install check needs a real network-backed wheel build (build isolation "
            "fetches setuptools>=77 -- insight/pyproject.toml:2-6 -- and this machine's ambient "
            "setuptools is 58.0.4); gated to CI (env var CI, set automatically by GitHub Actions) "
            "so the local gate stays offline, matching .sdlc/config.json's verify.command"
        )

    _, _, dest = extracted
    text = (dest / "pyproject.toml").read_text(encoding="utf-8")

    def _relparts(dotted):
        return dotted.split(".")[1:]   # drop the leading "insight" segment

    never_shipped = {"insight.tests"}  # test code, deliberately excluded from packages=[...]
    source_packages = sorted(
        "insight" if p.parent == dest else "insight." + ".".join(p.parent.relative_to(dest).parts)
        for p in dest.rglob("__init__.py")
    )
    shippable = [name for name in source_packages if name not in never_shipped]

    # `[tool.setuptools.package-data]` entries look like `"insight.metrics" = ["*.sql"]` --
    # capture the dotted key and the bracketed list text, then pull the quoted globs out of that
    # list text separately. Text/regex, not tomllib, deliberately (see this test's own docstring).
    package_data = [
        (f"insight.{key}", re.findall(r'"([^"]+)"', globs_text))
        for key, globs_text in re.findall(r'"insight\.([\w.]+)"\s*=\s*\[([^\]]*)\]', text)
    ]

    with tempfile.TemporaryDirectory() as install_dir:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", install_dir, str(dest)],
            capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, "pip install failed:\n" + proc.stdout + proc.stderr
        installed = pathlib.Path(install_dir)

        missing_packages = [
            name for name in shippable
            if not installed.joinpath("insight", *_relparts(name), "__init__.py").is_file()
        ]
        assert not missing_packages, (
            "these packages exist in insight/'s source tree but did not make it into the "
            "installed wheel -- pyproject.toml's packages=[...] allowlist is missing them: "
            f"{missing_packages}"
        )

        missing_data = []
        for dotted, globs in package_data:
            src_dir = dest.joinpath(*_relparts(dotted)) if _relparts(dotted) else dest
            dst_dir = installed.joinpath("insight", *_relparts(dotted))
            for pattern in globs:
                for f in sorted(src_dir.glob(pattern)):
                    rel = f.relative_to(src_dir)
                    if not (dst_dir / rel).is_file():
                        missing_data.append(str(dst_dir / rel))
        assert not missing_data, (
            "these package-data files exist in source but are missing from the installed wheel "
            f"(pyproject.toml's package-data globs are not being honoured): {missing_data}"
        )
