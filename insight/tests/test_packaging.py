# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the E0.S2 packaging surface: VERSION and pyproject.toml (issue #95).

These read the files as plain text rather than requiring a TOML parser, so they run
unconditionally on the repo's Python 3.9 baseline (tomllib is 3.11+). Where a parser IS
available, test_toml_actually_parses_with_the_expected_shape does a stricter structural
check on top, via pytest.importorskip rather than a new dependency — insight/'s only
declared runtime dependency is duckdb, and a test-only TOML parser has no business in
[project.dependencies].
"""
import pathlib
import re

import pytest

INSIGHT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject_text():
    return (INSIGHT / "pyproject.toml").read_text(encoding="utf-8")


def test_version_file_is_a_bare_semver_starting_at_0_1_0():
    text = (INSIGHT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), f"VERSION must be a bare X.Y.Z, got {text!r}"
    assert text == "0.1.0", "E0.S2 starts the package at 0.1.0"


def test_pyproject_does_not_hardcode_a_version():
    """The version is dynamic, sourced from VERSION - not duplicated here, so the two
    cannot drift the way a hardcoded version= would."""
    text = _pyproject_text()
    assert re.search(r'dynamic\s*=\s*\[[^\]]*"version"[^\]]*\]', text), (
        "pyproject.toml must declare version as dynamic so VERSION stays the single "
        "source of truth"
    )
    assert not re.search(r'(?m)^\s*version\s*=\s*"', text), (
        "found a hardcoded version = \"...\" in [project]; VERSION should be the only "
        "source of the version string"
    )


def test_pyproject_reads_the_dynamic_version_from_the_version_file():
    text = _pyproject_text()
    match = re.search(
        r"\[tool\.setuptools\.dynamic\].*?version\s*=\s*\{[^}]*file\s*=\s*\"([^\"]+)\"",
        text,
        re.S,
    )
    assert match, "tool.setuptools.dynamic.version must read {file = \"...\"}"
    assert match.group(1) == "VERSION"


def test_pyproject_pins_duckdb_with_a_lower_and_an_upper_bound():
    text = _pyproject_text()
    m = re.search(r'"duckdb\s*([^"]*)"', text)
    assert m, "duckdb must be declared in [project.dependencies]"
    spec = m.group(1).strip()
    assert spec, "duckdb must carry a version specifier, not be unbounded"
    # ~=X.Y (compatible release) supplies both bounds in one token; an explicit
    # ">=...,<..." pair is equally acceptable, so check for either shape.
    has_lower = bool(re.search(r"(~=|>=|>)\s*\d", spec))
    has_upper = "~=" in spec or bool(re.search(r"(<=|<)\s*\d", spec))
    assert has_lower and has_upper, f"duckdb spec {spec!r} needs both a lower and an upper bound"


def test_distribution_name_is_not_the_one_taken_on_pypi():
    """Text-based on purpose, so it pins on EVERY leg.

    The tomllib-gated test below also asserts this, but `importorskip("tomllib")` skips it on 3.9
    and 3.10 — and CI's matrix is 3.10 and 3.12, so on half the matrix, and on this repo's own
    local verify gate, that assertion never runs. A regression to the bare name would then be
    caught only by the 3.12 leg. This one has no such gate.

    `insight` is taken on PyPI by an unrelated package (0.8/0.9/1.0), so the bare name is
    unpublishable and `pip install insight` fetches something else entirely. The IMPORT name is a
    different thing and is deliberately still `insight` — see package-dir in pyproject.toml.
    """
    text = (INSIGHT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'(?m)^name = "loopsmith-insight"$', text), (
        "the distribution name is not loopsmith-insight — if it regressed to the bare `insight`, "
        "that name is already taken on PyPI and the package cannot be published (see #165)"
    )


def test_console_script_points_at_insight_dunder_main_colon_main():
    text = _pyproject_text()
    assert re.search(r'insight\s*=\s*"insight\.__main__:main"', text)


def test_toml_actually_parses_with_the_expected_shape():
    tomllib = pytest.importorskip("tomllib")  # stdlib on 3.11+; skip cleanly on 3.9/3.10
    data = tomllib.loads(_pyproject_text())
    project = data["project"]
    # The DISTRIBUTION name must NOT be the bare "insight" — that is taken on PyPI (#165), so
    # publishing would be impossible and `pip install insight` would fetch an unrelated package.
    # The IMPORT name is a different thing and stays "insight"; test_cli.py covers that.
    assert project["name"] == "loopsmith-insight"
    assert "version" in project["dynamic"]
    assert "version" not in project
    assert any("duckdb" in dep for dep in project["dependencies"])
    assert project["scripts"]["insight"] == "insight.__main__:main"
    assert data["tool"]["setuptools"]["dynamic"]["version"]["file"] == "VERSION"


def test_pyproject_declares_package_data_for_metric_sql_files():
    """BR-4 (research dossier): packages=[...] alone ships only .py files under insight.metrics
    -- a metrics/*.sql asset is silently dropped from a real (non-editable) wheel without this.
    VERIFIED live, this session, in a scratch copy of this exact pyproject.toml shape: building
    a wheel before this fix omits a planted metrics/1.sql; after adding the package-data
    stanza below, the identical build includes it. See .sdlc/plans/108.md Design decision H."""
    text = _pyproject_text()
    # Allows intervening comment lines between the table header and the key (Step 5.2's own
    # snippet has several) -- VERIFIED live, this session: a naive "\s*\n\s*" between the two
    # (no comment-line allowance) fails to match the real snippet, exactly the false-negative
    # this looser pattern avoids.
    assert re.search(
        r'\[tool\.setuptools\.package-data\]'
        r'(?:\s*\n\s*#[^\n]*)*'
        r'\s*\n\s*"insight\.metrics"\s*=\s*\[[^\]]*"\*\.sql"',
        text,
    ), "pyproject.toml must declare package-data for insight.metrics's *.sql files"


def test_pyproject_declares_package_data_for_gap_rule_sql_files():
    """Issue #116, E3.S1, Design decision 6: gap rules ship as .sql files (mirroring the metric
    catalog), and insight.gaps is already in packages=[...] with no matching package-data entry
    -- the exact gap test_pyproject_declares_package_data_for_metric_sql_files above already
    exists to name for a sibling package. Fixed before issue #117 lands the first real rule
    .sql file, so it never has a window where it would silently vanish from a built wheel.
    Not anchored to immediately follow the [tool.setuptools.package-data] header the way the
    metrics test above is -- this entry is second in the file, not first."""
    text = _pyproject_text()
    assert re.search(r'"insight\.gaps"\s*=\s*\[[^\]]*"\*\.sql"', text), (
        "pyproject.toml must declare package-data for insight.gaps's *.sql files (issue #116) "
        "-- packages=[...] alone silently drops them from a real wheel, see #108 Design "
        "decision H for the exact prior instance of this gap"
    )


def test_pyproject_declares_package_data_for_dash_font_licence_files():
    """Issue #262, D1: the two embedded WOFF2 fonts (insight/dash/fonts.py) ship under OFL 1.1,
    which REQUIRES its licence text travel with the Font Software -- insight/dash/fonts/OFL-*.txt
    (plain text, not .py) is insight.dash's own first non-.py asset, mirroring the exact gap the
    two tests above already exist to catch for insight.metrics/insight.gaps's *.sql files."""
    text = _pyproject_text()
    assert re.search(r'"insight\.dash"\s*=\s*\[[^\]]*"fonts/\*\.txt"', text), (
        "pyproject.toml must declare package-data for insight.dash's fonts/*.txt licence files "
        "(issue #262) -- packages=[...] alone silently drops them from a real wheel, dropping "
        "the OFL notice the embedded fonts' own licence requires ship alongside them"
    )
