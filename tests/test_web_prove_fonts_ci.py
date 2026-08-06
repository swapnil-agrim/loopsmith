"""issue #303 [E17.S2] review fix 1(5): the ci.yml HALF of the mechanical guard that `npm run
prove:fonts` stays wired up (see insight/tests/test_prove_fonts_wiring.py for the package.json
half, and both insight/verify_web.py's module docstring and insight/web/README.md for the full
reasoning): the browser-dependent font-applied proof (insight/web/scripts/
prove-fonts-actually-apply.mjs) was deliberately pulled out of `npm run test` / verify_web.py's
CHECKS -- run inside .sdlc/config.json's repo-wide verify.command, in a fresh worktree, for every
goal, on any machine, including ones with no browser at all -- and moved to its own `npm run
prove:fonts` step in .github/workflows/ci.yml's `web` job, the one place guaranteed to have Google
Chrome (ubuntu-latest). Without a test asserting CI still calls it, that move could silently
regress back into "the proof doesn't run anywhere" -- the exact defect class this issue exists to
fix.

Lives in the ROOT tests/, not insight/tests/: .github/workflows/ci.yml is outside insight/
entirely, and insight/tests/test_standalone_extraction.py's proof copies insight/ ALONE into a
scratch directory -- a test needing ci.yml could not run inside that copy. Root tests/ always runs
against the full repo tree (see .sdlc/config.json's verify.command), so ci.yml is always present
here -- mirrors tests/test_ci_workflow.py's own reasoning and job-block-extraction style (plain
text/regex, not a YAML parser, to avoid adding a PyYAML dependency for one test).

Does not pass by skipping when ci.yml is missing -- reading a nonexistent file raises, which fails
the test rather than silently passing it."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def _web_job_block():
    """Isolate the `web:` job's own YAML block, from its `web:` key up to the next top-level
    (2-space-indented) job key or end of file -- same regex-block-extraction style as
    tests/test_ci_workflow.py's `_insight_job_block`."""
    text = CI_YML.read_text(encoding="utf-8")
    m = re.search(r"\n  web:\n(.*?)(?=\n  \w[\w-]*:\n|\Z)", text, re.S)
    assert m, "could not find the `web:` job block in .github/workflows/ci.yml"
    return m.group(1)


def test_ci_web_job_runs_npm_run_prove_fonts():
    block = _web_job_block()
    assert "npm run prove:fonts" in block, (
        "the `web` job in .github/workflows/ci.yml no longer runs `npm run prove:fonts` -- the "
        "browser-dependent font-applied proof (insight/web/scripts/prove-fonts-actually-apply.mjs) "
        "is deliberately NOT part of `npm run test` / verify_web.py's CHECKS (see verify_web.py's "
        "module docstring), so if this step goes missing the proof runs NOWHERE, ever, again -- "
        "add a step running `npm run prove:fonts` (working-directory: insight/web) to the `web` "
        "job, after the `python3 insight/verify_web.py` step"
    )


def test_ci_web_job_prove_fonts_step_runs_after_verify_web_py():
    """Order matters: `npm run prove:fonts` needs insight/web/node_modules/, which only exists
    after verify_web.py's own `npm ci` has run -- see verify_web.py's `main()`. A `prove:fonts`
    step placed BEFORE the verify_web.py step would fail on a fresh checkout with no
    node_modules/ installed yet."""
    block = _web_job_block()
    verify_idx = block.find("python3 insight/verify_web.py")
    prove_idx = block.find("npm run prove:fonts")
    assert verify_idx != -1, "could not find the `python3 insight/verify_web.py` step"
    assert prove_idx != -1, "could not find the `npm run prove:fonts` step"
    assert verify_idx < prove_idx, (
        "the `npm run prove:fonts` step in .github/workflows/ci.yml's `web` job runs BEFORE "
        "`python3 insight/verify_web.py` -- it needs insight/web/node_modules/, which only exists "
        "once verify_web.py's own `npm ci` has run; move it to after that step"
    )
