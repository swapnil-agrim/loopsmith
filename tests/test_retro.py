"""sdlc-retro: the Retrospective/learning executor. Pins that the skill is well-formed, does its three
things (structural + product reflection, intent-vs-shipped, three-store harvest routed to LoopSmith's
OWN stores), stays advisory (proposes/parks standing changes), is wired into BOTH orchestrators'
Retrospective phase, and leaks nothing from the source repo it was genericized from."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RETRO = ROOT / "skills" / "sdlc-retro" / "SKILL.md"


def _t():
    return RETRO.read_text()


def test_skill_exists_with_frontmatter():
    assert RETRO.exists()
    t = _t()
    assert "name: sdlc-retro" in t
    assert "description:" in t and "allowed-tools:" in t


def test_does_the_three_things():
    t = _t().lower()
    assert "structural reflection" in t and "product reflection" in t
    assert "intent-vs-shipped" in t
    assert "achieved" in t and "partial" in t and "diverged" in t     # the intent grade


def test_three_stores_are_loopsmiths_own():
    t = _t()
    assert ".sdlc/context/north-star.md" in t                          # north-star store
    assert ".sdlc/project.md" in t and "CLAUDE.md" in t                # standing-rule store
    assert ".sdlc/journey" in t or "loop.py" in t                      # audit-trail store


def test_advisory_and_fail_open():
    t = _t().lower()
    assert "advisory" in t and "park" in t                             # proposes; parks standing changes
    assert "never" in t and ("auto-write" in t or "unattended" in t)   # no unattended standing writes
    assert "fail-open" in t


def test_wired_into_both_orchestrators_retrospective_phase():
    for orch in ("sdlc-goal", "sdlc-loop"):
        t = (ROOT / "skills" / orch / "SKILL.md").read_text()
        assert "sdlc-retro" in t, f"{orch} does not run sdlc-retro"
        assert "Retrospective" in t, f"{orch} has no Retrospective phase step"


def test_no_source_repo_leakage():
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch")
    t = _t()
    for b in banned:
        assert b not in t, f"sdlc-retro leaked '{b}'"


def test_proposes_standing_doc_retirements_not_just_additions():
    """Docs grow by addition and shrink by nobody: adding a rule has an obvious moment, retiring one
    never does. Retro is that moment — and a demotion is parked for approval like any other standing
    change, never written unattended."""
    t = _t()
    low = t.lower()
    assert "standing-doc rot" in low
    assert "mechanically" in low and "demot" in low      # a rule CI now enforces is redundant prose
    assert "premise moved" in low                        # ...or one the code has outgrown
    assert "superseded" in low and ".sdlc/plans/" in t   # shipped plans weaken the plan gate
    assert "nothing rotted" in low                       # the common answer must be allowed
    assert "archive" in low and "never delete" in low


def test_rot_pass_defers_the_mechanical_half_to_doctor():
    """Split by blast radius: doctor reports references that provably don't resolve and needs no
    approval; retro changes meaning, so it asks."""
    assert "sdlc-doctor" in _t()
