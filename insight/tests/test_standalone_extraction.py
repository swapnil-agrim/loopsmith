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
import time, breaks the cycle. An `--ignore` of this file's own path was considered and rejected:
renaming this file would silently stop excluding it with no failure anywhere, whereas the
sentinel travels with the file under any name.

CORRECTION TO AN EARLIER DRAFT OF THIS GUARD (issue #297 review, F2): skipping whenever the
sentinel was merely PRESENT -- with no second check -- meant a stale `_INSIGHT_STANDALONE_PROOF_
CHILD=1` left exported in a developer's shell silently disabled every assertion below in the
OUTER, real run, reading as just one more ordinary skip in a full suite. The guard now also
requires this module to be structurally inside a copy (see the comment on the guard's `if` just
below `INSIGHT`'s definition for the full four-case reasoning) before it will skip -- the
sentinel alone is no longer sufficient.

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
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest

_CHILD_SENTINEL = "_INSIGHT_STANDALONE_PROOF_CHILD"

INSIGHT = pathlib.Path(__file__).resolve().parents[1]   # insight/ itself

# RECURSION GUARD, corrected (issue #297 review, F2): the original guard skipped on the
# sentinel's mere PRESENCE. Verified failure: a stale `_INSIGHT_STANDALONE_PROOF_CHILD=1` left
# exported in a developer's shell (e.g. from a killed earlier run, or copy-pasted from this file)
# silently disables all seven proof assertions below in the OUTER, REAL run -- and it reads as
# just one more ordinary skip in a full suite, not a red flag. That is the exact "a guard that
# silently skips is worse than none" failure this story is about, reproduced in the guard meant
# to prevent it. Fix: skip only when the sentinel is set AND this module is structurally inside a
# copy, never on the sentinel alone -- the structural tell is that the real repo has a `skills/`
# sibling next to `insight/` and a copy (made by `shutil.copytree` of insight/ ALONE, see the
# `extracted` fixture below) never does. All four cases, reasoned through:
#   (a) real repo, no sentinel      -> neither condition holds                 -> proof runs
#   (b) real repo, stale sentinel   -> sentinel set, but skills/ IS present    -> proof STILL
#                                       runs (this is the hole this fix closes)
#   (c) the child copy              -> sentinel set AND skills/ is absent      -> skips once,
#                                       recursion broken
#   (d) a genuinely extracted insight/ repo -> its OUTER run has no sentinel (nobody set it) ->
#       proof runs; ITS OWN child copy has both conditions set -> skips -- still exactly one
#       level deep, same shape as (c).
# The structural check ALONE (drop the sentinel, skip whenever skills/ is absent) would also
# break the recursion, but it would then skip the proof in a genuinely extracted repo's OUTER
# run too (case (d)'s outer run has no skills/ sibling either, extraction being the point) --
# precisely the run where extraction correctness matters most. Hence both conditions are
# required together, not either alone.
if os.environ.get(_CHILD_SENTINEL) and not (INSIGHT.parent / "skills").is_dir():
    pytest.skip(
        "already inside the child copy's own pytest run (recursion guard) -- the outer run "
        "already performed this proof",
        allow_module_level=True,
    )

# ponytail: a MINIMUM, not the exact count. insight/'s suite grows on every unrelated goal that
# adds a metric/gap/dash test, so pinning the exact number here would make this file a merge
# conflict (or worse, a false failure) on PRs that never touch extraction. Raise this floor
# deliberately, in the same PR, when the real count grows well past it; never lower it to turn a
# real regression green. Maintenance: sdlc-retro or a future goal that measures the real count and
# finds this floor is now far below it should raise it -- that drift is a feature (cheap to fix),
# not a bug.
#
# Issue #298 ([E15.S4]): raised from 1090. Converting the four skip-guarded files
# (test_metric_severity_rank.py, test_gaps_severity_vocabulary.py, test_metric_23_gate_catch_rate.py,
# test_gaps_compare_mirrors_pipeline.py) off skills/-path reads onto insight/contract/'s fixtures,
# plus the new contract test files (Steps 6-9), measured a real standalone-copy count of 1131
# passed / 12 skipped -- reproduced three times this session on CPython 3.9.6, twice by an
# independent reviewer. An earlier report of 1137 in this PR's own implement note was WRONG and is
# not what this floor was set from; the number here is the one that reproduces. Raised to
# comfortably below the real count, not to it exactly, so an unrelated goal adding tests does not
# have to touch this line.
MIN_PASSED = 1125

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
    # issue #299 [E16.S1]: insight/api/'s first fastapi-touching test file, same position
    # test_store.py's duckdb gate was in at #99 -- these two reasons are added defensively for a
    # contributor machine without fastapi/httpx ambient; on this dev machine and in CI both
    # packages are present (fastapi declared in pyproject.toml, httpx installed via ci.yml's
    # insight-job line, Task 2), so this file actually runs rather than skips here -- see
    # .sdlc/plans/299.md Decision 3 for the scratch demonstration that these strings are correct.
    "could not import 'fastapi'",
    "could not import 'httpx'",
    # issue #300 [E16.S2]: insight/api/models.py's first pydantic-touching test file, pre-added
    # defensively the same way #299 pre-added the fastapi/httpx pair above before any file
    # actually exercised the skip path -- pydantic is declared in pyproject.toml and present on
    # this dev machine and in CI, so this file actually runs rather than skips here.
    "could not import 'pydantic'",
    # issue #306 [E18.S1]: insight/accounts/'s first argon2-cffi-touching test files
    # (test_accounts_hashing.py, test_accounts_store.py, test_accounts_symmetry.py,
    # test_accounts_no_leak.py, and the gated tests in test_cli_users.py) guard with
    # pytest.importorskip("argon2") -- argon2-cffi is declared in pyproject.toml but NOT
    # installed on this dev machine, so this child copy actually exercises this skip here (see
    # .sdlc/plans/306.md Decision 2, point 3). The KDF-unavailable seam test
    # (test_accounts_hashing_kdf_unavailable.py) and the CLI's ungated tests are deliberately NOT
    # gated on this string -- they must PASS, not skip, on this exact machine.
    "could not import 'argon2'",
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
    outcome.

    PROCESS-GROUP TIMEOUT KILL (issue #297 review, F3): a plain `subprocess.run(..., timeout=...)`
    only ever reaches the ONE process it started. If the F2 recursion guard above were ever wrong
    -- or a future edit reopens the hole it closes -- the child pytest spawns a grandchild pytest
    of its own, and on timeout `run()`'s internal kill reaches only the child, leaving the
    grandchild (and whatever IT has already spawned) running, orphaned, each still free to keep
    spawning further descendants. Reproduced for real, in a scratch copy, sentinel deliberately
    removed, never in this repo: the triggering command returned on its own timeout but orphaned
    `pytest` processes kept multiplying after it did, and 26+ had to be killed by hand. Fix:
    `start_new_session=True` puts the child in a NEW process group (it becomes that group's
    leader), so on timeout this code kills the WHOLE group (`os.killpg` + `SIGKILL`) -- child,
    grandchild, and anything the grandchild already spawned -- not just the one handle `run()`
    would have reached. `Popen` + `communicate(timeout=...)` replaces `subprocess.run` specifically
    because `run()` raises `TimeoutExpired` and gives back no handle to kill anything with; by the
    time that exception is caught there, the process is already unreachable through the object
    `run()` returned. The result is repackaged as a `CompletedProcess` so every assertion below
    keeps using `proc.returncode` / `proc.stdout` / `proc.stderr` exactly as before.

    On an actual timeout, the failure names the likely cause instead of surfacing as a bare
    `TimeoutExpired` traceback on every fixture-dependent test below with no hint why -- pointing
    at the sentinel and the recursion guard is the whole reason someone reading a red CI run here
    would know where to look first.

    TIMEOUT VALUE: 600s, raised from the original 180s. The real run on this machine takes ~44s;
    180s already looked generous next to that, and yet plan review's own estimate for this same
    run elsewhere is ~90s, i.e. this machine's timing does not bound what the CI matrix's (slower)
    runners will see. 600s keeps roughly a 6-7x margin over the slower of those two real numbers,
    which is the point: a timeout sized to what was observed only on the fastest box in the
    fleet is exactly the kind of guard that quietly stops meaning what it says, the same way F2's
    presence-only sentinel check did."""
    dest_root = tmp_path_factory.mktemp("standalone")
    dest = dest_root / "insight"
    # ignore=... node_modules (issue #301 [E16.S3], .sdlc/plans/301.md Decision g2): a locally-warm
    # insight/web/node_modules/ is thousands of files with zero signal for this proof -- it is
    # gitignored, never part of what "insight/ is extractable" is actually claiming (the CLAIM is
    # that the *sources* travel with the copy; `npm ci` is what repopulates node_modules/ in a real
    # extraction, exercised for real by insight/verify_web.py in CI's `web` job, never by this
    # file) -- and copying it wholesale here only slows every run down for no new coverage.
    # .next (issue #302 [E17.S1], .sdlc/plans/302.md Decision h): the same reasoning, extended to
    # the one other large generated tree this story introduces -- a locally-warm
    # insight/web/.next/ (gitignored, so never present in a fresh CI checkout, but routinely
    # present on a dev machine that just ran `npm run build`) is 86MB / 1,360 files (48MB of it
    # .next/standalone's own pruned node_modules copy, per this story's `output: "standalone"`
    # config) that `shutil.copytree` would otherwise copy wholesale on every local run of this
    # ~44-90s proof, for zero signal.
    shutil.copytree(INSIGHT, dest, ignore=shutil.ignore_patterns("node_modules", ".next"))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    env[_CHILD_SENTINEL] = "1"
    args = [sys.executable, "-m", "pytest", str(dest / "tests"), "-v", "-rs"]
    child = subprocess.Popen(
        args, cwd=str(dest_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,   # own process group -- see docstring, "PROCESS-GROUP TIMEOUT KILL"
    )
    try:
        stdout, stderr = child.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        # Kill the ENTIRE group start_new_session=True created, not just `child` itself --
        # a lone os.kill(child.pid, ...) here would repeat exactly the bug this fix closes.
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        child.wait()
        pytest.fail(
            "child pytest run exceeded its 600s timeout without finishing. The likely cause is "
            "runaway recursion -- the child spawning a grandchild pytest run of its own -- see "
            "this module's RECURSION GUARD docstring section and the `_CHILD_SENTINEL` check "
            "near the top of this file. The child's whole process group has already been killed "
            "(os.killpg + SIGKILL), so this failure should not leave orphaned pytest processes "
            "behind."
        )
    proc = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
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


def test_child_web_gate_travelled_with_the_extracted_copy(extracted):
    """Clause 5 and clause 4, RE-SCOPED (issue #301 [E16.S3], .sdlc/plans/301.md Decision g2) from
    an earlier version of this test that ran `verify_web.py` inside the extracted copy and
    asserted a SKIP -- true only while `insight/web/package.json` did not exist. This story lands
    it, so the copy's `verify_web.py` no longer skips; it falls straight into `npm ci`, which the
    `insight` CI job (no Node installed, by design -- see .sdlc/plans/301.md Decision a) cannot
    run, and this fixture's own docstring (module docstring, "NO pip install... verify explicitly
    must not require [network]") already forbids this proof from ever needing network or a second
    toolchain.

    What clause 5 actually needs, restated correctly: not that the web checks RUN a second time
    here (insight/verify_web.py already runs them for real, in the `web` job, against the real
    tree) -- only that the web gate's ENTRY POINT and its inputs travelled with the copy, proving
    clause 4 (the entry point lives under insight/ itself, so extraction does not strand it at a
    repo root that would not exist once insight/ is standalone). So this asserts PRESENCE and
    SHAPE only: verify_web.py, web/package.json, and web/package-lock.json all exist in the copy,
    and package.json's "scripts" declares every name verify_web.py's own CHECKS requires -- read
    off the copy's own verify_web.py module (never retyped), so this test and that module's
    contract cannot drift apart -- without ever invoking npm."""
    _, _, dest = extracted
    entry = dest / "verify_web.py"
    assert entry.is_file(), f"{entry} did not travel with the extracted copy"

    spec = importlib.util.spec_from_file_location("verify_web_extracted", entry)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    package_json = dest / "web" / "package.json"
    assert package_json.is_file(), f"{package_json} did not travel with the extracted copy"
    lockfile = dest / "web" / "package-lock.json"
    assert lockfile.is_file(), f"{lockfile} did not travel with the extracted copy"

    scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
    missing = [c for c in m.CHECKS if c not in scripts]
    assert not missing, f'{package_json} "scripts" is missing {missing}'


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
