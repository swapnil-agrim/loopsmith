"""The licence boundary is a folder boundary, and this is what holds it.

This repo ships TWO licences: everything is MIT except `insight/`, which is BUSL 1.1
(source-available, converts to MIT on its Change Date). A boundary that lives only in prose drifts
the first time someone adds a file, so it is asserted here.

WHY THE CHECKER IS TESTED, NOT JUST THE TREE. `insight/` contains no `.py` files today, so a bare
"every .py under insight/ carries the header" loop iterates an empty set and passes vacuously — and
would keep passing for months, then wave through the first real source file unmarked. Asserting
"insight/ must contain .py files" instead fails the day correct work lands (E0.S2 adds
`__init__.py`), and the cheapest fix under a red suite is deleting the assertion, restoring exactly
the vacuity it existed to prevent. So the guard is `_files_missing_header()`, a pure function tested
against planted fixtures: it has real coverage TODAY at zero `.py`, and the tree scan is then belt
and braces rather than the whole guard.

WHY THE MARKER IS READ, NEVER RETYPED. `insight/HEADER.txt` is the single source of the marker
string. Restating it here would let the two drift while this test kept passing against the wrong
string. That introduces its own hazard, guarded below: `"anything".startswith("")` is True, so an
emptied or truncated HEADER.txt would make EVERY file "carry the header" — green while `insight/`
fills with unmarked sources, strictly worse than no guard. Hence `test_marker_is_well_formed`.

WHY plugin.json STAYS "license": "MIT". `.claude-plugin/marketplace.json` has `"source": "./"`, so
a marketplace install clones the whole repo INCLUDING `insight/`, and those files are not MIT. The
manifest is still correct because it describes the PLUGIN — hooks, skills, commands — whose own
files are all MIT. It does not describe the repository. That distinction is invisible to an adopter
staring at a manifest, so the README says it out loud, and `test_readme_states_the_boundary`
asserts the README keeps saying it. (`install.sh` copies only hooks/, skills/, commands/, so the
copy path never carries BUSL files at all.)
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSIGHT = ROOT / "insight"
HEADER_FILE = INSIGHT / "HEADER.txt"

#: Directories a Python tree grows that nobody here owns. `insight/` becomes pip-installable in
#: E0.S2 (`pip install -e insight/`), which materialises .venv/build/*.egg-info full of third-party
#: sources — an unfiltered rglob would fail on thousands of files. Same idiom as
#: tests/test_self_contained.py's skip set, for the same reason.
SKIP_DIRS = {"__pycache__", ".venv", "venv", "env", "build", "dist", ".pytest_cache", ".mypy_cache"}


def _owned_py_files(root):
    """Every .py file under `root` that this repo actually owns."""
    out = []
    for path in root.rglob("*.py"):
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS or any(p.endswith(".egg-info") for p in parts):
            continue
        out.append(path)
    return out


def _carries_marker(text, marker):
    """True when `marker` is on line 1, or on line 2 when line 1 is a shebang or encoding cookie.

    Not `startswith`: 18 of this repo's Python files open with `#!/usr/bin/env python3`, and a
    shebang is only honoured on line 1 — forcing SPDX above it would silently break every script.
    Line 2 is therefore allowed, but ONLY behind a shebang/coding line, so the marker cannot drift
    arbitrarily far down the file.
    """
    lines = text.splitlines()
    if not lines:
        return False
    if lines[0].strip() == marker:
        return True
    first = lines[0].strip()
    if first.startswith("#!") or "coding:" in first or "coding=" in first:
        return len(lines) > 1 and lines[1].strip() == marker
    return False


def _files_missing_header(root, marker):
    """The guard, as a pure function so it can be tested against planted files rather than only
    against a tree that happens to be empty today. Returns paths relative to `root`."""
    return sorted(
        str(p.relative_to(root))
        for p in _owned_py_files(root)
        if not _carries_marker(p.read_text(encoding="utf-8", errors="replace"), marker)
    )


def _marker():
    return HEADER_FILE.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- the marker itself


def test_header_file_exists():
    assert HEADER_FILE.is_file(), (
        f"{HEADER_FILE.relative_to(ROOT)} is the single source of the licence marker; "
        "every other reference reads it rather than restating it"
    )


def test_marker_is_well_formed():
    """An empty or malformed marker would make every file pass. This is the failure mode that
    would be green while `insight/` filled with unmarked sources, so it is asserted directly."""
    marker = _marker()
    assert marker, "HEADER.txt is empty — every file would trivially 'carry' an empty marker"
    assert len(marker.splitlines()) == 1, "the marker must be exactly one line"
    assert "BUSL-1.1" in marker, "the marker must name the licence"
    assert "insight/LICENSE" in marker, "the marker must point at the licence file"
    assert marker.isascii(), (
        "ASCII only: the marker is compared byte-for-byte against file content, and a non-ASCII "
        "dash mangles under a non-UTF-8 default console encoding (Windows cp1252)"
    )


# --------------------------------------------------------------------------- the checker


def test_checker_flags_a_headerless_file(tmp_path):
    """Real coverage at zero .py in insight/ — this is what keeps the guard non-vacuous."""
    marker = _marker()
    (tmp_path / "good.py").write_text(marker + "\nx = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == ["bad.py"]


def test_checker_allows_the_marker_below_a_shebang(tmp_path):
    marker = _marker()
    (tmp_path / "cli.py").write_text(f"#!/usr/bin/env python3\n{marker}\nx = 1\n", encoding="utf-8")
    (tmp_path / "late.py").write_text(f"x = 1\n{marker}\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == ["late.py"], (
        "the marker is allowed on line 2 behind a shebang, but not buried further down"
    )


def test_checker_skips_unowned_trees(tmp_path):
    """A pip-installed insight/ grows .venv and *.egg-info; failing on those would be noise."""
    marker = _marker()
    for junk in (tmp_path / ".venv" / "lib", tmp_path / "insight.egg-info"):
        junk.mkdir(parents=True)
        (junk / "vendored.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == []


def test_checker_finds_nested_files(tmp_path):
    """glob('*.py') is top-level only and would miss every file E0.S2 onward creates."""
    marker = _marker()
    nested = tmp_path / "ingest" / "readers"
    nested.mkdir(parents=True)
    (nested / "ledger.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == ["ingest/readers/ledger.py".replace("/", __import__("os").sep)]


# --------------------------------------------------------------------------- the tree


def test_every_insight_source_carries_the_marker():
    if not INSIGHT.is_dir():
        pytest.skip("insight/ does not exist yet")
    missing = _files_missing_header(INSIGHT, _marker())
    assert not missing, (
        "these insight/ sources are missing the BUSL marker (see insight/HEADER.txt):\n  "
        + "\n  ".join(missing)
    )


# --------------------------------------------------------------------------- the two licences


def test_plugin_licence_is_still_mit():
    """The disaster this guards is someone relicensing the give-away."""
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text, "the root LICENSE must stay MIT — it covers the plugin"
    assert "Business Source" not in text, (
        "the root LICENSE has acquired BUSL text: the plugin must stay MIT, and BUSL belongs "
        "only in insight/LICENSE"
    )


def test_insight_licence_names_its_parameters():
    text = (INSIGHT / "LICENSE").read_text(encoding="utf-8")
    for needle in ("Business Source License 1.1", "Change Date:", "Change License:",
                   "Additional Use Grant:"):
        assert needle in text, f"insight/LICENSE is missing its {needle!r} parameter"


def test_readme_states_the_boundary():
    """done_when clause 2. The README is what an adopter actually reads, and it was the one part
    of this change with nothing asserting it — the failure family test_config_discoverability.py
    exists to prevent. Anchored on the heading, not on prose, per tests/test_docs.py's convention."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## License" in text, "README lost its ## License heading"
    section = text.split("## License", 1)[1]
    for needle in ("insight/", "BUSL", "MIT"):
        assert needle in section, (
            f"README's License section no longer names {needle!r}; it must state the two-licence "
            "folder boundary, because a marketplace install clones insight/ onto the adopter's disk"
        )
