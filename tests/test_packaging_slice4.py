import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_plan_review_skill_wellformed():
    t = (ROOT / "skills" / "sdlc-plan-review" / "SKILL.md").read_text()
    assert "name: sdlc-plan-review" in t
    # generic — no OnShot leakage
    for banned in ("media-orch", "Temporal", "R2", "OnShot", "RunPod"):
        assert banned not in t


def test_goal_skill_wellformed_and_records():
    t = (ROOT / "skills" / "sdlc-goal" / "SKILL.md").read_text()
    assert "name: sdlc-goal" in t and "allowed-tools:" in t
    assert "loop.py" in t and "record" in t        # records the outcome to .sdlc state


def test_versions_aligned():
    p = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert p["version"] == "0.4.0" and mk["plugins"][0]["version"] == "0.4.0"
