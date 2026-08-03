"""#142 [E6.S7] Vocabulary-versus-call-site guard, spec §A.6, the last story of epic E6.

Two independent, required directions:

  DIRECTION A (EVENT_KINDS -> site): every kind in `ledger.EVENT_KINDS` has >=1 emitting call
  site, Python or prose. A kind nobody can ever write is dead vocabulary.

  DIRECTION B (site -> EVENT_KINDS): every kind named at a detected EVENTS-stream call site is a
  real `EVENT_KINDS` member. `ledger.append()` already raises `ValueError` for an unknown kind
  (ledger.py:569-570) -- but only for a code path that actually EXECUTES. A typo'd kind behind a
  rare branch (an error-handling arm, a hook that only fires on a specific trigger --
  hooks/decision_gate.py's deny path is exactly this shape) can sit un-exercised for months with
  green tests. This check is static: it reads source text, so `pytest` catches the typo the moment
  the suite runs, regardless of whether any test ever reaches that branch at runtime.

PLUS the flag clause: every `--k` flag `loop.py emit` accepts must appear in the vocabulary table.
Already structurally guaranteed -- `loop.py`'s `_validate_event` indexes `ledger.EVENT_FIELDS[kind]`
directly (loop.py:370), so the vocabulary table IS the allowlist, never a hand-maintained parallel
list. This file adds a PINNING test only (`test_emit_flag_check_reads_ledger_event_fields`) so a
future refactor that swaps in a separate list is caught -- no production change.

PLUS a staleness check: the guard must fail if its own detection idiom goes stale. See the comment
above `test_the_idiom_still_finds_the_prose_only_kinds` for exactly what it pins, and why the
weaker "matches something" bar (tests/test_config_discoverability.py:32-35) is not enough here.

AST, NOT REGEX, FOR THE PYTHON SIDE. tests/test_import_boundary.py's own docstring makes this
argument for a different guard ("Grepping for the substring 'import insight' would false-positive
on this very sentence..."); the identical failure mode is real here, verified directly against
this tree, not assumed: `skills/sdlc-loop/scripts/slices.py:119` carries a COMMENT containing the
literal text "ledger.append()", and an unbounded regex over that file's raw text walks forward
across ~8 lines to the next bare-quoted word, `"title"` (a dict key, not a kind) -- harmless only
by luck, since `gate`/`verify`/`scan`/`spend`/`slice`/`park`/`retro` are all plausible dict-key
spellings too, and a future comment mentioning `ledger.append()` followed by one of them would
misattribute a real kind to text that is not a call site. So this checker parses every file with
`ast` and only ever inspects real `ast.Call` nodes whose function resolves to
`ledger.append`/`ledger.safe_append` -- a comment or docstring produces no such node, regardless of
what text it contains (see test_ignores_a_comment_that_merely_mentions_ledger_append, which plants
the real shape as a fixture rather than only trusting today's real file).

SPAN-AWARE BY CONSTRUCTION. Direction B needs to know, for a given call, whether it carries
`stream=ledger.EVENTS` -- and the real `spend` call site (loop.py:467) wraps that kwarg onto a
continuation line, separated from the `"spend"` literal by a nested-paren kwarg
(`config=state.load_config(sdlc_dir)`). A same-line or same-string-span regex would silently drop
this real EVENTS site. Reading `stream=` off the SAME `ast.Call` node's `.keywords` is exactly as
span-aware as the call itself, for free -- there is no separate "span" concept to get wrong (see
test_span_aware_stream_kwarg_on_a_continuation_line).

SCOPE (spec §A.6 verbatim): every kind in `EVENT_KINDS` must have >=1 emitting call site under
`skills/*/scripts/`, `hooks/`, or a `SKILL.md`; every `--k` flag `loop.py emit` accepts must appear
in the vocabulary table.

CURRENT FACT, NOT ASSERTED AS STALENESS (see amendment C / the comment above
test_the_idiom_still_finds_the_prose_only_kinds for why): `phase` and `retro` have ZERO literal
Python call sites today. `loop.py`'s `emit` dispatcher (loop.py:482) passes `kind` as a VARIABLE
(`argv[4]`), so it is never a literal site for any kind -- both are covered exclusively by
SKILL.md prose. A Python-only scan would falsely flag both as uncovered; this is why Direction A's
coverage is a UNION of python_kinds and prose_kinds, never python_kinds alone.

OUT OF SCOPE: the `GATE_KINDS` sub-vocabulary (the `--gate` values `loop.py emit --gate ...`
accepts) is a SEPARATE vocabulary from `EVENT_KINDS`, not named by spec §A.6's text, and 6+ of its
12 values have no emitting site today. Issue #229 already names this gap from the EMISSION side
(not the consumption side -- #229's own step 1 is "build the event emitter" for the missing
`risk_*` gates, its step 2 adds ingestion afterward) and is P1 on the backlog. Folding it into this
guard would force a "known-unemitted, tracked elsewhere" allowlist into the guard itself -- which
would BE the drift #142 exists to prevent. Not building it, and pointing at #229 instead, is
deliberate, not an oversight.
"""
import ast
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")
loop = _mod("loop")

_LOOP_PY_TEXT = (S / "loop.py").read_text(encoding="utf-8")

#: `loop.py" emit .sdlc "<goal-expr>" <kind>` -- the exact idiom tests/test_emit_prose.py already
#: pins as data (that file's job is 5 SPECIFIC lines; this one discovers however many exist).
#: Space-delimited: kind is the token immediately after the goal's closing quote.
_PROSE_KIND = re.compile(r'loop\.py"\s+emit\s+\.sdlc\s+"[^"]*"\s+(\w+)')

#: kind's positional slot differs by call shape (ledger.py's own signatures):
#: append(sdlc_dir, config, kind, goal, ...) vs safe_append(sdlc_dir, kind, goal, ...).
_KIND_POSITION = {"append": 2, "safe_append": 1}


# --------------------------------------------------------------------------- the checker itself


def _py_sources(root):
    """Every .py file this guard scans -- spec §A.6's own wording ("skills/*/scripts/, hooks/")
    turned into globs, deliberately widened rather than a hardcoded file list, so a new script
    under an existing skills/*/scripts/ or a new hooks/*.py is picked up automatically."""
    return sorted(root.glob("skills/*/scripts/*.py")) + sorted(root.glob("hooks/*.py"))


def _skill_md_sources(root):
    """Every SKILL.md this guard scans for prose emit instructions -- glob, not the 5 hardcoded
    paths tests/test_emit_prose.py uses (that file pins 5 SPECIFIC lines; this discovers however
    many orchestrator/gate SKILL.md files exist, matching test_config_discoverability.py's glob
    philosophy)."""
    return sorted(root.glob("skills/*/SKILL.md"))


def _is_ledger_append_call(node):
    """True for a real `ledger.append(...)` / `ledger.safe_append(...)` ast.Call node -- never a
    comment or docstring that merely contains that text (module docstring, AST-not-regex)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in ("append", "safe_append")
        and isinstance(func.value, ast.Name)
        and func.value.id == "ledger"
    )


def _const_str(expr):
    """The literal string an ast expression names, or None for anything else -- a variable (e.g.
    `kind = argv[4]`, loop.py:482's `emit` dispatcher) is not a literal site for any one kind."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _call_kind(node):
    """The kind argument of a ledger.append/safe_append Call node, as a literal string, or None
    when it is not a literal (a variable-dispatch site)."""
    for kw in node.keywords:
        if kw.arg == "kind":
            return _const_str(kw.value)
    idx = _KIND_POSITION[node.func.attr]
    if len(node.args) > idx:
        return _const_str(node.args[idx])
    return None


def _call_is_events_stream(node):
    """True only when the SAME call node carries `stream=ledger.EVENTS` (or the string-literal
    equivalent `stream="events"`) -- read off the call's own keywords, so a kwarg wrapped onto a
    continuation line (the real `spend` call, loop.py:467) is exactly as reachable as one on the
    call's opening line. This is what makes Direction B span-aware, for free (module docstring)."""
    for kw in node.keywords:
        if kw.arg != "stream":
            continue
        v = kw.value
        if isinstance(v, ast.Attribute) and v.attr == "EVENTS":
            return True
        if isinstance(v, ast.Constant) and v.value == "events":
            return True
    return False


def _ledger_calls(text, filename="<string>"):
    """Every real `ledger.append`/`ledger.safe_append` Call node in `text`, as (kind, is_events)
    pairs. `kind` is None when the kind argument isn't a literal string -- such a site names no
    single kind and is excluded by both directions below, on purpose."""
    tree = ast.parse(text, filename=filename)
    return [
        (_call_kind(node), _call_is_events_stream(node))
        for node in ast.walk(tree)
        if _is_ledger_append_call(node)
    ]


def _python_event_kinds_at(root):
    """The set of literal kind strings named at an EVENTS-stream ledger call, across every file
    `_py_sources(root)` finds. The SAME function the real-tree tests AND the fixture tests below
    call -- real run and mutation-proof fixtures share one code path (test_import_boundary.py's
    own precedent), so this file's correctness never depends on what the real tree currently
    contains. An unparseable file contributes no kinds (this guard is not chartered to validate
    Python syntax; other tests already require every owned .py file to parse)."""
    kinds = set()
    for path in _py_sources(root):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            calls = _ledger_calls(text, filename=str(path))
        except SyntaxError:
            continue
        for kind, is_events in calls:
            if is_events and kind is not None:
                kinds.add(kind)
    return kinds


def _prose_kinds_at(root):
    """The set of kinds named by the `loop.py" emit .sdlc "<goal>" <kind>` idiom, across every
    `_skill_md_sources(root)` file."""
    kinds = set()
    for path in _skill_md_sources(root):
        kinds.update(_PROSE_KIND.findall(path.read_text(encoding="utf-8")))
    return kinds


def _uncovered_kinds(event_kinds, covered):
    """Direction A's pure comparison: every `event_kinds` member not in the `covered` union."""
    return sorted(k for k in event_kinds if k not in covered)


def _bogus_event_kinds(python_event_kinds, event_kinds):
    """Direction B's pure comparison: every kind found at an EVENTS-stream call site that is not a
    real `event_kinds` member -- a typo, or a kind that was renamed in EVENT_KINDS but not at its
    call site."""
    return sorted(k for k in python_event_kinds if k not in event_kinds)


# --------------------------------------------------------------------------- fixture helpers


def _plant_py(root, skill_name, filename, body):
    d = root / "skills" / skill_name / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body, encoding="utf-8")


def _plant_hook_py(root, filename, body):
    d = root / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body, encoding="utf-8")


def _plant_skill_md(root, skill_name, command_line):
    d = root / "skills" / skill_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"Instructions.\n\n`{command_line}`\n", encoding="utf-8")


# --------------------------------------------------------------------------- non-vacuous by construction


def test_py_and_skill_md_sources_are_non_empty():
    """Mirrors tests/test_import_boundary.py's own non-vacuous check: a renamed or emptied
    skills/*/scripts/, hooks/, or SKILL.md convention must fail loudly instead of passing with
    silently-zero coverage."""
    assert _py_sources(ROOT), "no .py files found under skills/*/scripts/ or hooks/"
    assert _skill_md_sources(ROOT), "no SKILL.md files found under skills/*/"


# --------------------------------------------------------------------------- direction A / B, real tree


def test_every_event_kind_has_an_emitting_site():
    """Direction A (spec §A.6): every EVENT_KINDS member has >=1 emitting site, Python or prose.
    done_when: 'a kind with no emitting call site fails' -- and the message names it."""
    covered = _python_event_kinds_at(ROOT) | _prose_kinds_at(ROOT)
    missing = _uncovered_kinds(ledger.EVENT_KINDS, covered)
    assert not missing, (
        "EVENT_KINDS member(s) with no emitting call site anywhere (skills/*/scripts/, hooks/, or "
        f"a SKILL.md): {missing}")


def test_every_events_stream_site_names_a_real_event_kind():
    """Direction B (spec §A.6): every kind named at a detected EVENTS-stream call site must be a
    real EVENT_KINDS member. See module docstring for what this catches that append()'s own
    runtime raise does not (a site no test ever executes)."""
    bogus = _bogus_event_kinds(_python_event_kinds_at(ROOT), ledger.EVENT_KINDS)
    assert not bogus, (
        f"EVENTS-stream call site(s) name kind(s) not in ledger.EVENT_KINDS: {bogus} -- a typo, or "
        "a kind renamed in EVENT_KINDS but not updated at its call site")


# --------------------------------------------------------------------------- the flag clause, pinned


def test_emit_flag_check_reads_ledger_event_fields():
    """The flag clause: every `--k` flag `loop.py emit` accepts must appear in the vocabulary
    table. Already structurally guaranteed -- `_validate_event` indexes
    `ledger.EVENT_FIELDS[kind]` directly (loop.py:370) -- so this PINS the wiring two ways: a
    source-text check (catches a refactor to a separate, driftable list before behavior even
    changes) and a behavioral check (the refusal itself still works). No production change."""
    assert "ledger.EVENT_FIELDS[kind]" in _LOOP_PY_TEXT, (
        "loop.py's _validate_event no longer indexes ledger.EVENT_FIELDS[kind] directly -- the "
        "flag-vocabulary clause may have grown a separate, hand-maintained allowlist")
    err = loop._validate_event("phase", {"bogus_flag_xyz": "1"})
    assert err is not None and "bogus_flag_xyz" in err, (
        f"an unknown --flag for a known kind should be refused by name; got {err!r}")


# --------------------------------------------------------------------------- staleness


#: What this pins: Direction A's own "matches something" pass/fail would stay green even if
#: `_PROSE_KIND` regressed to matching only a lucky subset, AS LONG AS phase/retro's coverage
#: happened to survive by accident -- research proved this exact asymmetric-decay shape is live
#: (prose detection going dark while Python detection, which never covered phase/retro anyway,
#: keeps matching something else). So this test pins the two facts whose SILENT loss neither
#: Direction A nor a bare non-empty assertion (tests/test_config_discoverability.py:32-35's bar)
#: would surface on its own: phase and retro are still found by prose detection specifically.
#:
#: Deliberately NOT bundled here: an assertion that Python detection does NOT also cover phase/
#: retro. That would couple a robustness claim (already proven independently, on a fixture, by
#: test_staleness_fails_when_prose_is_reworded below) with a CONTINGENT current fact -- the day
#: legitimate work adds a literal Python `phase` emitter, such an assertion goes red for a reason
#: unrelated to staleness, and the natural "fix" is to update the assertion away, which is exactly
#: how a staleness check dies by attrition. The current fact is recorded in the module docstring's
#: comment instead, not asserted.
def test_the_idiom_still_finds_the_prose_only_kinds():
    prose = _prose_kinds_at(ROOT)
    assert "phase" in prose, "SKILL.md prose detection stopped finding `phase`"
    assert "retro" in prose, "SKILL.md prose detection stopped finding `retro`"


# --------------------------------------------------------------------------- mutation proof, on fixtures


def test_direction_a_fails_when_the_only_prose_site_is_removed(tmp_path):
    """Mutation 1 (done_when: 'a kind with no emitting call site fails'), the retro/phase
    prose-only shape specifically: retro has ZERO Python call sites in the real tree, so removing
    its one SKILL.md prose line removes its only detection path."""
    _plant_skill_md(tmp_path, "sdlc-retro",
                     'python3 "${X}/loop.py" emit .sdlc "$goal" retro --grade achieved')
    covered_before = _python_event_kinds_at(tmp_path) | _prose_kinds_at(tmp_path)
    assert "retro" in covered_before

    _plant_skill_md(tmp_path, "sdlc-retro", "no emit instruction here at all")
    covered_after = _python_event_kinds_at(tmp_path) | _prose_kinds_at(tmp_path)
    assert _uncovered_kinds(("retro",), covered_after) == ["retro"]


def test_direction_a_fails_on_a_fixture_with_no_site_for_a_kind(tmp_path):
    """Mutation 2: a kind added to the vocabulary with no site anywhere. Proven against a LOCAL
    copy of EVENT_KINDS (`fake_event_kinds`), not by mutating the real `ledger.EVENT_KINDS` --
    mutating the real module under test would also trip its own import-time
    `_assert_event_fields_classified` before this guard ever ran."""
    fake_event_kinds = ledger.EVENT_KINDS + ("bogus_kind",)
    covered = _python_event_kinds_at(ROOT) | _prose_kinds_at(ROOT)
    assert _uncovered_kinds(fake_event_kinds, covered) == ["bogus_kind"]


def test_staleness_fails_when_prose_is_reworded(tmp_path):
    """Mutation 3: reword the phase emit line to a shape `_PROSE_KIND` doesn't match (kind
    reordered before `.sdlc`) -- proves the staleness test's OWN logic fires on a planted reword,
    the idiom-robustness half amendment C keeps out of the real-tree assertion above."""
    good = 'python3 "${X}/loop.py" emit .sdlc "$goal" phase --phase goal --state start'
    _plant_skill_md(tmp_path, "sdlc-loop", good)
    assert "phase" in _prose_kinds_at(tmp_path)

    reworded = 'python3 "${X}/loop.py" emit phase .sdlc "$goal" --phase goal --state start'
    _plant_skill_md(tmp_path, "sdlc-loop", reworded)
    assert "phase" not in _prose_kinds_at(tmp_path)


def test_direction_b_fails_on_a_typo_d_kind_at_an_events_stream_site(tmp_path):
    """Mutation 4: a typo'd kind string at a real EVENTS-stream call. Proven on a planted fixture
    per tests/test_import_boundary.py's own precedent -- the checker's logic is proven correct
    against fixtures, not only trusted against whatever the real tree happens to contain today."""
    _plant_py(tmp_path, "sdlc-loop", "site.py",
              'ledger.safe_append(d, "gaet", g, stream=ledger.EVENTS, verdict="pass")\n')
    python_kinds = _python_event_kinds_at(tmp_path)
    assert python_kinds == {"gaet"}
    assert _bogus_event_kinds(python_kinds, ledger.EVENT_KINDS) == ["gaet"]


# --------------------------------------------------------------------------- the checker, precision proofs


def test_ignores_a_comment_that_merely_mentions_ledger_append(tmp_path):
    """The exact false positive an unbounded regex hits in the real tree
    (skills/sdlc-loop/scripts/slices.py:119): a COMMENT containing the literal text
    "ledger.append()" must produce zero extracted kinds, even when a bare-quoted word that IS a
    plausible kind spelling ("title" here stands in for the real file's own dict key) follows a
    few lines later. AST only inspects real ast.Call nodes; a comment produces none."""
    _plant_py(tmp_path, "sdlc-loop", "slices.py",
              '# `id` lands in the `slice` telemetry event via ledger.append() eventually, once it\n'
              '# reaches the one chokepoint every write path already funnels through.\n'
              'def _normalise(item):\n'
              '    return {\n'
              '        "id": str(item.get("id") or "").strip(),\n'
              '        "title": str(item.get("title") or "").strip(),\n'
              '    }\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_ignores_a_variable_kind_dispatch(tmp_path):
    """loop.py's real `emit` dispatcher (loop.py:482) passes `kind` as a variable (`argv[4]`) --
    it is not a literal site for any ONE kind, and must not be misread as one."""
    _plant_py(tmp_path, "sdlc-loop", "loop.py",
              'def main(argv):\n'
              '    kind = argv[4]\n'
              '    ledger.append(sdlc_dir, config, kind, goal, stream=ledger.EVENTS)\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_entries_stream_calls_are_excluded(tmp_path):
    """A `claimed`-style ENTRIES-stream call (no `stream=` kwarg -> defaults to ENTRIES) must never
    count as EVENTS-stream coverage -- ENTRIES kinds are a legitimately separate vocabulary
    (ledger.py's own KINDS), not a source of EVENT_KINDS false positives."""
    _plant_py(tmp_path, "sdlc-loop", "loop.py",
              'ledger.safe_append(sdlc_dir, "claimed", goal, config=config)\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_span_aware_stream_kwarg_on_a_continuation_line(tmp_path):
    """Amendment B, proven directly: the real `spend` call (loop.py:467) wraps
    `stream=ledger.EVENTS` onto a continuation line, separated from the kind literal by a
    nested-paren kwarg (`config=state.load_config(sdlc_dir)`). Reading `stream=` off the SAME
    ast.Call node's keywords makes this trivially correct regardless of line layout -- a
    same-line/regex check would silently drop this real site."""
    _plant_py(tmp_path, "sdlc-loop", "loop.py",
              'ledger.safe_append(sdlc_dir, "spend", goal, config=state.load_config(sdlc_dir),\n'
              '                   stream=ledger.EVENTS, tokens_in=flags.get("tokens_in"))\n')
    assert _python_event_kinds_at(tmp_path) == {"spend"}


def test_an_unparseable_python_file_does_not_crash_the_scan(tmp_path):
    """This guard is not chartered to validate Python syntax -- an unparseable file contributes no
    kinds rather than aborting the whole scan with an uncaught SyntaxError."""
    _plant_py(tmp_path, "sdlc-loop", "broken.py", "def broken(:\n")
    assert _python_event_kinds_at(tmp_path) == set()


def test_a_hooks_py_file_is_scanned_too(tmp_path):
    """spec §A.6 names `hooks/` as a valid emitting location alongside skills/*/scripts/ -- proven
    directly, on a fixture shaped like the real hooks/decision_gate.py deny-path call."""
    _plant_hook_py(tmp_path, "decision_gate.py",
                    'ledger.safe_append(sdlc_dir, "gate", _NO_GOAL, stream=ledger.EVENTS,\n'
                    '                   gate="decision", verdict="block")\n')
    assert _python_event_kinds_at(tmp_path) == {"gate"}
