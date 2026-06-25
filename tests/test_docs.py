"""Lock the README's pipeline documentation: it must name all 7 phases, document both companion
plugins it depends on, and explain the dependency mechanism (auto-install + the fix path). Stable
anchors only (phase names, plugin names, documented field/error names) — not prose wording."""
import pathlib

README = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text()


def test_documents_all_seven_phases():
    for phase in ("Goal", "Research", "Plan", "Plan-Review", "Implement", "Review", "Retrospective"):
        assert phase in README, f"README omits phase: {phase}"


def test_distinguishes_shipped_plan_review_from_companions():
    assert "sdlc-plan-review" in README                      # the gate this kit ships
    assert "superpowers" in README and "code-review" in README  # the companions it relies on


def test_documents_dependency_mechanism_and_fix_path():
    assert "allowCrossMarketplaceDependenciesOn" in README   # the allowlist that makes it resolve
    assert "claude-plugins-official" in README                # the marketplace the companions live in
    assert "dependency-unsatisfied" in README                 # the failure mode + documented fix path
