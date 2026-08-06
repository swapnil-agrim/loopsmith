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
the test rather than silently passing it.

ANCHORING, issue #303 [E17.S2] review fix 2 (BLOCKING 2): a plain `"npm run prove:fonts" in block`
substring check also matches inside the step's OWN `name:` parenthetical (`- name: prove fonts
actually apply (npm run prove:fonts)`) -- so a mutation that deletes the step's `run:` and
`working-directory:` lines, leaving only that bare name header (a step that invokes NOTHING),
still satisfied the old assertion. Verified by actually performing that mutation and re-running
this file: it stayed green. Both assertions below are now anchored to an actual `run:` line via
regex, never a bare substring search, so a gutted step (name kept, invocation removed) trips them."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

#: A `run:` line invoking `npm run prove:fonts` -- NOT a bare substring match, so this cannot be
#: satisfied by the step's own `name:` parenthetical (`(npm run prove:fonts)`) once the `run:`
#: line itself has been deleted.
_PROVE_FONTS_RUN_RE = re.compile(r"^[ \t]*run:[ \t]*npm run prove:fonts[ \t]*$", re.M)
#: Same anchoring for the verify_web.py step, for the ordering check below -- kept regex-anchored
#: for symmetry even though its plain-substring form was not ambiguous against any `name:` line
#: in the current file (belt-and-suspenders against a future `name:` that does happen to embed
#: the same words).
_VERIFY_WEB_RUN_RE = re.compile(r"^[ \t]*run:[ \t]*python3 insight/verify_web\.py[ \t]*$", re.M)


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
    assert _PROVE_FONTS_RUN_RE.search(block), (
        "the `web` job in .github/workflows/ci.yml no longer has a `run: npm run prove:fonts` "
        "line -- the browser-dependent font-applied proof (insight/web/scripts/"
        "prove-fonts-actually-apply.mjs) is deliberately NOT part of `npm run test` / "
        "verify_web.py's CHECKS (see verify_web.py's module docstring), so if this step's actual "
        "invocation goes missing (even if a `- name: ... (npm run prove:fonts)` header survives) "
        "the proof runs NOWHERE, ever, again -- add a step running `npm run prove:fonts` "
        "(working-directory: insight/web) to the `web` job, after the "
        "`python3 insight/verify_web.py` step"
    )


def test_ci_web_job_prove_fonts_step_runs_after_verify_web_py():
    """Order matters: `npm run prove:fonts` needs insight/web/node_modules/, which only exists
    after verify_web.py's own `npm ci` has run -- see verify_web.py's `main()`. A `prove:fonts`
    step placed BEFORE the verify_web.py step would fail on a fresh checkout with no
    node_modules/ installed yet."""
    block = _web_job_block()
    verify_match = _VERIFY_WEB_RUN_RE.search(block)
    prove_match = _PROVE_FONTS_RUN_RE.search(block)
    assert verify_match, "could not find a `run: python3 insight/verify_web.py` line"
    assert prove_match, "could not find a `run: npm run prove:fonts` line"
    assert verify_match.start() < prove_match.start(), (
        "the `npm run prove:fonts` step in .github/workflows/ci.yml's `web` job runs BEFORE "
        "`python3 insight/verify_web.py` -- it needs insight/web/node_modules/, which only exists "
        "once verify_web.py's own `npm ci` has run; move it to after that step"
    )
