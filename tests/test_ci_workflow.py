"""Issue #299 [E16.S1], plan-review amendment 2: `httpx` is needed only by
`fastapi.testclient.TestClient` (insight/tests/test_api_health.py), never by the shipped
`insight/api/` app code -- see .sdlc/plans/299.md Decision 2 for why it therefore does NOT belong
in insight/pyproject.toml's [project.dependencies] (that would ship a test-only package in the
product wheel). Instead it rides on .github/workflows/ci.yml's `insight` job's existing ad hoc
`pip install ... pytest pytest-cov` line -- the same place that job already installs pytest/
pytest-cov without either being declared in insight/pyproject.toml.

Lives in the ROOT tests/, not insight/tests/: .github/workflows/ci.yml is outside insight/
entirely, and insight/tests/test_standalone_extraction.py's proof copies insight/ ALONE into a
scratch directory -- a test needing ci.yml could not run inside that copy at all. Root tests/
always runs against the full repo tree (see .sdlc/config.json's verify.command), so ci.yml is
always present here.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _insight_job_block():
    """Isolate the `insight:` job's own YAML block, from its `insight:` key up to the next
    top-level (2-space-indented) job key or end of file. Plain text/regex, not a YAML parser --
    this repo's own text-based style for structural pyproject.toml checks (test_packaging.py)
    applies equally well here, and avoids adding a PyYAML dependency for one test."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"\n  insight:\n(.*?)(?=\n  \w[\w-]*:\n|\Z)", text, re.S)
    assert m, "could not find the `insight:` job block in .github/workflows/ci.yml"
    return m.group(1)


def test_ci_insight_job_installs_httpx():
    block = _insight_job_block()
    install_lines = [
        line for line in block.splitlines()
        if "pip install" in line and "pytest" in line and "insight/" not in line
    ]
    assert install_lines, (
        "could not find the insight job's ad hoc `pip install ... pytest pytest-cov` line -- "
        "either the job's shape changed, or this test's own extraction regex needs updating"
    )
    assert any("httpx" in line for line in install_lines), (
        "the insight job's ad hoc pip install line does not install httpx -- "
        "insight/tests/test_api_health.py's fastapi.testclient.TestClient-based tests need it, "
        "and without this they will silently SKIP in CI forever rather than actually run "
        "(issue #299, plan-review amendment 2, .sdlc/plans/299.md Decision 2)"
    )


def test_ci_test_job_line_19_is_not_mistaken_for_the_insight_jobs_own_install():
    """Regression guard against fixing the wrong line: .github/workflows/ci.yml has TWO
    textually-similar `pip install --upgrade pip pytest pytest-cov` ad hoc lines -- one on the
    unrelated `test:` job (skills/hooks, never runs insight/tests/), one on the `insight:` job
    (does run insight/tests/). httpx must land on the insight job's own line specifically, or
    insight/tests/test_api_health.py still skips in CI even after an edit that "looks right"."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    test_job = re.search(r"\n  test:\n(.*?)(?=\n  \w[\w-]*:\n|\Z)", text, re.S)
    assert test_job, "could not find the `test:` job block in .github/workflows/ci.yml"
    assert "pytest insight/tests" not in test_job.group(1), (
        "the test: job appears to run insight/tests/ -- if that ever becomes true, httpx must "
        "also be installed there, and this test's own premise (only the insight: job needs it) "
        "no longer holds"
    )
