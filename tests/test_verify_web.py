"""Drives scripts/verify_web.py (evals/run.py's own module-load pattern, see tests/test_evals.py)
against a stub `npm` on PATH -- never the real network -- proving: absent app -> SKIP + exit 0;
present app + a failing check -> nonzero exit naming the check; every one of the four checks is
actually invoked; and the can't-run case (install fails) fails rather than passing."""
import importlib.util
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
