import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_plan_review_skill_wellformed():
    t = (ROOT / "skills" / "sdlc-plan-review" / "SKILL.md").read_text()
    assert "name: sdlc-plan-review" in t
    # generic — no OnShot leakage
    for banned in ("media-orch", "Temporal", "R2", "OnShot", "RunPod"):
        assert banned not in t


def test_plan_review_has_alignment_gate():
    t = (ROOT / "skills" / "sdlc-plan-review" / "SKILL.md").read_text()
    assert "north-star.md" in t                     # vision-first alignment gate
    assert "non-goal" in t.lower() and "FIX-FIRST" in t   # contradicting the strategy blocks
    assert "architecture rule" in t.lower()         # ...and violating an architecture rule blocks


def test_goal_skill_wellformed_and_records():
    t = (ROOT / "skills" / "sdlc-goal" / "SKILL.md").read_text()
    assert "name: sdlc-goal" in t and "allowed-tools:" in t
    assert "loop.py" in t and "record" in t        # records the outcome to .sdlc state


def test_context_skill_gates_on_kg():
    t = (ROOT / "skills" / "sdlc-context" / "SKILL.md").read_text()
    assert "name: sdlc-context" in t and "allowed-tools:" in t
    assert "kg.py" in t and "status" in t          # gated: only acts when the KG is enabled/built
    assert "graphify query" in t and "--mcp" in t  # push (pre-flight query) + pull (live MCP)


def test_orchestrators_run_context_preflight():
    for skill in ("sdlc-loop", "sdlc-goal"):
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "sdlc-context" in t, f"{skill} must run the context pre-flight"


def test_vision_skill_wellformed():
    t = (ROOT / "skills" / "sdlc-vision" / "SKILL.md").read_text()
    assert "name: sdlc-vision" in t and "allowed-tools:" in t
    assert "--vision" in t                              # scaffolds the north-star via the init flag
    assert "north-star" in t and "non-goals" in t       # the tiers it fills (non-goals feed the gate)


def test_velocity_skill_wellformed():
    t = (ROOT / "skills" / "sdlc-velocity" / "SKILL.md").read_text()
    assert "name: sdlc-velocity" in t and "allowed-tools:" in t
    assert "velocity.py" in t and "measure" in t and "estimate" in t   # git-throughput sizing


def test_radar_skill_is_dry_run_by_default():
    t = (ROOT / "skills" / "sdlc-radar" / "SKILL.md").read_text()
    assert "name: sdlc-radar" in t and "radar.py" in t and "agenda" in t
    assert "dry-run" in t.lower()                                       # Phase A writes nothing external
    assert "file nothing to github" in t.lower() or "never file" in t.lower()


def test_doctor_skill_wellformed():
    t = (ROOT / "skills" / "sdlc-doctor" / "SKILL.md").read_text()
    assert "name: sdlc-doctor" in t and "doctor.py" in t and "check" in t
    assert "never run an interactive login" in t.lower() or "hand them the command" in t.lower()


def test_versions_aligned():
    p = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert p["version"] == "0.5.0" and mk["plugins"][0]["version"] == "0.5.0"
