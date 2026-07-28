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
    assert "draft" in t.lower() and "refine" in t.lower()   # drafts from the repo first, user refines (no blank page)


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


# Each portable executor must (1) defer to its superpowers companion on Claude via a resolution header,
# and (2) ship a committed parity review vs superpowers asserting >= par. Add a row as each lands.
PORTABLE_EXECUTORS = {
    "sdlc-verify": ("verification-before-completion", "verify.md"),
    "sdlc-implement": ("test-driven-development", "implement.md"),
    "sdlc-plan": ("writing-plans", "plan.md"),
    "sdlc-brainstorm": ("brainstorming", "brainstorm.md"),
    "sdlc-review": ("requesting-code-review", "review.md"),
}


def test_portable_executors_defer_to_superpowers_and_have_parity():
    for skill, (sp, doc) in PORTABLE_EXECUTORS.items():
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert f"name: {skill}" in t
        assert f"superpowers:{sp}" in t, f"{skill}: no resolution header deferring to superpowers on Claude"
        parity = (ROOT / "docs" / "executor-parity" / doc).read_text()
        assert skill in parity and "par" in parity.lower(), f"{doc}: missing parity verdict"


def test_versions_aligned():
    p = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert p["version"] == "0.9.2" and mk["plugins"][0]["version"] == "0.9.2"


def test_plan_review_dispositions_close_the_loop():
    """A FIX-FIRST that sends the plan back with no record of what happened to each finding is half
    a gate. Matters most in /sdlc-loop, where no human adjudicates: without this, nothing stops the
    loop from faithfully implementing a review finding that was simply wrong."""
    t = (ROOT / "skills" / "sdlc-plan-review" / "SKILL.md").read_text()
    low = t.lower()
    for verdict in ("accept", "reject", "partially accept"):
        assert verdict in low, f"plan-review has no '{verdict}' disposition"
    assert "file:line" in t                              # every verdict is evidence-bound
    assert "the review can also be wrong" in low         # findings are hypotheses too
    assert "regen" in low and "half" in low              # mostly-substantive findings => regenerate
    assert "structural" in low and "patchwork" in low
