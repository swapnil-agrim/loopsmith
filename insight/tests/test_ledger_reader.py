# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.ledger_reader (issue #101, E1.S3): the union of
ledger/entries/*.jsonl and ledger/events/*.jsonl, oldest-first, plus .sdlc/events/*.jsonl when
telemetry.share is off.

No pytest.importorskip("duckdb") anywhere in this file -- ledger_reader.py never imports duckdb
(same convention as insight/tests/test_collectors.py, not test_store.py)."""
import json
import os
import pathlib

import pytest

from insight.ingest import ledger_reader

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- malformed/contract-violating lines

_LINE_FIXTURES = [
    ("invalid_json",       "not json at all",                                                  False),
    ("blank_line",         "",                                                                  False),
    ("json_list",          "[1, 2, 3]",                                                         False),
    ("json_string",        '"just a string"',                                                  False),
    ("json_null",          "null",                                                              False),
    ("dict_no_kind",       '{"id":"a:1","ts":"t","actor":"a"}',                                 False),
    ("dict_falsy_kind",    '{"id":"a:1","ts":"t","actor":"a","kind":""}',                       False),
    ("dict_numeric_kind",  '{"id":"a:1","ts":"t","actor":"a","kind":5}',                        True),
    ("missing_ts",         '{"id":"a:1","actor":"a","kind":"claimed"}',                          True),
    ("missing_actor",      '{"id":"a:1","ts":"t","kind":"claimed"}',                             True),
    ("missing_id",         '{"ts":"t","actor":"a","kind":"claimed"}',                             True),
    ("id_no_colon",        '{"id":"nocolon","ts":"t","actor":"a","kind":"claimed"}',              True),
    ("id_non_digit_tail",  '{"id":"a:xyz","ts":"t","actor":"a","kind":"claimed"}',                True),
    # Big/deep input must not blow up. This row does NOT pin the RecursionError guard — whether
    # this depth raises is interpreter-dependent (it does on 3.9, it does not under 3.12's C
    # parser, where the isinstance(dict) check drops it instead). That guard is pinned
    # deterministically by test_recursion_error_from_json_is_caught.
    ("deeply_nested",      "[" * 1200 + "]" * 1200,                                            False),
    ("id_is_a_dict",       '{"id":{"x":1},"ts":"t","actor":"a","kind":"claimed"}',               True),
    ("ts_is_a_dict",       '{"id":"a:1","ts":{"x":1},"actor":"a","kind":"claimed"}',             True),
    # int() refuses >4300 digits since 3.10.7/3.11 (CVE-2020-10735), so isdigit() alone does not
    # make _seq safe — this used to take out the entire sort, every record, every file.
    ("id_tail_5000_digits", '{"id":"a:' + "9" * 5000 + '","ts":"t","actor":"a","kind":"claimed"}', True),
]


@pytest.mark.parametrize("label, line_text, kept", _LINE_FIXTURES, ids=[f[0] for f in _LINE_FIXTURES])
def test_malformed_and_contract_violating_lines_never_raise(tmp_path, label, line_text, kept):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "a.jsonl").write_text(line_text + "\n" + good + "\n", encoding="utf-8")
    records = ledger_reader.read_all(tmp_path)  # must not raise, for every row
    assert any(r.get("id") == "z:1" for r in records), "the valid trailing record must survive"
    assert len(records) == (2 if kept else 1), f"{label}: expected kept={kept}"


def test_recursion_error_from_json_is_caught(tmp_path, monkeypatch):
    # The `deeply_nested` fixture row canNOT pin this: whether a given nesting depth raises
    # RecursionError depends on the interpreter — 1200 raises on 3.9's pure-Python path but
    # parses fine under 3.12's C parser, so on CI that row is filtered by the isinstance(dict)
    # check instead and would still pass with RecursionError removed from the except clause.
    # A reviewer proved that by mutation. Raise it directly so the guard is pinned everywhere.
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "a.jsonl").write_text('{"deep":1}\n' + good + "\n", encoding="utf-8")

    real = json.loads

    def loads(text, *a, **kw):
        if "deep" in text:
            raise RecursionError("maximum recursion depth exceeded")
        return real(text, *a, **kw)

    monkeypatch.setattr(ledger_reader.json, "loads", loads)
    records = ledger_reader.read_all(tmp_path)
    assert [r["id"] for r in records] == ["z:1"]


def test_seq_survives_a_tail_isdigit_accepts_but_int_refuses():
    # Pins _seq's OWN guard, independent of _sort_key's catch-all — which otherwise absorbs this
    # and lets the 5000-digit fixture row pass even with _seq unguarded (proved by mutation).
    # Two unrelated inputs reach this same except: an over-long digit run (int()'s CVE-2020-10735
    # limit, which any caller can lower with sys.set_int_max_str_digits — hence catching rather
    # than comparing against a constant), and a superscript, which isdigit() calls a digit and
    # int() refuses. The superscript is deterministic on every interpreter, so it is the one
    # asserted here; the digit-limit case is interpreter- and settings-dependent.
    assert "²".isdigit() and ledger_reader._seq({"id": "a:²"}) == 0


def test_an_unsortable_record_sorts_first_instead_of_killing_the_read(tmp_path, monkeypatch):
    # Pins the guarantee, not the one case: whatever future value makes a sort key raise, the
    # read still returns every record. Three separate crashes here were each an unenumerated
    # sort-key value, so this asserts the structural property rather than a fourth enumeration.
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:1","ts":"2026-01-01T00:00:00Z","actor":"a","kind":"claimed"}\n'
        '{"id":"a:2","ts":"boom","actor":"a","kind":"claimed"}\n', encoding="utf-8")

    real = ledger_reader._sort_str

    def explode(value):
        if value == "boom":
            raise RuntimeError("a value nobody enumerated")
        return real(value)

    monkeypatch.setattr(ledger_reader, "_sort_str", explode)
    records = ledger_reader.read_all(tmp_path)
    assert len(records) == 2
    assert records[0]["id"] == "a:2"  # unsortable sorts first, and is not dropped
    assert records[0]["ts"] == "boom"  # and the raw record is not mutated by the guard


def test_bom_on_first_line_does_not_eat_it(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    first = '{"id":"a:1","ts":"2026-01-01T00:00:00Z","actor":"a","kind":"claimed"}'
    second = '{"id":"a:2","ts":"2026-01-02T00:00:00Z","actor":"a","kind":"claimed"}'
    (entries / "a.jsonl").write_bytes(
        b"\xef\xbb\xbf" + (first + "\n" + second + "\n").encode("utf-8")
    )
    records = ledger_reader.read_all(tmp_path)
    assert {r["id"] for r in records} == {"a:1", "a:2"}


def test_invalid_utf8_bytes_do_not_crash_the_whole_read(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "a.jsonl").write_bytes(b"\xff\xfe not valid utf-8\n" + good.encode("utf-8") + b"\n")
    records = ledger_reader.read_all(tmp_path)
    assert any(r.get("id") == "z:1" for r in records)


def test_truncated_final_line_with_no_trailing_newline_still_parses(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "a.jsonl").write_text(good, encoding="utf-8")  # no trailing \n
    records = ledger_reader.read_all(tmp_path)
    assert any(r.get("id") == "z:1" for r in records)


def test_a_file_that_is_a_directory_is_skipped_not_fatal(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "oops.jsonl").mkdir()
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "a.jsonl").write_text(good + "\n", encoding="utf-8")
    records = ledger_reader.read_all(tmp_path)
    assert any(r.get("id") == "z:1" for r in records)


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 0o000 has no effect for root, and Windows has no equivalent permission bits",
)
def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    unreadable = entries / "a.jsonl"
    unreadable.write_text('{"id":"a:1","ts":"t","actor":"a","kind":"claimed"}\n', encoding="utf-8")
    unreadable.chmod(0o000)
    good = '{"id":"z:1","ts":"2026-01-01T00:00:00Z","actor":"z","kind":"claimed"}'
    (entries / "b.jsonl").write_text(good + "\n", encoding="utf-8")
    try:
        records = ledger_reader.read_all(tmp_path)
    finally:
        unreadable.chmod(0o644)
    assert any(r.get("id") == "z:1" for r in records)


def test_numeric_truthy_kind_is_kept_not_dropped(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:1","ts":"t","actor":"a","kind":5}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    assert any(r.get("id") == "a:1" and r.get("kind") == 5 for r in records)


def test_sort_tolerates_mixed_type_ts_without_raising(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:1","ts":"2026-01-01T00:00:00Z","actor":"a","kind":"claimed"}\n'
        '{"id":"a:2","ts":20260101,"actor":"a","kind":"claimed"}\n',
        encoding="utf-8",
    )
    records = ledger_reader.read_all(tmp_path)  # must not raise
    assert {r["id"] for r in records} == {"a:1", "a:2"}


def test_seq_parsed_from_id_tail_breaks_ties_numerically_not_lexicographically(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:10","ts":"t","actor":"a","kind":"claimed"}\n'
        '{"id":"a:2","ts":"t","actor":"a","kind":"claimed"}\n',
        encoding="utf-8",
    )
    records = ledger_reader.read_all(tmp_path)
    assert [r["id"] for r in records] == ["a:2", "a:10"]


def test_reads_union_of_entries_and_ledger_events_oldest_first(tmp_path):
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    events = tmp_path / "ledger" / "events"
    events.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:1","ts":"2026-01-01T00:00:00Z","actor":"a","kind":"claimed"}\n', encoding="utf-8"
    )
    (events / "b.jsonl").write_text(
        '{"id":"b:1","ts":"2026-01-02T00:00:00Z","actor":"b","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    assert [r["id"] for r in records] == ["a:1", "b:1"]


def test_local_events_excluded_by_default_share_true(tmp_path):
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    assert records == []


def test_local_events_excluded_when_config_has_no_telemetry_key(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    assert records == []


def test_local_events_excluded_when_share_is_explicitly_true(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"telemetry": {"share": True}}), encoding="utf-8"
    )
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    assert records == []


def test_local_events_included_when_share_is_explicitly_false(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"telemetry": {"share": False}}), encoding="utf-8"
    )
    entries = tmp_path / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "a.jsonl").write_text(
        '{"id":"a:1","ts":"t","actor":"a","kind":"claimed"}\n', encoding="utf-8"
    )
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)
    ids = {r["id"] for r in records}
    assert ids == {"a:1", "x:1"}, "local events must be ADDITIVE to the ledger globs, not a fallback"


def test_malformed_config_json_defaults_to_share_true_without_crashing(tmp_path):
    (tmp_path / "config.json").write_text('"{not valid json', encoding="utf-8")
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)  # must not raise
    assert records == []


def test_config_present_but_telemetry_value_is_wrong_type_defaults_share_true(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"telemetry": "oops"}), encoding="utf-8")
    local_events = tmp_path / "events"
    local_events.mkdir(parents=True)
    (local_events / "x.jsonl").write_text(
        '{"id":"x:1","ts":"t","actor":"x","kind":"phase"}\n', encoding="utf-8"
    )
    records = ledger_reader.read_all(tmp_path)  # must not raise
    assert records == []


def test_real_ledger_worktree_if_present():
    sdlc_dir = REPO_ROOT / ".sdlc"
    if not (sdlc_dir / "ledger").exists():
        pytest.skip(".sdlc/ledger does not exist in this checkout")
    records = ledger_reader.read_all(sdlc_dir)  # must not raise
    assert isinstance(records, list)
