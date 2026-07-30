"""The conditional-risk review skills (Slice 2): sdlc-security-review / -contract-check /
-migration-check / -release-check / -debug. These are LoopSmith's OWN skills (no platform companion) —
orthogonal to sdlc-review's code-quality pass, invoked only when a change trips the matching risk. This
guards their port: each ships a valid SKILL.md, and none persists its artifact into the gitignored
`.sdlc/knowledge/` tree (Slice 1), which would make a review invisible to its PR."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RISK_SKILLS = ("sdlc-security-review", "sdlc-contract-check", "sdlc-migration-check",
               "sdlc-release-check", "sdlc-debug")


def test_risk_skills_present_with_valid_frontmatter():
    for skill in RISK_SKILLS:
        p = ROOT / "skills" / skill / "SKILL.md"
        assert p.exists(), "%s: missing SKILL.md" % skill
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "%s: no frontmatter" % skill
        assert "name: %s\n" % skill in text, "%s: name does not match dir" % skill
        assert "description:" in text and "allowed-tools:" in text, "%s: missing frontmatter keys" % skill


def test_risk_skills_persist_to_reviews_not_the_gitignored_knowledge_dir():
    for skill in RISK_SKILLS:
        text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert ".sdlc/reviews/" in text, "%s: should persist artifacts to .sdlc/reviews/" % skill
        # the only allowed mention of the gitignored dir is the explicit "NOT under" guidance
        for line in text.splitlines():
            if ".sdlc/knowledge/" in line:
                assert "NOT under" in line and "gitignored" in line, "%s: writes under knowledge/" % skill
