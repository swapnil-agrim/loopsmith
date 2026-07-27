"""sdlc-research: the Research phase executor. Pins the things that make it worth having over the
prose line it replaced — a re-runnable blast-radius query, a lane sized from measured footprint, and
an artifact that does NOT sit where the plan gate will mistake it for a plan — plus its wiring into
both orchestrators and no leakage from the repo it was genericized from."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "skills" / "sdlc-research" / "SKILL.md"


def _t():
    return RESEARCH.read_text()


def test_skill_exists_with_frontmatter():
    assert RESEARCH.exists()
    t = _t()
    assert "name: sdlc-research" in t
    assert "description:" in t and "allowed-tools:" in t


def test_blast_radius_query_is_stored_for_rescan():
    """The coverage guarantee is re-running the query later, not trusting today's list. Without the
    stored query there is no re-scan, and a site that lands after research is caught by nothing."""
    t = _t()
    assert "re-run" in t.lower()
    assert "verbatim" in t.lower() or "exact command" in t.lower()


def test_dispositions_cover_the_unsure_case():
    t = _t().lower()
    for d in ("in-scope", "deferred", "out-of-scope", "verify"):
        assert d in t, f"blast-radius row has no '{d}' disposition"


def test_lane_is_sized_from_footprint_not_calendar_time():
    t = _t()
    for lane in ("small", "medium", "large"):
        assert f"**{lane}**" in t
    assert "lane: auto" in t                                   # closes sdlc-init's dangling promise
    assert "structural footprint" in t.lower()
    assert "fabricate" in t.lower() or "invented duration" in t.lower()


def test_dossier_stays_out_of_the_plan_gate_directory():
    """plan_gate.sh unblocks source edits on ANY recent .md under .sdlc/plans/ — a research note
    landing there would silently satisfy the gate it is supposed to precede."""
    t = _t()
    assert ".sdlc/research/" in t
    assert ".sdlc/plans/" in t and "not a plan" in t.lower()


def test_defers_the_binding_alignment_verdict_to_plan_review():
    """Research warns early because it is cheap there; the gate that blocks is still plan-review's,
    and duplicating the verdict would give two authorities for one decision."""
    t = _t()
    assert "sdlc-plan-review" in t
    assert "early warning" in t.lower()


def test_proportion_gate_allows_skipping():
    """Ceremony for a typo fix is the failure mode a Research phase invites. Skipping must be a
    stated, legitimate outcome — not something the agent has to justify."""
    t = _t().lower()
    assert "skipping it is a legitimate outcome" in t or "does **not**" in _t()


def test_read_only_and_evidence_bound():
    t = _t()
    assert "Read-only" in t or "read-only" in t
    assert "no fabrication" in t.lower()
    assert "file:line" in t


def test_wired_into_both_orchestrators_research_phase():
    for orch in ("sdlc-goal", "sdlc-loop"):
        t = (ROOT / "skills" / orch / "SKILL.md").read_text()
        assert "sdlc-research" in t, f"{orch} does not run sdlc-research"


def test_readme_no_longer_calls_research_skill_less():
    t = (ROOT / "README.md").read_text()
    assert "agent practice; no dedicated skill" not in t
    assert "`sdlc-research`" in t


def test_no_source_repo_leakage():
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch", "Temporal", "RunPod")
    t = _t()
    for b in banned:
        assert b not in t, f"sdlc-research leaked '{b}'"
