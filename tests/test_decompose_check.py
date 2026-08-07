"""goal_decompose 8a: the deterministic `goal_size` classifier + `loop.py`'s `decompose-check` verb
(#519) — the `log`/`park` rungs of the mode ladder only; the meta-goal filing branch (`file` mode's
real behavior) ships in a follow-up slice, so `file` degrades to `park` here. Opt-in
(`goal_decompose.enabled`), zero LLM, fail-open, off by default. Hermetic, $0.

No conftest.py in this repo (tests/test_import_boundary.py's own docstring records why) — every
helper below is copied in, not imported, mirroring test_backlog_precheck.py / test_sources.py."""
import json
import pathlib
import importlib.util
import tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _recording_runner(by_subcommand=None):
    """Fake `gh` runner: records every call, returns canned stdout keyed by the gh verb (args[1]).
    Copied from tests/test_sources.py:14-23."""
    calls = []
    by_subcommand = by_subcommand or {}
    def run(args):
        calls.append(list(args))
        return by_subcommand.get(args[1] if len(args) > 1 else args[0], "")
    run.calls = calls
    return run


class _FakeSource:
    """A source stub carrying a canned title/body plus full call-recording — enough surface for
    `decompose_check` to drive (fetch_title_body/park/note/complete/fail), mirroring
    tests/test_backlog_precheck.py's own `_FakeSource` extended with the new read method."""
    def __init__(self, title="", body=""):
        self.calls = []
        self._title = title
        self._body = body

    def fetch_title_body(self, goal):
        self.calls.append(("fetch_title_body", goal))
        return {"title": self._title, "body": self._body}

    def park(self, goal, reason): self.calls.append(("park", goal, reason))
    def note(self, goal, text): self.calls.append(("note", goal, text))
    def complete(self, goal): self.calls.append(("complete", goal))
    def fail(self, goal, reason): self.calls.append(("fail", goal, reason))


def _sdlc(tmp_path, config):
    """A bare .sdlc — config.json + a scaffolded run cursor + review queue — for tests that drive
    `decompose_check`/`_record` directly instead of through `run_loop`. Mirrors
    tests/test_backlog_precheck.py's `_sdlc()` / tests/test_loop.py's `_telemetry_base()`."""
    base = tmp_path / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(config))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    return str(base)


# An unambiguous epic-shaped body: 4 "##" sections (>= SECTION_THRESHOLD) *and* explicit
# Phase-1/2/3 markers — flagged regardless of exactly which signal fires first, so tests that only
# care about "is this thing flagged" (not "by which signal") don't couple to threshold tuning.
_EPIC_BODY = (
    "## Phase 1: backend\nBuild the backend pieces.\n\n"
    "## Phase 2: frontend\nBuild the UI pieces.\n\n"
    "## Phase 3: migration\nMigrate the old data.\n\n"
    "## Verification\nRun the whole suite.\n"
)

_SMALL_BODY = (
    "Add a retry to fetch_comments so a transient 502 doesn't fail the whole precheck.\n"
    "Tests: simulate a 502 then success; simulate an exhausted retry.\n"
    "Verification: pytest tests/test_sources.py -q.\n"
)


# --------------------------------------------------------------------- goal_size.classify (unit)


def test_classify_small_body_is_not_flagged():
    gs = _mod("goal_size")
    flagged, reason = gs.classify(_SMALL_BODY)
    assert flagged is False and reason == ""


def test_classify_flags_a_body_over_the_word_threshold():
    gs = _mod("goal_size")
    body = "word " * (gs.WORD_THRESHOLD + 50)          # one line, far over on words alone
    flagged, reason = gs.classify(body)
    assert flagged is True and "words" in reason


def test_classify_flags_a_body_over_the_line_threshold_but_under_the_word_threshold():
    gs = _mod("goal_size")
    body = "\n".join(f"item {i}" for i in range(gs.LINE_THRESHOLD + 10))   # 2 words/line
    assert len(body.split()) <= gs.WORD_THRESHOLD          # isolates the LINE signal specifically
    flagged, reason = gs.classify(body)
    assert flagged is True and "lines" in reason


def test_classify_flags_six_or_more_independent_h2_sections():
    """Retuned for #520's corpus-calibrated SECTION_THRESHOLD (3 -> 6, see goal_size.py's module
    docstring): the old value of 3 flagged every conventionally-shaped Context/Scope/AC/Verification
    goal in this repo's own issue history with zero true positives. Body has exactly 6 H2 sections —
    the new threshold's own boundary."""
    gs = _mod("goal_size")
    body = ("## Context\nshort\n\n## Scope\nshort\n\n## Approach\nshort\n\n"
            "## Risks\nshort\n\n## Testing\nshort\n\n## Verification\nshort\n")
    flagged, reason = gs.classify(body)
    assert flagged is True and "sections" in reason


def test_classify_does_not_flag_five_h2_sections():
    """Boundary pin one below the new threshold: 5 is the fence-stripped corpus's own observed
    max (see goal_size.py's module docstring), so this is also the tightest real-world case the
    corpus itself validates precision against."""
    gs = _mod("goal_size")
    body = ("## A\nshort\n\n## B\nshort\n\n## C\nshort\n\n"
            "## D\nshort\n\n## E\nshort\n")
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_does_not_flag_only_two_h2_sections():
    gs = _mod("goal_size")
    body = "## Context\nshort\n\n## Scope\nshort\n"
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_h3_subsections_never_count_toward_the_h2_section_signal():
    """Re-armed for #520's SECTION_THRESHOLD=6 (was 3): with only 3 H3 subsections, even a
    regression that started counting H3 as a section would total just 1+3=4, still under the new
    threshold, so that body could no longer catch anything — silently vacuous at the new
    threshold, not just weak. Widened to 1 H2 + 6 H3 (total 7 if H3 wrongly counted) so the
    threshold itself is what keeps this test load-bearing; proof: a scratch copy of goal_size.py
    with `_SECTION_RE` widened to `^###?[ \\t]+\\S.*$` (H2 or H3 both match) classifies this exact
    body as flagged=True, reason='7 independent ## sections (>= 6)' — confirming this body WOULD
    fail against that regression, while the real (H2-only) regex classifies it as flagged=False."""
    gs = _mod("goal_size")
    body = "## Context\n### a\nx\n### b\nx\n### c\nx\n### d\nx\n### e\nx\n### f\nx\n"   # 1 H2 + 6 H3
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_flags_four_or_more_top_level_checkboxes():
    gs = _mod("goal_size")
    body = "- [ ] build the classifier\n- [ ] wire the verb\n- [x] write tests\n- [ ] update docs\n"
    flagged, reason = gs.classify(body)
    assert flagged is True and "checkboxes" in reason


def test_classify_does_not_flag_only_three_top_level_checkboxes():
    gs = _mod("goal_size")
    body = "- [ ] one\n- [ ] two\n- [x] three\n"
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_indented_checkboxes_are_not_top_level():
    gs = _mod("goal_size")
    body = "top task\n  - [ ] sub 1\n  - [ ] sub 2\n  - [ ] sub 3\n  - [ ] sub 4\n"
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_flags_explicit_multi_phase_structure():
    gs = _mod("goal_size")
    body = "## Phase 1: backend\ndo it\n\n## Phase 2: frontend\ndo it\n"   # only 2 sections, isolates phase
    flagged, reason = gs.classify(body)
    assert flagged is True and "phase" in reason.lower()


def test_classify_does_not_flag_a_single_phase_mention():
    gs = _mod("goal_size")
    body = "## Phase 1: backend\ndo it\n\nsome other prose with no second phase.\n"
    flagged, reason = gs.classify(body)
    assert flagged is False


def test_classify_reason_is_always_single_line():
    """`reason` lands verbatim in a park detail / action-log field — both reject raw newlines
    (state.py's `_offboard`/actionlog's `reject_newline`) — so a multi-line reason would corrupt
    either channel, not just look ugly. The sections body below is bumped to 6 (#520's new
    SECTION_THRESHOLD) — at the old 3-section body this arm no longer flags at all, so `reason`
    was always "" (trivially single-line) and this stopped actually exercising the sections-signal
    reason string; 6 makes it produce a real, non-empty reason again."""
    gs = _mod("goal_size")
    bodies = ["word " * (gs.WORD_THRESHOLD + 5),
              "\n".join(f"x {i}" for i in range(gs.LINE_THRESHOLD + 5)),
              "## a\nx\n\n## b\nx\n\n## c\nx\n\n## d\nx\n\n## e\nx\n\n## f\nx\n",
              "- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
              "## Phase 1\nx\n\n## Phase 2\nx\n"]
    for body in bodies:
        _, reason = gs.classify(body)
        assert "\n" not in reason


# --------------------------------------------------------------------- #520: negative-precision synthetics
#
# Synthetic (not corpus) cases for _strip_fences / the anchored _PHASE_RE / the widened checkbox
# dialect — pinned here beside the other classifier units rather than as tests/fixtures/goal_size/
# corpus fixtures (per #520's adopted spec: these are constructed edge cases, not real issue bodies).


def test_classify_fenced_code_hash_lines_do_not_count_as_sections():
    """Without _strip_fences, the 5 fenced '## fake N' lines below would join the 2 real sections
    for a raw total of 7 (>= SECTION_THRESHOLD) — this proves they're excluded, not just
    coincidentally under threshold."""
    gs = _mod("goal_size")
    body = ("## Context\nshort\n\n"
            "```\n## fake 1\n## fake 2\n## fake 3\n## fake 4\n## fake 5\n```\n\n"
            "## Scope\nshort\n")
    flagged, reason = gs.classify(body)
    assert flagged is False, reason


def test_classify_shell_style_hash_comments_inside_fences_do_not_count():
    """A fenced shell snippet's `## Banner` comment lines are visually identical to a markdown H2
    to a naive regex; only fencing (not comment-syntax detection) keeps them out. Same 2-real+5-fake
    shape as the fenced-code case above, so it would also raw-total 7 without stripping."""
    gs = _mod("goal_size")
    body = ("## Context\nshort\n\n"
            "```bash\n## Section one\n## Section two\n## Section three\n## Section four\n"
            "## Section five\necho hi\n```\n\n"
            "## Scope\nshort\n")
    flagged, reason = gs.classify(body)
    assert flagged is False, reason


def test_classify_prose_phase_accident_does_not_flag():
    """The old unanchored `_PHASE_RE` flagged this exact sentence shape as a genuine two-phase body
    (reproduced live against this repo's own #519, whose prose mentions 'Phase-1/Phase-2' without
    describing an actual multi-phase goal). The new line-start-anchored regex requires 'phase' to
    open the line (optionally after a heading/bullet/number marker), never mid-sentence."""
    gs = _mod("goal_size")
    body = "This is the phase-2 follow-up to the phase-1 work in #500.\n"
    flagged, reason = gs.classify(body)
    assert flagged is False, reason


def test_classify_plus_bullet_checkboxes_count_now():
    """Dialect pin: `+` is a valid GFM bullet marker the old regex (`[-*]` only) missed."""
    gs = _mod("goal_size")
    body = "+ [ ] one\n+ [ ] two\n+ [ ] three\n+ [ ] four\n"
    flagged, reason = gs.classify(body)
    assert flagged is True and "checkboxes" in reason


def test_classify_two_space_gap_checkboxes_count_now():
    """Dialect pin: a two-space gap between the bullet and `[` is valid GFM the old regex (exactly
    one `[ \\t]`) missed."""
    gs = _mod("goal_size")
    body = "-  [ ] one\n-  [ ] two\n-  [ ] three\n-  [ ] four\n"
    flagged, reason = gs.classify(body)
    assert flagged is True and "checkboxes" in reason


def test_classify_unterminated_fence_blanks_to_eof():
    """An opened-but-never-closed fence blanks EVERYTHING after it, to EOF — not just some
    heuristic extent — conservative, failing TOWARD not-flagging a malformed body. Without that,
    the 2 real sections before the fence plus the 4 '## ' lines after the (never-closed) fence would
    raw-total 6 (>= SECTION_THRESHOLD); properly blanked, only the 2 real sections before the fence
    count."""
    gs = _mod("goal_size")
    body = ("## Context\nshort\n\n## Scope\nshort\n\n"
            "```\nsome code\n"
            "## D\n## E\n## F\n## G\n")
    flagged, reason = gs.classify(body)
    assert flagged is False, reason


def test_classify_mismatched_fence_delimiter_does_not_close_the_fence():
    """CommonMark: a fence closes only on a matching delimiter CHARACTER — a ~~~ line inside an
    open ``` fence is ordinary fenced content, not a close. Without that rule, the ~~~ below would
    have closed the ``` fence early, exposing the 5 '## ' lines that follow it as real sections (1
    real section before the fence + those 5 = 6, >= SECTION_THRESHOLD) — this bug failed TOWARD
    flagging, never away from it."""
    gs = _mod("goal_size")
    body = ("## Context\nshort\n\n"
            "```\nsome code\n~~~\n## D\n## E\n## F\n## G\n## H\n```\n")
    flagged, reason = gs.classify(body)
    assert flagged is False, reason


# --------------------------------------------------------------------- config gate (OFF / fail-open)


def test_decompose_check_off_when_disabled_touches_nothing(tmp_path, capsys):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": False}})
    src = _FakeSource(body=_EPIC_BODY)
    assert lp.decompose_check(base, "1", json.loads((pathlib.Path(base) / "config.json").read_text()), src) == "OFF"
    assert src.calls == []
    assert capsys.readouterr().err == ""           # OFF is silent — no spurious warning


def test_decompose_check_off_when_key_absent_touches_nothing(tmp_path, capsys):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {})                       # no goal_decompose key at all
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)
    assert lp.decompose_check(base, "1", cfg, src) == "OFF"
    assert src.calls == []
    assert capsys.readouterr().err == ""


def test_decompose_check_off_when_enabled_key_absent_touches_nothing(tmp_path, capsys):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {}})    # present, but no "enabled" key
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)
    assert lp.decompose_check(base, "1", cfg, src) == "OFF"
    assert src.calls == []
    assert capsys.readouterr().err == ""


def test_decompose_check_off_on_falsy_malformed_config(tmp_path, capsys):
    """Falsy-malformed (`false`/`0`/`""`/`[]`) degrades the same as absent — OFF, no crash, no
    warning (plan-review change 4)."""
    lp = _mod("loop")
    for i, bogus in enumerate((False, 0, "", [])):
        base = _sdlc(tmp_path / str(i), {"goal_decompose": bogus})   # fresh dir per case
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        src = _FakeSource(body=_EPIC_BODY)
        assert lp.decompose_check(base, "1", cfg, src) == "OFF", bogus
        assert src.calls == [], bogus
        assert capsys.readouterr().err == "", bogus


def test_decompose_check_fails_open_on_truthy_malformed_config(tmp_path, capsys):
    """Truthy-malformed (`"on"`/`true`/`5` — where `.get` would raise AttributeError) fails OPEN to
    PROCEED via the outer catch, with exactly one stderr line — never a crash, never a silent OFF
    (plan-review change 4: this must not be confused with the falsy-malformed OFF case above)."""
    lp = _mod("loop")
    for i, bogus in enumerate(("on", True, 5)):
        base = _sdlc(tmp_path / str(i), {"goal_decompose": bogus})   # fresh dir per case
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        src = _FakeSource(body=_EPIC_BODY)
        assert lp.decompose_check(base, "1", cfg, src) == "PROCEED", bogus
        assert src.calls == [], bogus                  # the gate itself raised — never reached the read
        err = capsys.readouterr().err
        assert err.strip() != "" and err.count("\n") == 1, bogus


def test_decompose_check_proceeds_for_an_unflagged_goal(tmp_path):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_SMALL_BODY)
    assert lp.decompose_check(base, "1", cfg, src) == "PROCEED"
    assert not any(c[0] == "park" for c in src.calls)   # not flagged -> never parked, even in park mode


# --------------------------------------------------------------------- first-line anchoring


def test_decompose_check_proceeds_for_a_decomposition_child_marked_first_line(tmp_path):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    body = "loopsmith:decomposed-from=100\n\n" + _EPIC_BODY     # would otherwise clearly flag
    src = _FakeSource(body=body)
    assert lp.decompose_check(base, "1", cfg, src) == "PROCEED"
    assert src.calls == [("fetch_title_body", "1")]              # read, but never classified/parked


def test_decompose_check_proceeds_for_a_meta_goal_marked_first_line(tmp_path):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    body = "loopsmith:decompose-of=100\n\n" + _EPIC_BODY
    src = _FakeSource(body=body)
    assert lp.decompose_check(base, "1", cfg, src) == "PROCEED"
    assert src.calls == [("fetch_title_body", "1")]


def test_decompose_check_a_marker_in_the_title_only_does_not_refuse(tmp_path):
    """Negative anchoring test (plan-review change 7): the guard reads the BODY only. A marker
    sitting in the title must never exempt an otherwise-epic-shaped body."""
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "log"}, "action_log": {"enabled": False}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(title="loopsmith:decomposed-from=100 — my epic", body=_EPIC_BODY)
    result = lp.decompose_check(base, "1", cfg, src)
    assert result.startswith("PROCEED (flagged:")           # classification actually ran


def test_decompose_check_a_marker_mid_body_only_does_not_refuse(tmp_path):
    """Negative anchoring test (plan-review change 7 + base AC): a marker anywhere other than the
    first line of the body must never exempt the goal."""
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "log"}, "action_log": {"enabled": False}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    body = "This is a normal-looking first line.\n" + _EPIC_BODY + "\nloopsmith:decomposed-from=100\n"
    src = _FakeSource(body=body)
    result = lp.decompose_check(base, "1", cfg, src)
    assert result.startswith("PROCEED (flagged:")


def test_decompose_check_anchoring_is_crlf_tolerant(tmp_path):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    body = "loopsmith:decomposed-from=100\r\n\r\n" + _EPIC_BODY.replace("\n", "\r\n")
    src = _FakeSource(body=body)
    assert lp.decompose_check(base, "1", cfg, src) == "PROCEED"
    assert not any(c[0] == "park" for c in src.calls)


def test_decompose_check_verb_reads_the_marker_constant_live_not_a_hardcoded_copy(tmp_path):
    """#521 review (SHOULD-FIX): the single-source-of-truth pin was one-sided.
    tests/test_backlog_check.py's test_backlog_check_exempt_reads_the_constant_live_not_a_hardcoded_copy
    proves backlog_check reads goal_size's constant live, by mutating the ACTUAL goal_size module
    object backlog_check holds. But loop.py's own `_load("goal_size")` call resolves a FRESH module
    instance on every invocation (never cached), so that same trick doesn't reach it -- nothing
    previously proved decompose_check's guard (loop.py:698) actually reads `gs.DECOMPOSED_FROM_MARKER`
    at runtime rather than a hand-typed literal that merely happens to match today.

    Monkeypatch loop.py's OWN `_load` (the name decompose_check's `gs = _load("goal_size")` resolves
    via its module globals at call time) so it hands back a stand-in with a RENAMED
    DECOMPOSED_FROM_MARKER but the REAL `classify`. A body whose first line carries the OLD/real
    marker text no longer matches the guard's (renamed) constant, so it falls through to real
    classification -- and, being unambiguously epic-shaped, gets flagged and parked instead of
    short-circuiting to a bare PROCEED. A hardcoded-literal guard would be blind to this swap and
    would still short-circuit to PROCEED regardless -- exactly the gap this test closes."""
    lp = _mod("loop")
    gs = _mod("goal_size")

    class _StubGoalSize:
        DECOMPOSED_FROM_MARKER = "totally-renamed-marker="
        DECOMPOSE_OF_MARKER = "totally-renamed-meta="
        classify = staticmethod(gs.classify)             # real classification, unmodified

    real_load = lp._load
    lp._load = lambda name: _StubGoalSize if name == "goal_size" else real_load(name)
    try:
        base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        body = "loopsmith:decomposed-from=100\n\n" + _EPIC_BODY     # the OLD/real marker text
        src = _FakeSource(body=body)
        result = lp.decompose_check(base, "1", cfg, src)
        # renamed constant -> the OLD marker text no longer matches the guard -> falls through to
        # real classification -> epic body gets flagged+parked, NOT a bare "PROCEED".
        assert result.startswith("PARKED"), result
    finally:
        lp._load = real_load


# --------------------------------------------------------------------- log mode: zero mutation


def test_log_mode_is_zero_mutation_and_writes_one_actionlog_entry(tmp_path):
    """The load-bearing proof (plan-review change 1): a REAL GitHubSource, built the same way
    production code builds one, driven through decompose_check — the read must show up in the
    recording fake's own call log (proving the verb used `source.fetch_title_body`, not a bypassing
    module-level shell-out — a bypass would make this whole test vacuous), and not one mutating
    `issue edit|comment|close` call may ever appear."""
    lp = _mod("loop")
    sources = _mod("sources")
    actionlog = _mod("actionlog")
    base = _sdlc(tmp_path, {
        "goal_decompose": {"enabled": True, "mode": "log"},
        "action_log": {"enabled": True},
        "discovery": {"source": "github"},
    })
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    run = _recording_runner({"view": json.dumps({"title": "an epic issue", "body": _EPIC_BODY})})
    gh = sources.GitHubSource(cfg, run=run)

    result = lp.decompose_check(base, "42", cfg, gh)

    assert result.startswith("PROCEED (flagged:")
    assert any(len(c) > 1 and c[0] == "issue" and c[1] == "view" for c in run.calls), \
        "the read never reached the recording fake — decompose_check likely bypassed the source"
    mutating = [c for c in run.calls if len(c) > 1 and c[0] == "issue" and c[1] in ("edit", "comment", "close")]
    assert mutating == []

    entries = [e for e in actionlog.read_goal(base, "42") if e["kind"] == "decompose_check"]
    assert len(entries) == 1
    assert entries[0]["verdict"] == "flagged"
    assert entries[0]["mode"] == "log"
    assert entries[0]["reason"]                 # non-empty, and (schema-enforced) newline-free


def test_log_mode_without_action_log_enabled_still_proceeds_without_mutating(tmp_path):
    """`action_log` is its own independent opt-in — decompose_check's `log` mode must not depend on
    it being on to stay non-mutating."""
    lp = _mod("loop")
    sources = _mod("sources")
    base = _sdlc(tmp_path, {
        "goal_decompose": {"enabled": True, "mode": "log"},
        "discovery": {"source": "github"},
    })
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    run = _recording_runner({"view": json.dumps({"title": "an epic issue", "body": _EPIC_BODY})})
    gh = sources.GitHubSource(cfg, run=run)
    result = lp.decompose_check(base, "42", cfg, gh)
    assert result.startswith("PROCEED (flagged:")
    mutating = [c for c in run.calls if len(c) > 1 and c[0] == "issue" and c[1] in ("edit", "comment", "close")]
    assert mutating == []


# --------------------------------------------------------------------- park mode: single _record


def test_park_mode_records_exactly_once_with_needs_decision_reason_class(tmp_path):
    """Precedent: tests/test_backlog_precheck.py:51-61 (cursor-advance proof) + plan-review change 6
    (the ledger park event's reason_class)."""
    lp = _mod("loop")
    state = _mod("state")
    ledger = _mod("ledger")
    base = _sdlc(tmp_path, {
        "goal_decompose": {"enabled": True, "mode": "park"},
        "ledger": {"enabled": True, "actor": "rae"},
        "telemetry": {"enabled": True},   # the EVENTS stream gate is ledger AND telemetry, both
    })
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    gs = _mod("goal_size")
    _, expected_reason = gs.classify(_EPIC_BODY)
    src = _FakeSource(body=_EPIC_BODY)

    result = lp.decompose_check(base, "7", cfg, src)

    expected_detail = f"too large per goal_size ({expected_reason}) — needs manual decomposition"
    assert result == "PARKED " + expected_detail
    park_calls = [c for c in src.calls if c[0] == "park"]
    assert len(park_calls) == 1                                       # exactly one _record park
    assert park_calls[0] == ("park", "7", expected_detail)
    assert state.load_cursor(base)["run_iteration"] == 1              # the park counted as an iteration

    events = [e for e in ledger.read_all(base, stream=ledger.EVENTS) if e["kind"] == "park"]
    assert len(events) == 1
    assert events[0]["reason_class"] == "needs_decision"


def test_file_mode_behaves_as_park_in_this_slice(tmp_path):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "file"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)
    result = lp.decompose_check(base, "7", cfg, src)
    assert result.startswith("PARKED ")
    assert len([c for c in src.calls if c[0] == "park"]) == 1


def test_park_survives_a_failure_after_source_park_has_already_landed(tmp_path, monkeypatch):
    """A flagged park path must NEVER report PROCEED once the actual park mutation
    (`source.park`) has already gone out — a caller reading PROCEED would go implement the very
    epic-shaped goal this feature exists to catch, on top of it now ALSO being parked on GitHub.
    `_record` calls `source.park` first, then cursor/ledger/actionlog bookkeeping; this monkeypatches
    `ledger.safe_append` (loop.py's own module-level reference, called from inside `_record` strictly
    AFTER `source.park`) to raise, simulating exactly that "mutation landed, bookkeeping blew up"
    ordering — a bare `_record(...)` call with no try/except of its own lets that exception fall
    through to decompose_check's outer catch, which used to answer PROCEED."""
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "park"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)

    def _boom(*a, **kw):
        raise RuntimeError("ledger boom")
    monkeypatch.setattr(lp.ledger, "safe_append", _boom)

    result = lp.decompose_check(base, "7", cfg, src)

    assert result.split()[0] == "PARKED", \
        f"a flagged park path must never downgrade to PROCEED after the mutation landed, got: {result!r}"
    park_calls = [c for c in src.calls if c[0] == "park"]
    assert len(park_calls) == 1, "the park mutation itself must still be attempted exactly once"


# --------------------------------------------------------------------- unrecognized mode


def test_unrecognized_mode_falls_back_to_log_behavior_with_a_stderr_warning(tmp_path, capsys):
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True, "mode": "bogus-mode"}})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)

    result = lp.decompose_check(base, "1", cfg, src)

    assert result is not None
    assert isinstance(result, str)
    assert result.startswith("PROCEED (flagged:")        # log behavior, never PARKED
    assert not any(c[0] == "park" for c in src.calls)
    err = capsys.readouterr().err
    assert "bogus-mode" in err


def test_absent_mode_key_behaves_as_log_silently(tmp_path, capsys):
    """Plan-review change 3: an ABSENT `mode` key (not just an unrecognized string) must default to
    `log` behavior with NO stderr warning at all — the unrecognized-mode fallback above is for a
    genuinely unrecognized non-empty string only, never for the common "I didn't set mode" case."""
    lp = _mod("loop")
    base = _sdlc(tmp_path, {"goal_decompose": {"enabled": True}})   # enabled, no "mode" key
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)

    result = lp.decompose_check(base, "1", cfg, src)

    assert result.startswith("PROCEED (flagged:")
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------- CLI dispatch (local mode)


def test_decompose_check_cli_local_mode_parks_an_epic(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "goals").mkdir(parents=True)
        (base / "state").mkdir()
        (base / "config.json").write_text(json.dumps(
            {"goal_decompose": {"enabled": True, "mode": "park"}, "budget": {"max_iterations": 100}}))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (base / "state" / "review-queue.md").write_text("# Q\n")
        goal_path = base / "goals" / "0001.md"
        goal_path.write_text("---\nid: 0001\nstatus: pending\ntitle: a huge multi-phase goal\n---\n"
                              + _EPIC_BODY)
        rc = lp.main(["loop.py", "decompose-check", str(base), str(goal_path)])
        assert rc == 0
        assert capsys.readouterr().out.startswith("PARKED")


def test_decompose_check_cli_prints_off_when_disabled(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "goals").mkdir(parents=True)
        (base / "state").mkdir()
        (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 100}}))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        goal_path = base / "goals" / "0001.md"
        goal_path.write_text("---\nid: 0001\nstatus: pending\ntitle: t\n---\nx\n")
        rc = lp.main(["loop.py", "decompose-check", str(base), str(goal_path)])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "OFF"


def test_decompose_check_is_in_the_usage_string(capsys):
    lp = _mod("loop")
    assert lp.main(["loop.py"]) == 2
    assert "decompose-check <dir> <goal>" in capsys.readouterr().err


# --------------------------------------------------------------------- config-template discoverability


def test_goal_decompose_key_is_discoverable_in_the_scaffolded_config():
    """A feature nobody can find is a feature that doesn't ship (tests/test_config_discoverability.py's
    own framing) — but that file's `_GATE_READ` regex is scoped to `gates.*` sub-keys and will not
    catch a top-level key like `goal_decompose`, so this is a focused, standalone pin instead of an
    extension of that file."""
    tmpl_path = ROOT / "skills" / "sdlc-init" / "templates" / "config.json.tmpl"
    tmpl = tmpl_path.read_text(encoding="utf-8")
    cfg = json.loads(tmpl)          # also proves the template is still valid JSON with the new key
    assert cfg.get("goal_decompose") == {"enabled": False, "mode": "log", "max_children": 8}
    assert "_goal_decompose" in cfg
    explainer = cfg["_goal_decompose"]
    assert "NOT YET WIRED" in explainer            # honest about max_children + file-mode (review change 5)
    assert "park" in explainer and "log" in explainer


def test_scaffolded_default_config_is_off_end_to_end(tmp_path):
    """Feature invisible unless enabled; zero behavior change with default config — proven against
    the REAL shipped template, not a hand-written stand-in."""
    lp = _mod("loop")
    tmpl_path = ROOT / "skills" / "sdlc-init" / "templates" / "config.json.tmpl"
    tmpl_cfg = json.loads(tmpl_path.read_text(encoding="utf-8"))
    base = _sdlc(tmp_path, {"goal_decompose": tmpl_cfg["goal_decompose"]})
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = _FakeSource(body=_EPIC_BODY)
    assert lp.decompose_check(base, "1", cfg, src) == "OFF"
    assert src.calls == []
