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


def test_documents_both_backlog_sources():
    assert "discovery" in README                      # the config knob that selects the source
    assert ".sdlc/goals/" in README                   # the local files source
    assert "GitHub issues" in README and "sdlc:goal" in README   # the github source + its label scheme
    assert "github-project" in README and "QC" in README and "Blocked" in README   # the Projects board source


def test_documents_optional_knowledge_graph():
    assert "knowledge_graph" in README                       # the config toggle
    assert "graphify" in README                              # the default builder
    assert "off by default" in README or "opt-in" in README  # not on without consent
    assert ".sdlc/knowledge/" in README                      # the corpus (research + analysis)


def test_documents_dependency_mechanism_and_fix_path():
    assert "allowCrossMarketplaceDependenciesOn" in README   # the allowlist that makes it resolve
    assert "claude-plugins-official" in README                # the marketplace the companions live in
    assert "dependency-unsatisfied" in README                 # the failure mode + documented fix path
