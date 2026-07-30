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

WHY THE SKIP IS STRUCTURAL, NOT BY NAME AND NOT BY IGNORE STATUS. Two earlier versions failed here,
and the second failure is the instructive one:

  1. A name set (`env`, `build`, `dist`, `.venv`) skipped at any depth. Those are also plausible
     module names for this product, so planting unmarked sources in `insight/metrics/dist/` kept
     the suite green.
  2. Enumerating via `git ls-files --exclude-standard` instead. This only MOVED the name heuristic
     into `.gitignore`: the same change added `/insight/build/` and `/insight/dist/`, so unmarked
     sources there were still invisible. It also made guard coverage depend on a contributor's
     GLOBAL `core.excludesFile`, and let any subtree be un-guarded by dropping a `.gitignore`
     containing `*` into it.

So the skip is now three narrow exclusions, and it is worth being exact about which is which:

  * a virtualenv — STRUCTURAL: the directory holds a `pyvenv.cfg` AND a `bin/` or `Scripts/`.
    Both halves are required. `pyvenv.cfg` alone is a one-file veto anyone could drop into a real
    module to silence it; `venv` always creates the launcher directory too, so requiring both costs
    nothing and closes that.
  * `__pycache__` and `*.egg-info` — BY NAME. Neither is a legal Python module name (a dunder cache
    directory is never imported; a dot makes `*.egg-info` unimportable), so unlike `env`/`build`/
    `dist` there is no plausible module they could collide with.

The distinction matters because an earlier docstring claimed all three were structural, which was
false and would have told the next reader the wrong thing. What is true of all three: none depends
on git or on ignore rules, and the fixture tests exercise the exact function the tree scan uses.

WHY THE MARKER IS READ, NEVER RETYPED. `insight/HEADER.txt` is the single source of the marker
string. Restating it here would let the two drift while this test kept passing against the wrong
string. Note the comparison is EQUALITY, not `startswith`: under `startswith` an emptied HEADER.txt
would make every file trivially "carry" the marker, because `"anything".startswith("")` is True.
Equality fails loud instead — an empty or truncated marker flags every file rather than none. Do
not "simplify" this to `startswith`. `test_marker_is_well_formed` additionally catches a marker
that is merely *wrong* rather than empty.

WHY plugin.json STAYS "license": "MIT". `.claude-plugin/marketplace.json` has `"source": "./"`, so
a marketplace install carries `insight/` onto the adopter's disk, and those files are not MIT. The
manifest is still correct because it describes the PLUGIN — hooks, skills, commands — whose own
files are all MIT. It does not describe the repository. That distinction is invisible to an adopter
staring at a manifest, so the README must say it out loud, and
`test_marketplace_source_still_implies_the_readme_warning` couples the two: while the manifest pair
stays (`source: "./"`, `license: MIT`), the README section must keep naming `plugin.json` and
`install.sh`. (`install.sh` copies only hooks/, skills/, commands/, so the copy path never carries
BUSL files at all — which is exactly why the README must distinguish the two install paths.)
"""
import json
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSIGHT = ROOT / "insight"
HEADER_FILE = INSIGHT / "HEADER.txt"

#: PEP 263 / CPython's tokenizer. Deliberately NOT `\bcoding` — that rejects
#: `# encoding: utf-8`, the commonest cookie form, on a correctly-marked file.
_CODING_COOKIE = re.compile(r"^[ \t\f]*#.*coding[:=][ \t]*[-\w.]+")


def _is_virtualenv(directory):
    """A virtualenv announces itself with pyvenv.cfg AND a launcher directory — `python -m venv`
    produces exactly `bin/ include/ lib/ pyvenv.cfg` (`Scripts/` on Windows). Both halves are
    required: pyvenv.cfg alone would be a one-file veto that anyone could drop into a real module
    to hide its sources from this guard."""
    return ((directory / "pyvenv.cfg").is_file()
            and ((directory / "bin").is_dir() or (directory / "Scripts").is_dir()))


def _owned_py_files(root):
    """Every .py file under `root` that this repo owns (see the module docstring for why the skip
    is structural). No git, no ignore rules, no name list."""
    out = []
    # "*.[pP][yY]": pathlib's glob is case-sensitive on POSIX regardless of the
    # filesystem, so a plain "*.py" misses LEAK.PY on the Linux CI runner too.
    for path in sorted(root.rglob("*.[pP][yY]")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or any(q.endswith(".egg-info") for q in rel.parts):
            continue
        if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1])) for i in range(len(rel.parts) - 1)):
            continue
        out.append(path)
    return out


def _carries_marker(text, marker):
    """True when `marker` is on line 1, or on line 2 when line 1 is a shebang or encoding cookie.

    Not `startswith`: 19 of this repo's Python files open with `#!/usr/bin/env python3`, and a
    shebang is only honoured on line 1 — forcing SPDX above it would silently break every script.
    Line 2 is therefore allowed, but ONLY behind a shebang/coding line, so the marker cannot drift
    arbitrarily far down the file.
    """
    lines = [l.strip() for l in text.splitlines()]
    if not lines:
        return False
    # Skip a shebang, then an encoding cookie: PEP 263 permits the cookie on line 2 when line 1 is
    # a shebang, so the canonical Python preamble legitimately pushes the marker to line 3.
    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    if i < len(lines) and _CODING_COOKIE.match(lines[i]):
        i += 1
    return i < len(lines) and lines[i] == marker


def _files_missing_header(root, marker):
    """The guard, as a pure function so it can be tested against planted files rather than only
    against a tree that happens to be empty today. Returns paths relative to `root`.

    `utf-8-sig` strips a BOM: a Windows editor can write one, `str.strip()` does not remove it
    (U+FEFF is not whitespace), and without this a correctly-marked file is reported as unmarked
    with a message that contradicts what its author sees on screen.
    """
    return sorted(
        str(p.relative_to(root))
        for p in _owned_py_files(root)
        if not _carries_marker(p.read_text(encoding="utf-8-sig", errors="replace"), marker)
    )


def _marker():
    return HEADER_FILE.read_text(encoding="utf-8-sig").strip()


def _licence_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## License" in text, "README lost its ## License heading"
    return text.split("## License", 1)[1]


# --------------------------------------------------------------------------- the marker itself


def test_header_file_exists():
    assert HEADER_FILE.is_file(), (
        f"{HEADER_FILE.relative_to(ROOT)} is the single source of the licence marker; "
        "every other reference reads it rather than restating it"
    )


def test_marker_is_well_formed():
    marker = _marker()
    assert marker, "HEADER.txt is empty — the marker must be a real string"
    assert len(marker.splitlines()) == 1, "the marker must be exactly one line"
    assert "BUSL-1.1" in marker, "the marker must name the licence"
    assert "insight/LICENSE" in marker, "the marker must point at the licence file"
    assert marker.isascii(), (
        "ASCII only: the marker is compared character-for-character against file content, and a "
        "non-ASCII dash mangles under a non-UTF-8 default console encoding (Windows cp1252)"
    )


# --------------------------------------------------------------------------- the checker


def test_checker_flags_a_headerless_file(tmp_path):
    """Real coverage at zero .py in insight/ — this is what keeps the guard non-vacuous."""
    marker = _marker()
    (tmp_path / "good.py").write_text(marker + "\nx = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == ["bad.py"]


def test_checker_flags_everything_when_the_marker_is_empty(tmp_path):
    """Equality, not startswith. An empty marker must fail LOUD (flag every file), never silently
    pass every file — which is what `startswith("")` would do."""
    (tmp_path / "good.py").write_text(_marker() + "\nx = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, "") == ["good.py"]


def test_checker_allows_the_marker_below_a_shebang(tmp_path):
    marker = _marker()
    (tmp_path / "cli.py").write_text(f"#!/usr/bin/env python3\n{marker}\nx = 1\n", encoding="utf-8")
    # PEP 263: the cookie is honoured on line 2 when line 1 is a shebang, so the canonical preamble
    # legitimately pushes the marker to line 3.
    (tmp_path / "both.py").write_text(
        f"#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n{marker}\nx = 1\n", encoding="utf-8")
    (tmp_path / "cookie.py").write_text(f"# -*- coding: utf-8 -*-\n{marker}\nx = 1\n", encoding="utf-8")
    # `# encoding: utf-8` is a legal cookie and must be accepted; CPython also treats
    # `# decoding: rot13` as one (it raises "encoding problem: rot13"), so we match its rule
    # rather than invent a stricter one.
    (tmp_path / "enc.py").write_text(f"# encoding: utf-8\n{marker}\nx = 1\n", encoding="utf-8")
    (tmp_path / "late.py").write_text(f"x = 1\n{marker}\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == ["late.py"], (
        "the marker may sit below a shebang and/or a PEP 263 encoding cookie (either form), "
        "but not buried further down the file"
    )


def test_checker_accepts_a_file_with_a_bom(tmp_path):
    """A BOM must not make a correctly-marked file read as unmarked."""
    marker = _marker()
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbf" + (marker + "\nx = 1\n").encode("utf-8"))
    assert _files_missing_header(tmp_path, marker) == []


def test_modules_named_like_build_trees_are_still_checked(tmp_path):
    """The hole two prior versions shipped. `env`, `build`, `dist` are plausible MODULE names as
    well as build-tree names, so neither a name set nor a .gitignore-based skip can tell them
    apart. Every one of these must be caught."""
    for rel in ("env/settings.py", "build/gen.py", "dist/hist.py",
                "ingest/env/detect.py", "metrics/dist/percentile.py", "dash/build/render.py"):
        q = tmp_path / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text("x = 1\n", encoding="utf-8")
    missing = _files_missing_header(tmp_path, _marker())
    assert len(missing) == 6, f"a module must never be skipped for its NAME; got {missing}"


def test_a_lone_pyvenv_cfg_cannot_hide_a_module(tmp_path):
    """A one-file veto: dropping pyvenv.cfg into a real module must NOT silence its sources."""
    mod = tmp_path / "realmod"
    mod.mkdir()
    (mod / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (mod / "leak.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, _marker()) == [os.path.join("realmod", "leak.py")]


def test_case_variant_extensions_are_checked(tmp_path):
    (tmp_path / "LEAK.PY").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, _marker()) == ["LEAK.PY"]


def test_a_real_virtualenv_is_skipped(tmp_path):
    """The legitimate exclusion, identified by what the directory CONTAINS. The venv here is called
    `env` — the same name the test above insists must be checked when it is a module."""
    venv = tmp_path / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv / "lib" / "vendored.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "mine.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, _marker()) == ["mine.py"]


def test_checker_finds_nested_files(tmp_path):
    """glob('*.py') is top-level only and would miss every file E0.S2 onward creates."""
    marker = _marker()
    nested = tmp_path / "ingest" / "readers"
    nested.mkdir(parents=True)
    (nested / "ledger.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, marker) == [
        os.path.join("ingest", "readers", "ledger.py")]


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
                   "Additional Use Grant:", "Covenants of Licensor"):
        assert needle in text, f"insight/LICENSE is missing {needle!r}"


def test_insight_licence_says_how_to_buy_one():
    """The Terms require a non-compliant user to purchase a commercial licence. A licence that
    demands that without giving any way to make contact is unusable."""
    params = (INSIGHT / "LICENSE").read_text(encoding="utf-8").split("Terms", 1)[0]
    licensor = params.split("Licensor:", 1)[1].split("\n\n", 1)[0]
    assert "@" in licensor, (
        "the Licensor parameter must carry a contact for alternative licensing arrangements — the "
        "Terms require a non-compliant user to purchase a commercial licence, which is unusable "
        "without one. It rides on Licensor rather than a new label so the Parameters block keeps "
        "exactly the five slots BUSL defines (see insight/LICENSE-NOTES.md)."
    )


def test_readme_states_the_boundary():
    """done_when clause 2. The README is what an adopter actually reads."""
    section = _licence_section()
    for needle in ("insight/", "BUSL", "MIT"):
        assert needle in section, (
            f"README's License section no longer names {needle!r}; it must state the two-licence "
            "folder boundary"
        )


def test_marketplace_source_still_implies_the_readme_warning():
    """Couples the manifests to the README claim. An earlier version of this file asserted only
    that three substrings appeared somewhere in the section — a review gutted the entire
    marketplace paragraph and replaced the section with one dismissive line, and it stayed green.
    While the manifest pair below holds, the README must keep explaining BOTH install paths."""
    source = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(
        encoding="utf-8"))["plugins"][0]["source"]
    licence = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["license"]
    if source == "./" and licence == "MIT":
        section = _licence_section()
        for needle in ("plugin.json", "install.sh"):
            assert needle in section, (
                f"marketplace source is {source!r} and plugin.json declares {licence!r}, so a "
                f"marketplace install puts BUSL files on disk from an MIT-declared package. The "
                f"README's License section must explain that, and it no longer mentions {needle!r}."
            )
