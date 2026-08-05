# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Insight-side half of the data contract (issue #298, [E15.S4]): proves insight/ingest still
READS what insight/contract/'s golden fixtures declare -- the counterpart to
tests/test_ledger_contract.py's engine-side "still WRITES" proof. Independent of that file: this
one imports insight.ingest.ledger_reader, never skills/, and the engine-side file imports
skills/sdlc-loop/scripts/ledger.py, never insight/ -- clause 3's own zero-cross-import
requirement, both directions."""
import json
import pathlib

import insight.contract
from insight.ingest import ledger_reader

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "contract"
VOCAB = json.loads((CONTRACT / "vocabulary.json").read_text(encoding="utf-8"))


def _sdlc_with_fixtures(tmp_path):
    entries_dir = tmp_path / ".sdlc" / "ledger" / "entries"
    events_dir = tmp_path / ".sdlc" / "ledger" / "events"
    entries_dir.mkdir(parents=True)
    events_dir.mkdir(parents=True)
    (entries_dir / "dana.jsonl").write_text(
        (CONTRACT / "ledger_entries.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    (events_dir / "agent.jsonl").write_text(
        (CONTRACT / "telemetry_events.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path / ".sdlc"


def test_every_fixture_line_parses_and_kinds_match_the_contract_vocabulary(tmp_path):
    sdlc_dir = _sdlc_with_fixtures(tmp_path)
    records = ledger_reader.read_all(sdlc_dir)
    entries_fixture_lines = len(
        (CONTRACT / "ledger_entries.jsonl").read_text(encoding="utf-8").strip().splitlines())
    events_fixture_lines = len(
        (CONTRACT / "telemetry_events.jsonl").read_text(encoding="utf-8").strip().splitlines())
    assert len(records) == entries_fixture_lines + events_fixture_lines

    known_kinds = set(VOCAB["entries_kinds"]) | set(VOCAB["event_kinds"])
    for record in records:
        assert record["kind"] in known_kinds, (
            f"fixture record {record!r} has a kind outside insight/contract/vocabulary.json's "
            f"declared entries_kinds/event_kinds"
        )
    seen_kinds = {r["kind"] for r in records}
    assert seen_kinds == known_kinds, (
        "every vocabulary member should appear at least once across the two golden fixtures -- "
        f"missing: {known_kinds - seen_kinds}"
    )


def test_an_unknown_field_from_the_fixture_survives_intact_clause_4(tmp_path):
    """The insight-side half of clause 4, independent of tests/test_ledger_contract.py's
    engine-side proof: ledger_entries.jsonl's own 10th line carries
    "future_experimental_field", simulating a newer engine writer's field an older insight/
    reader has never heard of. It must ride along unused, not get dropped or raise."""
    sdlc_dir = _sdlc_with_fixtures(tmp_path)
    records = ledger_reader.read_all(sdlc_dir)
    future_field_records = [r for r in records if "future_experimental_field" in r]
    assert len(future_field_records) == 1
    assert future_field_records[0]["future_experimental_field"] == \
        "an older reader must ignore this, not fail"
    assert future_field_records[0]["kind"] == "note"


def test_read_all_with_reliability_tags_both_streams_correctly(tmp_path):
    sdlc_dir = _sdlc_with_fixtures(tmp_path)
    tagged = ledger_reader.read_all_with_reliability(sdlc_dir)
    entries_kinds = set(VOCAB["entries_kinds"])
    event_kinds = set(VOCAB["event_kinds"])
    for record in tagged:
        if record["kind"] in entries_kinds:
            assert record["reliability_class"] == 1
        else:
            assert record["kind"] in event_kinds
            assert record["reliability_class"] == 2


def test_contract_version_matches_the_fixtures_own_self_reported_version():
    """Decision 2's only real teeth: vocabulary.json's own "contract_version" key and the
    package's CONTRACT_VERSION constant cannot quietly diverge from each other."""
    assert VOCAB["contract_version"] == insight.contract.CONTRACT_VERSION
