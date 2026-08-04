"""Drives scripts/verify_web.py (evals/run.py's own module-load pattern, see tests/test_evals.py)
against a stub `npm` on PATH -- never the real network -- proving: absent app -> SKIP + exit 0;
present app + a failing check -> nonzero exit naming the check; every one of the four checks is
actually invoked; and the can't-run case (install fails) fails rather than passing.

One further test, against the REAL tree rather than a stubbed one: issue #296's package.json
invariant -- IF insight/web/package.json ever exists THEN it must carry a lockfile and declare
every one of CHECKS as an npm script. See that test's own docstring for why."""
import importlib.util
import json
import os
import pathlib
import stat

ROOT = pathlib.Path(__file__).resolve().parent.parent
R = ROOT / "scripts" / "verify_web.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_web", R)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _stub_npm(bin_dir, fail_on="", log=None):
    """A tiny fake `npm` on PATH: exits 1 iff its argv (space-joined) contains `fail_on` (when set),
    else 0. Every invocation is appended to `log` (one line per call) so a test can assert exactly
    which checks ran, in what order -- without ever shelling out to the real npm."""
    script = bin_dir / "npm"
    lines = ["#!/bin/sh", f'echo "$*" >> "{log}"' if log else ":"]
    if fail_on:
        lines.append(f'case "$*" in *"{fail_on}"*) exit 1 ;; esac')
    lines.append("exit 0")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _web(tmp_path, with_package_json=True, with_node_modules=True):
    web = tmp_path / "insight_web"
    web.mkdir()
    if with_package_json:
        (web / "package.json").write_text("{}")
    if with_node_modules:
        (web / "node_modules").mkdir()
    return web


def _world(m, tmp_path, **kw):
    """Point the module at a hermetic tmp world: ROOT is the SAME tmp_path PACKAGE_JSON's printed
    path is made relative to, so overriding WEB without also moving ROOT can never raise on a real
    (non-tmp) repo path leaking into the message."""
    m.ROOT = tmp_path
    m.WEB = _web(tmp_path, **kw)
    m.PACKAGE_JSON = m.WEB / "package.json"


def test_absent_app_skips_and_exits_zero(tmp_path, capsys):
    m = _module()
    _world(m, tmp_path, with_package_json=False)
    assert m.main() == 0
    assert "SKIP" in capsys.readouterr().out


def test_present_app_failing_check_names_it_and_fails(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_npm(bin_dir, fail_on="run lint")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    m = _module()
    _world(m, tmp_path)
    assert m.main() != 0
    out = capsys.readouterr().out
    assert "FAIL: npm run lint" in out
    assert "OK: npm run typecheck" in out


def test_all_four_checks_invoked_when_green(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _stub_npm(bin_dir, log=log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    m = _module()
    _world(m, tmp_path)
    assert m.main() == 0
    calls = log.read_text()
    for check in m.CHECKS:
        assert f"run {check}" in calls


def test_cannot_install_fails_rather_than_passes(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _stub_npm(bin_dir, fail_on="ci", log=log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    m = _module()
    _world(m, tmp_path, with_node_modules=False)  # forces the npm ci path
    assert m.main() != 0
    assert "run typecheck" not in log.read_text()  # never reached a check after the install failed


def test_real_package_json_if_it_ever_lands_has_a_lockfile_and_every_check():
    """A machine-checked invariant (issue #296 A2), not just insight/web/README.md's prose: IF the
    REAL insight/web/package.json exists THEN (a) it has a sibling package-lock.json, and (b) its
    "scripts" declares every name in this module's own CHECKS.

    Why this matters: this module's line ~49 treats package.json's mere EXISTENCE as "the app
    exists" and unconditionally proceeds. From there, `npm ci` hard-fails with EUSAGE the instant
    there is no committed lockfile, and each name in CHECKS that isn't a declared npm script fails
    on npm's own "Missing script" error -- both inside every future goal's fresh worktree
    (work.py:18-20 installs nothing ahead of time), so a goal wholly unrelated to insight/web/ would
    park on a failure it did not cause. Checking this directly turns that into a fast, legible
    failure attributable to the actual cause, rather than an unrelated goal quietly discovering it.

    CHECKS is read off `m` (loaded the same importlib.util.spec_from_file_location way
    tests/test_evals.py loads evals/run.py, and this file already loads scripts/verify_web.py via
    `_module()` above), never retyped, so this test and scripts/verify_web.py's own contract cannot
    drift apart.

    Vacuous TODAY: insight/web/package.json does not exist (E17.S1 lands it), so the `if` below
    never runs its body and this test passes trivially -- mirroring the IF/THEN shape
    test_marketplace_source_still_implies_the_readme_warning already uses in
    tests/test_licence_boundary.py for the same reason (a real invariant, checked directly, that
    happens to have no real-tree hits yet, rather than a one-shot tripwire someone would have to
    remember to delete)."""
    m = _module()
    package_json = m.ROOT / "insight" / "web" / "package.json"
    if package_json.is_file():
        lockfile = package_json.parent / "package-lock.json"
        assert lockfile.is_file(), (
            f"{package_json} exists without a sibling {lockfile.name} -- `npm ci` hard-fails with "
            "EUSAGE without a committed lockfile, inside every future goal's fresh worktree"
        )
        scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        missing = [c for c in m.CHECKS if c not in scripts]
        assert not missing, (
            f'{package_json} "scripts" is missing {missing} -- scripts/verify_web.py\'s CHECKS '
            "requires every one of these names to exist so each fails on npm's own error, not a "
            "silent skip"
        )
