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
  * `__pycache__` and `*.egg-info` — BY NAME, and the justification has to be exact because a
    wrong rule here is how the first two holes got written. It is NOT that they are illegal module
    names: `import __pycache__` genuinely works if you create one. It is that `__pycache__` is
    RESERVED by CPython (PEP 3147 writes bytecode there, so no sane package claims it) and a dot
    makes `*.egg-info` unimportable — and both are gitignored at the repo root, so their contents
    cannot be committed without `git add -f`. That pair of reasons is what makes the name match
    safe here and is exactly what `env`/`build`/`dist` lacked. Do not extend this list to a name
    that is merely conventional (`node_modules`, `.tox`): check both halves first.

The distinction matters because an earlier docstring claimed all three were structural, which was
false and would have told the next reader the wrong thing. What is true of all three: none depends
on git or on ignore rules, and the fixture tests exercise the exact function the tree scan uses.

KNOWN RESIDUE, stated so nobody mistakes this for airtight: `rglob` does not follow symlinked
directories, so `insight/core -> ../src` hides that tree; and planting BOTH a `pyvenv.cfg` and a
`bin/` in a real module still silences it. Both are two-artifact, diff-visible moves — the veto
this guard closed was a one-file one. Tracked with the extension work in issue #163.

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

ONE GUARD HERE IS NOT ABOUT THE LICENCE: `test_no_docker_or_compose_files_at_the_repository_root`
(issue #296) checks a *folder* boundary — spec §7 puts compose/Dockerfile artifacts under
`insight/` for the same reason everything else in this file must be — but it is not itself a
marker or licence-text check. It lives here anyway, a deliberate cross-cutting exception to this
file's own remit, because no better home exists (it is not an AST import check, so it does not
belong in test_import_boundary.py either) and fewest files wins over a third guard file for one
test.
"""
import json
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSIGHT = ROOT / "insight"
HEADER_FILE = INSIGHT / "HEADER.txt"
WEB = INSIGHT / "web"
API = INSIGHT / "api"

#: PEP 263 / CPython's tokenizer. Deliberately NOT `\bcoding` — that rejects
#: `# encoding: utf-8`, the commonest cookie form, on a correctly-marked file.
_CODING_COOKIE = re.compile(r"^[ \t\f]*#.*coding[:=][ \t]*[-\w.]+")


def _is_virtualenv(directory):
    """A virtualenv announces itself with pyvenv.cfg AND a launcher directory — `python -m venv`
    always creates a launcher directory beside it — `bin/` on POSIX, `Scripts/` on Windows. Both halves are
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


def _owned_ts_files(root):
    """Every .ts/.tsx file under `root`, same structural skip as `_owned_py_files` (no name skip,
    no git) -- see the module docstring. Two glob patterns because a module is either `.ts` or
    `.tsx`, never both."""
    out = []
    for pattern in ("*.[tT][sS]", "*.[tT][sS][xX]"):
        for path in root.rglob(pattern):
            rel = path.relative_to(root)
            if "__pycache__" in rel.parts or any(q.endswith(".egg-info") for q in rel.parts):
                continue
            if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1]))
                   for i in range(len(rel.parts) - 1)):
                continue
            out.append(path)
    return sorted(out)


def _ts_marker(py_marker):
    """Derive the `//`-led `.ts`/`.tsx` marker MECHANICALLY from the one stored `.py` marker in
    HEADER.txt -- swap only the leading `#` comment-leader for `//`, leave the payload untouched,
    so the two markers cannot say different things. An empty py_marker (an emptied HEADER.txt) maps
    to an empty ts_marker too, so `_carries_marker`'s equality check keeps failing loud (flagging
    every file) instead of going silently permissive -- same contract
    test_checker_flags_everything_when_the_marker_is_empty already pins for .py."""
    return "//" + py_marker[1:] if py_marker.startswith("#") else py_marker


def _files_missing_ts_header(root, marker):
    """The `.ts`/`.tsx` analogue of `_files_missing_header` -- same `_carries_marker` equality
    check (shebang/coding-cookie skip included, harmlessly inert for TS), different file set."""
    return sorted(
        str(p.relative_to(root))
        for p in _owned_ts_files(root)
        if not _carries_marker(p.read_text(encoding="utf-8-sig", errors="replace"), marker)
    )


def _refuse_if_stale(probe):
    """Shared by the planted-probe tests below (this file's and, independently re-typed rather than
    imported per this module's own no-shared-test-helper convention, test_import_boundary.py's): a
    stale leftover from a previous killed/failed run must refuse to run silently over it, with a
    message naming the exact path, rather than double-write and pass for the wrong reason. Factored
    out so the refusal itself can be pinned directly
    (test_stale_probe_guard_fires_with_a_legible_message) instead of trusted to fire correctly on
    faith, since a clean run must never actually observe it. A leftover probe abandoned by a
    SIGKILL rather than cleaned up trips both guard files at once -- it reads as an unmarked
    source to this file's own ambient real-tree scan and, if it also carries a banned import, as
    a boundary violation to test_import_boundary.py's ambient scan, so one root cause fans out
    into two unrelated-looking failures across two files; this guard already names the exact path
    to delete, which is exactly what a maintainer seeing both fail at once should go looking
    for."""
    assert not probe.exists(), (
        f"stale probe from a previous failed run -- delete it and rerun: {probe}"
    )


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


def test_ts_marker_is_derived_mechanically_from_the_py_marker():
    py_marker = _marker()
    ts_marker = _ts_marker(py_marker)
    assert ts_marker == "//" + py_marker[1:], "the // marker must be a swap, never retyped"
    assert ts_marker.startswith("// SPDX-License-Identifier: BUSL-1.1")


def test_ts_marker_is_empty_when_the_py_marker_is_empty():
    """Mirrors test_checker_flags_everything_when_the_marker_is_empty for .py: an emptied
    HEADER.txt must still make the .ts/.tsx checker flag everything, never pass everything."""
    assert _ts_marker("") == ""


def test_ts_checker_flags_a_headerless_ts_or_tsx_file(tmp_path):
    marker = _ts_marker(_marker())
    (tmp_path / "good.tsx").write_text(marker + "\nexport const x = 1\n", encoding="utf-8")
    (tmp_path / "good.ts").write_text(marker + "\nexport const y = 1\n", encoding="utf-8")
    (tmp_path / "bad.tsx").write_text("export const z = 1\n", encoding="utf-8")
    assert _files_missing_ts_header(tmp_path, marker) == ["bad.tsx"]


def test_ts_checker_flags_everything_when_the_marker_is_empty(tmp_path):
    (tmp_path / "good.tsx").write_text(
        _ts_marker(_marker()) + "\nexport const x = 1\n", encoding="utf-8")
    assert _files_missing_ts_header(tmp_path, "") == ["good.tsx"]


def test_deriving_the_ts_marker_does_not_change_the_py_marker_or_checker(tmp_path):
    """Clause 2's other half: adding the .ts/.tsx path must not perturb the existing .py marker or
    _files_missing_header. Reads HEADER.txt itself (never retypes it) before and after calling
    _ts_marker, then re-runs the existing .py fixture behaviour."""
    before = _marker()
    _ts_marker(before)
    assert _marker() == before, "_ts_marker must be a pure read, not a mutation"
    (tmp_path / "bad.py").write_text("x = 1\n", encoding="utf-8")
    assert _files_missing_header(tmp_path, before) == ["bad.py"]


def test_stale_probe_guard_fires_with_a_legible_message(tmp_path):
    """Pins the refusal in `_refuse_if_stale` directly: a leftover file from a previous killed or
    failed run must make the guard raise, and the message must name the offending path, rather
    than trusting -- on faith -- that a real planted-probe test would somehow surface it. None of
    the four planted-probe tests -- test_a_planted_plugin_import_in_the_real_insight_web_is_caught,
    test_a_planted_plugin_import_in_the_real_insight_api_is_caught (both test_import_boundary.py),
    test_a_planted_markerless_tsx_in_the_real_insight_web_is_caught, or
    test_a_planted_markerless_py_in_the_real_insight_api_is_caught (both below) -- ever exercises
    this path on a clean run: by construction, the probe each plants never pre-exists. So this is
    the only place any of the four behaviours is actually pinned."""
    probe = tmp_path / "stale.tsx"
    probe.write_text("leftover from a previous run\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(str(probe))):
        _refuse_if_stale(probe)


# --------------------------------------------------------------------------- the tree


def test_every_insight_source_carries_the_marker():
    if not INSIGHT.is_dir():
        pytest.skip("insight/ does not exist yet")
    missing = _files_missing_header(INSIGHT, _marker())
    assert not missing, (
        "these insight/ sources are missing the BUSL marker (see insight/HEADER.txt):\n  "
        + "\n  ".join(missing)
    )


def test_every_insight_ts_source_carries_the_marker():
    """The .ts/.tsx analogue of the .py test above, and DELIBERATELY UNCONDITIONAL -- no
    `if not WEB.is_dir(): pytest.skip(...)` guard, unlike that one. insight/web/ exists as of this
    story (issue #296), so the "the directory doesn't even exist yet" vacuity the .py guard was
    written to skip past at E0.S2 does not apply here; this runs against the real tree on every
    invocation, exactly the way the .py guard eventually did.

    This is the whole point of clause 2: without an unconditional real-tree check, a future story
    (E17.S1) could land fifty unmarked .tsx files and this suite would stay green forever, because
    the fixture tests above only prove the checker CAN catch a planted violation in a tmp_path --
    never that anyone actually ran it against the real insight/web/. See this file's own module
    docstring, "WHY THE CHECKER IS TESTED, NOT JUST THE TREE" -- the identical argument, restated
    here because it is what justifies running unconditionally rather than skipping until content
    exists.

    Vacuous TODAY, by design: insight/web/ carries no real .ts/.tsx yet (E17.S1), so
    _owned_ts_files(INSIGHT) is empty and this passes trivially -- the same zero-files-today,
    real-coverage-the-day-a-file-lands shape the .py guard had at E0.S2, and the direct reason it
    is safe to ship unconditional now rather than deferred behind a skip.
    """
    missing = _files_missing_ts_header(INSIGHT, _ts_marker(_marker()))
    assert not missing, (
        "these insight/ .ts/.tsx sources are missing the BUSL marker (see insight/HEADER.txt):\n  "
        + "\n  ".join(missing)
    )


def test_every_insight_ts_source_carries_the_marker_stays_unconditional():
    """Pins the "DELIBERATELY UNCONDITIONAL" property test_every_insight_ts_source_carries_the_marker
    (above) only asserts in PROSE (F2, PR #322 mutation-testing finding on #322): re-adding an
    `if not WEB.is_dir(): pytest.skip(...)` guard -- the exact shape
    test_every_insight_source_carries_the_marker carries for `.py` -- would make that test vacuous
    again while test_a_planted_markerless_tsx_in_the_real_insight_web_is_caught (below) stayed
    green regardless, because that test calls `_files_missing_ts_header` directly and never goes
    through this one. Nothing else in this file would notice the regression. Parses the test
    function's own source via `ast` (never a substring match against the docstring itself, which
    legitimately mentions "pytest.skip" in prose two lines up) and asserts no `pytest.skip` call
    appears in its body -- the same class of "this test's body still does what its docstring
    claims" pin test_loop.py's test_spend_cli_verb_accumulates_is_unmodified uses via
    inspect.getsource, rather than trusting the docstring on faith.

    KNOWN RESIDUE: this pins only the absence of an attribute call literally named `skip` (i.e.
    `pytest.skip(...)`) inside that one function's own body and decorators; it does not stop a
    differently-shaped route to the same vacuity (an early `return` before the assertion, or a
    change inside `_files_missing_ts_header`/`_owned_ts_files` themselves). Nor does it stop five
    further routes, each tried and verified to escape it: a bare-name `skip()` reached via `from
    pytest import skip` (the call site is an `ast.Name`, not the `ast.Attribute` this walk
    matches, so it never enters `called` at all); a `@pytest.mark.skipif(...)` decorator (walked
    along with the rest of the function, but its attribute name is `skipif`, not `skip`);
    `pytest.importorskip(...)` (attribute name `importorskip`, same miss); a wrapper helper that
    itself calls `pytest.skip` on this function's behalf (`inspect.getsource` only sees this
    function's own body, so a skip buried one call away is invisible to it); and a `try/except`
    around the assertion that swallows it without ever calling anything named `skip`. This pin is
    deliberately scoped to the ONE regression PR #322's mutation-testing finding named --
    re-adding `pytest.skip` to this function's body -- not a general vacuity detector, and must
    not be read as one: an over-claiming pin is worse than none, which is this story's own thesis
    ("A guard that silently skips a directory is worse than no guard," above) applied to itself.
    The identical class of exposure already exists, unremarked, in
    test_every_insight_source_carries_the_marker's own `if not INSIGHT.is_dir(): pytest.skip(...)`
    line: nothing pins that guard to exactly "the directory does not exist" either, so a future
    edit could widen its condition (e.g. to "or has no .py files") and nothing in this file would
    fail. That is pre-existing residue, not a regression introduced here.
    """
    import ast
    import inspect
    src = inspect.getsource(test_every_insight_ts_source_carries_the_marker)
    tree = ast.parse(src)
    called = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "skip" not in called, (
        "test_every_insight_ts_source_carries_the_marker must stay unconditional -- a pytest.skip "
        "call anywhere in its body would make clause 4's real-tree proof prose again, with no "
        "other test in this file left to notice"
    )


def test_a_planted_markerless_tsx_in_the_real_insight_web_is_caught():
    """Clause 4's proof, the licence-boundary half (issue #296): a marker-less .tsx planted inside
    the REAL insight/web/ directory must be caught by the real checker against the real tree, not a
    tmp_path look-alike. A guard that silently skips a directory is worse than no guard, because the
    board reads as covered; only planting inside the real path proves the walk actually reaches it.
    Removed in `finally` so a failed assertion never leaves litter."""
    probe = WEB / "_e15s2_marker_probe.tsx"
    _refuse_if_stale(probe)
    probe.write_text("export const x = 1\n", encoding="utf-8")
    try:
        missing = _files_missing_ts_header(INSIGHT, _ts_marker(_marker()))
        assert str(probe.relative_to(INSIGHT)) in missing, (
            f"planting a markerless .tsx inside insight/web/ was not caught: {missing}")
    finally:
        probe.unlink()


def test_a_planted_markerless_py_in_the_real_insight_api_is_caught():
    """Clause 4's proof, extended to insight/api/ (F1, PR #322 mutation-testing finding on #322):
    the web-only .tsx probe above proves nothing about insight/api/, and a .tsx probe there would
    be meaningless anyway -- insight/api/ is E16.S1's FastAPI service, real `.py` source, not a
    TypeScript tree. insight/api/ holds no `.py` files at all today, so this guard's coverage there
    is otherwise vacuous by omission, not by design the way test_every_insight_source_carries_the_
    marker's zero-files-at-E0.S2 vacuity was: nothing has ever planted a violation in the real
    insight/api/ to prove `_files_missing_header` actually reaches it. This plants a REAL
    marker-less `.py` file inside the REAL insight/api/ directory -- not a tmp_path look-alike,
    which cannot prove the real path isn't skipped -- and asserts the real `.py` marker checker
    catches it. Removed in `finally` so a failed assertion never leaves litter. Distinct filename
    from the import-boundary probe this file's sibling module plants in the same directory
    (`_e15s2_api_import_boundary_probe.py`) so the two guards' probes can never collide."""
    probe = API / "_e15s2_api_licence_marker_probe.py"
    _refuse_if_stale(probe)
    probe.write_text("x = 1\n", encoding="utf-8")
    try:
        missing = _files_missing_header(INSIGHT, _marker())
        assert str(probe.relative_to(INSIGHT)) in missing, (
            f"planting a markerless .py inside insight/api/ was not caught: {missing}")
    finally:
        probe.unlink()


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


def test_no_docker_or_compose_files_at_the_repository_root():
    """Layout regression guard, issue #296 clause 1: spec §7 puts docker-compose.yml,
    Dockerfile.web, and Dockerfile.api under insight/ specifically so `git mv insight/ ../` carries
    them -- anything at the repository root is, by definition, left behind. No such file exists
    anywhere in this repo yet (E17.S1/E22.S1 author them); this guard has zero real coverage today,
    which is exactly why it's cheap to ship ahead of the first file it will ever catch.

    SEVEN glob patterns, not five (F3, PR #322 review): the original five --
    `compose.yaml`/`compose.yml` (checked separately -- `docker-compose*` catches neither, and
    verified directly rather than assumed) are Compose V2's preferred discovery order, with
    `docker-compose.yml` (`docker-compose*`) kept only as a V1-compatibility fallback;
    `Containerfile` is the OCI-neutral name Podman and buildah accept as a `Dockerfile` synonym --
    catch the build artifacts themselves, but missed each one's IGNORE-file companion, which ships
    beside it and must move with it under the same `git mv insight/ ../` extraction:
    `.dockerignore` (Docker's build-context ignore file) and `.containerignore` (Podman/buildah's
    identical mechanism for a Containerfile build, the same OCI-neutral parity that earned
    `Containerfile*` its own pattern). Neither is caught by the five patterns above: a
    per-Dockerfile ignore file (`Dockerfile.web.dockerignore`) IS already caught, because it starts
    with the literal string `Dockerfile`, but the bare, dot-led `.dockerignore`/`.containerignore`
    do not start with `Dockerfile`/`Containerfile` at all -- verified directly (see
    test_dockerignore_and_containerignore_are_not_matched_by_the_dockerfile_globs below) rather than
    assumed from the name alone. Missing any one of the seven would let that specific filename sit
    at the root undetected while every other one was caught -- coverage that looks complete without
    being complete."""
    hits = (
        sorted(p.name for p in ROOT.glob("docker-compose*"))
        + sorted(p.name for p in ROOT.glob("Dockerfile*"))
        + sorted(p.name for p in ROOT.glob("compose.yaml"))
        + sorted(p.name for p in ROOT.glob("compose.yml"))
        + sorted(p.name for p in ROOT.glob("Containerfile*"))
        + sorted(p.name for p in ROOT.glob(".dockerignore"))
        + sorted(p.name for p in ROOT.glob(".containerignore"))
    )
    assert not hits, f"these must live under insight/, not the repository root (spec §7): {hits}"


def test_dockerignore_and_containerignore_are_not_matched_by_the_dockerfile_globs():
    """Pins the empirical claim test_no_docker_or_compose_files_at_the_repository_root's docstring
    makes (F3): `Dockerfile*`/`Containerfile*` do not match `.dockerignore`/`.containerignore` --
    confirmed here with `fnmatch`, the same matching `pathlib.Path.glob` uses internally, rather
    than trusted from the string shapes alone. If this ever turned out False, the two new patterns
    above would be redundant, not additive, and the docstring's claim would be wrong."""
    import fnmatch
    assert not fnmatch.fnmatch(".dockerignore", "Dockerfile*")
    assert not fnmatch.fnmatch(".containerignore", "Containerfile*")
