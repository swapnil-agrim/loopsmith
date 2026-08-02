"""#140: the five orchestrator/gate SKILL.md files instruct the agent to call `loop.py emit` at
the right moments. These tests are structural greps, mirroring `tests/test_config_discoverability.py`'s
model — they catch the prose being deleted or reworded so it silently stops instructing the
command it names.

BE HONEST ABOUT WHAT THIS PROVES. A literal-substring match proves the command line is still
present in the file; it proves NOTHING about whether the agent actually reads and follows it, or
whether the flags/values in the line are still valid against `loop.py`'s real vocabulary. Only
`test_the_idiom_still_matches_loop_pys_actual_emit_verb` below is independent of the prose files
themselves — it re-derives the verb from `loop.py`'s own source, so a rename of the `emit` verb
fails this suite loudly instead of five prose tests silently passing forever against a dead
command."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP_PY = (ROOT / "skills" / "sdlc-loop" / "scripts" / "loop.py").read_text(encoding="utf-8")

SDLC_LOOP = (ROOT / "skills" / "sdlc-loop" / "SKILL.md").read_text(encoding="utf-8")
SDLC_GOAL = (ROOT / "skills" / "sdlc-goal" / "SKILL.md").read_text(encoding="utf-8")
SDLC_PLAN_REVIEW = (ROOT / "skills" / "sdlc-plan-review" / "SKILL.md").read_text(encoding="utf-8")
SDLC_ALIGN = (ROOT / "skills" / "sdlc-align" / "SKILL.md").read_text(encoding="utf-8")
SDLC_RETRO = (ROOT / "skills" / "sdlc-retro" / "SKILL.md").read_text(encoding="utf-8")


def test_the_idiom_still_matches_loop_pys_actual_emit_verb():
    """If `loop.py`'s verb dispatch is ever renamed or restructured, this fails loudly instead of
    the five prose tests below silently passing forever against a command that no longer exists."""
    assert 'argv[1] == "emit"' in LOOP_PY, (
        "loop.py's emit-verb dispatch idiom changed — the five SKILL.md prose tests below are "
        "now checking for a command that may no longer exist; update this staleness check AND "
        "verify the prose is still accurate.")


def test_sdlc_loop_instructs_phase_emit():
    assert 'emit .sdlc "$goal" phase' in SDLC_LOOP


def test_sdlc_loop_instructs_per_phase_spend_attribution():
    assert 'spend .sdlc <tokens> "$goal" --phase' in SDLC_LOOP


def test_sdlc_goal_instructs_phase_emit():
    assert 'emit .sdlc "<goal>" phase' in SDLC_GOAL


def test_sdlc_plan_review_instructs_gate_emit():
    assert 'emit .sdlc "$goal" gate --gate plan_review' in SDLC_PLAN_REVIEW


def test_sdlc_plan_review_verdict_mapping_is_present():
    assert "SOUND-WITH-REFINEMENTS" in SDLC_PLAN_REVIEW and "`warn`" in SDLC_PLAN_REVIEW
    assert "FIX-FIRST" in SDLC_PLAN_REVIEW and "`block`" in SDLC_PLAN_REVIEW


def test_sdlc_align_instructs_gate_emit():
    assert 'emit .sdlc "(alignment)" gate --gate alignment' in SDLC_ALIGN


def test_sdlc_align_never_instructs_a_block_verdict():
    """Align is read-only/advisory and never blocks a merge — the prose must say `block` is never
    used here rather than silently omitting a value an agent might otherwise guess belongs."""
    assert "--verdict pass|warn" in SDLC_ALIGN
    assert "pass|warn|block" not in SDLC_ALIGN


def test_sdlc_retro_instructs_retro_emit():
    assert 'emit .sdlc "$goal" retro' in SDLC_RETRO


def test_sdlc_retro_grade_values_match_ledger_retro_grades():
    assert "achieved|partial|diverged" in SDLC_RETRO
