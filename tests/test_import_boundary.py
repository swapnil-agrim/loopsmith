"""Both directions of the plugin/product import boundary, enforced (spec §1.1 rule 1, issue #97).

skills/ and hooks/ are the plugin; insight/ is the product. Neither may `import` the other as a
Python package — THE ALLOWED COUPLING IS FILE FORMATS ONLY. insight/ingest is specified to read
ledger/*/*.jsonl, goal frontmatter, config.json and state/* straight off disk (see the design
spec's §1.1 and ledger.py's own "an older reader ignores a field it does not know" contract):
reading a path is fine, `import insight` / `import skills` / `import hooks` is not.

WHY AST, NOT TEXT. Grepping for the substring "import insight" would false-positive on this very
sentence, and on test_self_contained.py's legitimate comment mentioning "insight" in prose (the
brief for this file calls that out by name) — and would false-negative on `import insight as ins`
or `import os, insight, sys`. So this checker parses every file with `ast` and inspects only
`ast.Import`/`ast.ImportFrom` nodes: real import statements, never comments or docstrings. Verified
empirically (not assumed): a docstring plus a commented-out `# from insight import x` produces zero
Import/ImportFrom nodes — see test_ignores_comment_and_docstring_mentions.

WHAT COUNTS AS A MATCH. `import insight`, `import insight.ingest`, `import insight.ingest as ii`
(Import) and `from insight import x`, `from insight.ingest import y` (ImportFrom) are all caught by
comparing the dotted target's FIRST segment — `"insight.ingest".split(".", 1)[0] == "insight"` —
against the banned name. An `as`-alias only renames the LOCAL binding, never the thing imported, so
it needs no separate handling. A merely similarly-named module (`insightful`, `skills_extra`) is
NOT caught, because the comparison is exact-segment, not substring/prefix — see
test_does_not_flag_a_module_whose_name_merely_starts_with_the_banned_name.

ONLY ABSOLUTE IMPORTS COUNT (`ImportFrom.level == 0`). `from . import x` and even `from .insight
import x` (level=1, module=="insight", confirmed via ast.dump) resolve a name INSIDE the current
package, never the top-level package of the same name, so they can never cross this boundary no
matter what text follows the dots — see test_ignores_relative_import_named_like_the_banned_module.
This is also why this checker needs no name-based directory skip, and so cannot reopen
tests/test_licence_boundary.py's `env`/`build`/`dist` hole (where a directory-name skip couldn't
tell a build artifact from a real module of the same name): this checker never excludes a directory
by what it is called, and never matches a module by name unless that name is the actual absolute
target of a real import statement.

DYNAMIC IMPORTS — `importlib.import_module("insight")`, `__import__("insight")` — ARE DELIBERATELY
NOT COVERED. Both take an arbitrary expression as their argument in the general case (a variable, a
built-up string, a config value); a check that only catches the literal-string special case would
miss the general one while advertising coverage it doesn't have. The literal case is also the least
likely form of accidental coupling — nobody reaches for `importlib.import_module("insight")` by
accident when plain `import insight` already does the job and IS caught here. Staying scoped to
`ast.Import`/`ast.ImportFrom` also keeps this checker structurally clear of
skills/sdlc-loop/scripts/*.py's legitimate, unrelated `importlib.util.spec_from_file_location(name,
path)` calls, used to load SIBLING scripts by file path rather than by package name (discovery.py,
handoff.py, loop.py, sources.py, state.py, sync.py, watch.py, work.py all do this today) — that
call produces no Import/ImportFrom node at all, so it is never a special case this guard has to
carve out; see test_ignores_the_spec_from_file_location_sibling_loading_pattern, and, against the
real tree, test_skills_and_hooks_do_not_import_insight passing today.

STRUCTURAL SKIP, THE SAME APPROACH AS tests/test_licence_boundary.py. That file's module docstring
records two holes a near-identical guard shipped: a name-based skip (`env`/`build`/`dist`, at any
depth — also plausible module names) and a `.gitignore`-based skip (which only relocated the same
name heuristic). Its fix is structural: a directory is excluded only if it IS a virtualenv
(`pyvenv.cfg` AND a `bin/`/`Scripts/` launcher dir — both required, so one planted `pyvenv.cfg`
can't veto a real module), or is named `__pycache__`, or ends `.egg-info`. That logic is
reimplemented here (`_is_virtualenv`, `_owned_py_files`) rather than imported: every guard test file
in this repo defines its own scan helpers rather than sharing a module (test_self_contained.py and
test_licence_boundary.py both do), and this file follows that convention.
test_modules_named_like_build_trees_are_still_checked and
test_a_lone_pyvenv_cfg_cannot_hide_a_module below pin the same two holes shut for THIS checker, on
planted fixtures, so the coverage does not depend on skills/hooks/insight happening to contain a
build/dist/env directory today.

MESSAGES ARE SORTED, NEVER A RAW SET. A violation's hit names are joined via
`", ".join(sorted(hits))`, never an f-string over the set directly: CPython randomizes string
hashing per process (confirmed here — `set(["insight", "skills", "hooks", ...])` printed in three
different orders across three separate `python3 -c` invocations), so an unsorted join would make
failure messages, and any test asserting on them, flaky across runs.

NON-VACUOUS BY CONSTRUCTION. skills/ and hooks/ already carry real .py files today, so
test_skills_and_hooks_do_not_import_insight has live coverage; insight/ has few by comparison
(#95's package skeleton), so — matching the pattern test_licence_boundary.py settled on — the
checker is factored into a pure function (`_boundary_violations`) and proven against planted
tmp_path fixtures for BOTH directions, so its correctness never depends on what either tree
currently contains.

KNOWN RESIDUE, stated so nobody mistakes this for airtight (mirrors test_licence_boundary.py's own
note — checked directly for this file, not assumed true by similarity): `rglob` does not follow
symlinked directories on this repo's Python 3.9 baseline, so `insight/core -> ../src` would hide
that tree from this guard.
"""
import ast
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The plugin — both directories are scanned for a real `import insight`.
_PLUGIN_DIRS = ("skills", "hooks")

#: The two banned-root-name sets, one per direction.
_BANNED_INSIGHT = frozenset({"insight"})
_BANNED_PLUGIN = frozenset({"skills", "hooks"})


def _is_virtualenv(directory):
    """A directory IS a virtualenv only if it carries BOTH `pyvenv.cfg` and a launcher dir (`bin/`
    POSIX, `Scripts/` Windows) — never by name alone (a real module could be called `env`), and
    never by `pyvenv.cfg` alone (a one-file veto anyone could drop into a real module). Same rule
    as tests/test_licence_boundary.py's `_is_virtualenv` — see this file's module docstring for why
    it is reimplemented here rather than imported."""
    return ((directory / "pyvenv.cfg").is_file()
            and ((directory / "bin").is_dir() or (directory / "Scripts").is_dir()))


def _owned_py_files(root):
    """Every .py file under `root` this repo owns: structural skip only (virtualenv by content,
    `__pycache__`/`*.egg-info` by name — both gitignored and unimportable/reserved, never a
    plausible module name) — no git, no ignore rules, no by-name directory skip. Mirrors
    tests/test_licence_boundary.py's `_owned_py_files`.
    """
    out = []
    for path in sorted(root.rglob("*.[pP][yY]")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or any(part.endswith(".egg-info") for part in rel.parts):
            continue
        if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1])) for i in range(len(rel.parts) - 1)):
            continue
        out.append(path)
    return out


def _imported_root_modules(source, filename):
    """Parse `source` and return the set of top-level module names it imports via a real
    `import`/`from ... import` statement — see the module docstring for exactly what counts as a
    match and why dynamic `importlib.import_module`/`__import__` calls are out of scope.
    """
    roots = set()
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def _boundary_violations(root, banned):
    """The guard, as a pure function tested against planted fixtures (see module docstring) rather
    than only against whatever `root` currently contains. Returns one sorted
    "relpath: imports x, y" entry per offending file, relative to `root`.
    """
    violations = []
    for path in _owned_py_files(root):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        hits = _imported_root_modules(text, str(path)) & banned
        if hits:
            rel = path.relative_to(root)
            violations.append(f"{rel}: imports {', '.join(sorted(hits))}")
    return sorted(violations)


# --------------------------------------------------------------------------- the tree, both directions


def test_skills_and_hooks_do_not_import_insight():
    """done_when, direction 1 (spec §1.1 rule 1): the plugin never imports the product as a Python
    package. The ALLOWED coupling is reading insight/'s output files by path (there are none yet —
    insight/ingest is still a stub, issue #98); `import insight` / `from insight import x` /
    `from insight.<anything> import y` is not."""
    violations = []
    for name in _PLUGIN_DIRS:
        violations += [f"{name}/{v}" for v in _boundary_violations(ROOT / name, _BANNED_INSIGHT)]
    assert not violations, (
        "skills/ or hooks/ must never `import insight` — the contract with insight/ is file "
        "formats, never imports (spec §1.1 rule 1):\n  " + "\n  ".join(violations)
    )


def test_insight_does_not_import_skills_or_hooks():
    """done_when, direction 2 (spec §1.1 rule 1): the product never imports the plugin as a Python
    package. The ALLOWED coupling is insight/ reading ledger/*/*.jsonl, goal frontmatter,
    config.json, state/* straight off disk; `import skills` / `import hooks` (or a submodule of
    either) is not."""
    violations = [f"insight/{v}" for v in _boundary_violations(ROOT / "insight", _BANNED_PLUGIN)]
    assert not violations, (
        "insight/ must never `import skills` or `import hooks` — the contract with the plugin is "
        "file formats, never imports (spec §1.1 rule 1):\n  " + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- the checker, on fixtures


def test_flags_plain_import(tmp_path):
    (tmp_path / "leak.py").write_text("import insight\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text("import insight.ingest\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_aliased_import(tmp_path):
    """The alias only renames the LOCAL binding; the thing actually imported is still insight."""
    (tmp_path / "leak.py").write_text("import insight.ingest as ii\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_from_import(tmp_path):
    (tmp_path / "leak.py").write_text("from insight import ingest\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_from_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text("from insight.ingest import Foo\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_multiple_names_on_one_import_line(tmp_path):
    (tmp_path / "leak.py").write_text("import os, insight, sys\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_ignores_comment_and_docstring_mentions(tmp_path):
    """Proves AST, not substring: test_self_contained.py itself legitimately mentions "insight" in
    a comment, and this repo's own docstrings discuss it in prose — neither may trip this guard."""
    (tmp_path / "clean.py").write_text(
        '"""This module discusses insight and imports insight, in prose only."""\n'
        "# from insight import something -- intentionally commented out\n"
        "x = 1\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []


def test_ignores_relative_import_named_like_the_banned_module(tmp_path):
    """`from .insight import x` is level=1 (confirmed via ast.dump), so it resolves inside the
    CURRENT package, never the top-level insight/ — it must not be flagged regardless of what its
    module text says."""
    (tmp_path / "leak.py").write_text("from .insight import x\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []


def test_does_not_flag_a_module_whose_name_merely_starts_with_the_banned_name(tmp_path):
    (tmp_path / "leak.py").write_text("import insightful\nimport insights\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []


def test_ignores_the_spec_from_file_location_sibling_loading_pattern(tmp_path):
    """skills/sdlc-loop/scripts/*.py load sibling scripts via
    importlib.util.spec_from_file_location(name, path) — a runtime call, not an import statement —
    specifically to share code without a package import. This plants the real pattern (see e.g.
    discovery.py) rather than only trusting a read of the real files; the real tree is checked
    too, by test_skills_and_hooks_do_not_import_insight passing today."""
    (tmp_path / "loader.py").write_text(
        "import importlib.util\n"
        "import pathlib\n"
        "_HERE = pathlib.Path(__file__).parent\n"
        "def _load(name):\n"
        "    spec = importlib.util.spec_from_file_location(name, _HERE / f'{name}.py')\n"
        "    m = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(m)\n"
        "    return m\n"
        "frontmatter = _load('frontmatter')\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []


def test_a_file_with_a_bom_still_parses(tmp_path):
    """A BOM (common from Windows editors) must not crash the AST parse — utf-8-sig strips it
    before ast.parse ever sees it; plain utf-8 would leave a stray U+FEFF that ast.parse rejects."""
    (tmp_path / "leak.py").write_bytes(b"\xef\xbb\xbf" + b"import insight\n")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["leak.py: imports insight"]


def test_flags_reverse_direction_plain_import(tmp_path):
    (tmp_path / "leak.py").write_text("import hooks\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports hooks"]


def test_flags_reverse_direction_from_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text("from skills.sdlc_loop import scripts\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports skills"]


def test_flags_both_banned_names_in_one_file(tmp_path):
    (tmp_path / "leak.py").write_text("import skills\nimport hooks\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports hooks, skills"]


# --------------------------------------------------------------------------- structural skip, on fixtures


def test_modules_named_like_build_trees_are_still_checked(tmp_path):
    """The hole tests/test_licence_boundary.py's docstring records two prior versions shipping:
    `env`, `build`, `dist` are plausible MODULE names too, so a name-based skip can't tell them
    apart from a build artifact. None of these is a real virtualenv (no pyvenv.cfg); all six must
    be caught."""
    for rel in ("env/leak.py", "build/leak.py", "dist/leak.py",
                "ingest/env/leak.py", "metrics/dist/leak.py", "dash/build/leak.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("import insight\n", encoding="utf-8")
    violations = _boundary_violations(tmp_path, _BANNED_INSIGHT)
    assert len(violations) == 6, f"a module must never be skipped for its NAME; got {violations}"


def test_a_real_virtualenv_is_skipped(tmp_path):
    """The legitimate exclusion, identified by what the directory CONTAINS — not by being named
    `env`, which the test above insists must be checked when it is a real module."""
    venv = tmp_path / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv / "lib" / "vendored.py").write_text("import insight\n", encoding="utf-8")
    (tmp_path / "mine.py").write_text("import insight\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == ["mine.py: imports insight"]


def test_a_lone_pyvenv_cfg_cannot_hide_a_module(tmp_path):
    """A one-file veto: dropping pyvenv.cfg into a real module must NOT silence its sources — only
    pyvenv.cfg PAIRED WITH a launcher dir counts as a virtualenv."""
    mod = tmp_path / "realmod"
    mod.mkdir()
    (mod / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (mod / "leak.py").write_text("import insight\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == [
        os.path.join("realmod", "leak.py") + ": imports insight"]


def test_pycache_is_skipped(tmp_path):
    p = tmp_path / "__pycache__" / "leak.py"
    p.parent.mkdir(parents=True)
    p.write_text("import insight\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []


def test_egg_info_is_skipped(tmp_path):
    p = tmp_path / "insight.egg-info" / "leak.py"
    p.parent.mkdir(parents=True)
    p.write_text("import insight\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_INSIGHT) == []
