# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The definition of "the web checks" — type-check, lint, unit tests, production build — run by
BOTH the CI `web` job and `.sdlc/config.json`'s `verify.command` (issue #295). insight/web/ does not
exist yet pre-E17.S1; until it does, this SKIPs loudly and exits 0 rather than silently passing
something it never ran.

CI and the local gate DELIBERATELY DIFFER by exactly one check as of issue #303 [E17.S2]'s review
fix, correcting this docstring's former "one definition, cannot drift" claim. `npm run test` (the
`test` entry in CHECKS below) no longer runs `prove-fonts-actually-apply.mjs` — that script needs a
real Chromium-family browser, and this module runs inside `.sdlc/config.json`'s repo-wide
`verify.command`, in a FRESH worktree, for EVERY goal in this repo, with `verify.enforce: true`. A
machine with neither system Chrome nor an installed Playwright Chromium (a plain Linux dev box, a
fresh contributor checkout, a minimal container, any non-GitHub CI) would park EVERY goal in the
repo, not just web ones, on a proof it structurally cannot run. The font proof still runs — as its
own `npm run prove:fonts` step in `.github/workflows/ci.yml`'s `web` job, right after this module's
own `python3 insight/verify_web.py` step — and `ubuntu-latest` ships Google Chrome, so it never
skips there. `web` is one of the five required branch-protection contexts (see ci.yml's own
comments), so the font proof still hard-gates every merge to main; it has just moved out of the
repo-wide local gate into the one place guaranteed to have a browser.

package.json (`insight/web/package.json`) is the single source of truth for "does the app exist":
absent -> SKIP; present -> every one of CHECKS below is actually invoked via `npm run <name>`, so a
present-but-partially-wired app (a script missing from package.json) fails on npm's own "Missing
script" error rather than reading as covered.

npm ci: unlike the Python side (stdlib needs no install), tsc/eslint/vitest/next simply do not exist
without node_modules/, and every goal after E17.S1 runs this in a FRESH worktree with none installed
(work.py:18-20) — so "refuse when node_modules is absent" would permanently park every future goal,
web-touching or not. `npm ci` runs on demand instead. This is a deliberate departure from the pip
precedent in this same config (verify.command's `_command`): unlike pip, there is no zero-install
path for a JS toolchain. An install failure (e.g. no network) is a hard FAIL, never a skip -- a gate
that cannot run must never report success.

ponytail: each of the four checks only proves "npm run <name> exited 0" — it cannot tell a real check
apart from a vacuous one that structurally cannot fail (`jest --passWithNoTests`, an ESLint config
globbing zero files, `tsc` given no input files). This story owns the four NAMES the contract runs;
it does not audit what each underlying tool does with them. That audit is E17.S1's job, at the point
the real package.json/tsconfig/eslint config are written and can be inspected directly.
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent   # insight/ itself — this file lives directly
                                                  # under insight/ (issue #297), so WEB is
                                                  # derived from HERE, never from a repo root
                                                  # that would not exist once insight/ is
                                                  # extracted into a repo of its own.
WEB = HERE / "web"
PACKAGE_JSON = WEB / "package.json"
#: The npm-script contract E17.S1's package.json must satisfy — CI and verify.command both drive
#: exactly these four names, nothing more, nothing product-specific.
CHECKS = ["typecheck", "lint", "test", "build"]


def _npm(args):
    """Run one npm command, reporting a MISSING npm as a normal failure rather than a traceback.

    Until this story landed insight/web/package.json, main() always returned at the SKIP branch and
    this line was unreachable, so a machine with no Node never got here. Now every goal's verify
    gate runs it, and `npm` simply not being installed is the most likely way that happens -- an
    unattended run deserves a sentence naming the cause, not a FileNotFoundError traceback. It
    still fails closed (returncode 1, never 0): a gate that cannot run must never report success.
    """
    try:
        return subprocess.run(["npm", *args], cwd=str(WEB), capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["npm", *args], 1, "",
            "npm was not found on PATH. The web checks need Node (see insight/web/README.md); "
            "install it, or this gate stays red -- it will not pass by skipping.",
        )


def _report(label, proc):
    out = (proc.stdout or "") + (proc.stderr or "")
    print(f"FAIL: {label} (exit {proc.returncode})\n{out}")


def main(argv=None):
    if not PACKAGE_JSON.is_file():
        print(f"SKIP: {PACKAGE_JSON.relative_to(HERE.parent)} not found — web checks not run "
              "(expected pre-E17.S1: the web app does not exist yet)")
        return 0

    if not (WEB / "node_modules").is_dir():
        install = _npm(["ci"])
        if install.returncode != 0:
            _report("npm ci", install)
            return install.returncode or 1

    for check in CHECKS:
        proc = _npm(["run", check])
        if proc.returncode != 0:
            _report(f"npm run {check}", proc)
            return proc.returncode or 1
        print(f"OK: npm run {check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
