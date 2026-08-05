# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Insight-side contract tests (issue #298, [E15.S4]) for the two remaining fixtured formats:
goal frontmatter and config.json. The dossier's own finding this closes: config.json is
reimplemented FOUR times across insight/ingest (artifact_reader._discovery_source,
artifact_reader.read_config_snapshot, gh_reader._repo_from_config,
ledger_reader._telemetry_share_is_off, goal_lifecycle._discovery_source) with no drift test
anywhere that they still agree on the same real file."""
import json
import pathlib

from insight.ingest import artifact_reader, gh_reader, goal_lifecycle, ledger_reader

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "contract"


def test_goal_frontmatter_parses_the_documented_fields():
    text = (CONTRACT / "goal_frontmatter.md").read_text(encoding="utf-8")
    fm = artifact_reader.parse_frontmatter(text)
    assert fm["id"] == "0298"
    assert fm["title"] == "Freeze the engine-product data contract, with golden fixtures"
    assert fm["lane"] == "large"
    assert fm["source"] == "github"
    assert fm["status"] == "in_progress"
    assert fm["verify_command"] == (
        "python3 -m pytest -q tests/ && python3 -m pytest -q insight/tests/ && "
        "python3 insight/verify_web.py"
    )
    assert "done_when" in fm


def test_goal_record_reads_the_fixture_through_the_real_read_path(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    (sdlc_dir / "goals").mkdir(parents=True)
    goal_path = sdlc_dir / "goals" / "0298.md"
    goal_path.write_text((CONTRACT / "goal_frontmatter.md").read_text(encoding="utf-8"),
                          encoding="utf-8")
    record = artifact_reader.goal_record(sdlc_dir, goal_path)
    assert record["goal_id"] == "0298"
    assert record["lane"] == "large"
    assert record["status"] == "in_progress"
    assert record["done_when_present"] is True


def _sdlc_with_config(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir(parents=True)
    (sdlc_dir / "config.json").write_text(
        (CONTRACT / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    return sdlc_dir


def test_all_four_independent_config_readers_agree_on_the_same_fixture(tmp_path):
    """The concrete drift test the dossier found missing: config.json reimplemented four times,
    zero test that they read the SAME real file identically. If a future edit changes one
    reader's key path (e.g. discovery.github.repo -> discovery.repo) without updating the other
    three, this fails -- today, before this goal, nothing would have noticed."""
    sdlc_dir = _sdlc_with_config(tmp_path)

    assert artifact_reader._discovery_source(sdlc_dir) == "local-goals"
    assert goal_lifecycle._discovery_source(sdlc_dir) == "local-goals"
    assert gh_reader._repo_from_config(sdlc_dir) == "swapnil-agrim/loopsmith"
    # telemetry.share is true in the fixture -> sharing is ON -> "is off" reads False.
    assert ledger_reader._telemetry_share_is_off(sdlc_dir) is False

    snapshot = artifact_reader.read_config_snapshot(sdlc_dir)
    assert snapshot is not None
    assert json.loads(snapshot) == json.loads((CONTRACT / "config.json").read_text(encoding="utf-8"))
