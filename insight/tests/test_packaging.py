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


def test_console_script_points_at_insight_dunder_main_colon_main():
    text = _pyproject_text()
    assert re.search(r'insight\s*=\s*"insight\.__main__:main"', text)


def test_toml_actually_parses_with_the_expected_shape():
    tomllib = pytest.importorskip("tomllib")  # stdlib on 3.11+; skip cleanly on 3.9/3.10
    data = tomllib.loads(_pyproject_text())
    project = data["project"]
    assert project["name"] == "insight"
    assert "version" in project["dynamic"]
    assert "version" not in project
    assert any("duckdb" in dep for dep in project["dependencies"])
    assert project["scripts"]["insight"] == "insight.__main__:main"
    assert data["tool"]["setuptools"]["dynamic"]["version"]["file"] == "VERSION"
