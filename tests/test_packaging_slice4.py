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


def test_versions_aligned():
    p = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert p["version"] == "0.5.0" and mk["plugins"][0]["version"] == "0.5.0"
