# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`insight web ic` CLI wiring (issue #310 [E19.S2] Task 2, .sdlc/plans/310.md Decision 1/2):
the Next.js `/ic` Server Component's server-to-server bridge to
`insight.dash.ic.collect_ic_payload`.

PER-TEST `pytest.importorskip("duckdb")`, never a module-level skip -- amendment SHOULD-FIX 1 of
.sdlc/plans/310.md, matching `test_dash_ic.py`/`test_dash_ic_no_leak.py`'s own exact discipline,
so a duckdb-less machine still runs every OTHER test in the suite rather than hard-failing at
collection.
"""
import json

import pytest

from insight.__main__ import build_parser, main


def test_help_lists_web_ic_subcommand(capsys):
    """Ungated -- building the parser and rendering --help never imports duckdb."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["web", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "ic" in out


def test_web_ic_requires_actor_flag():
    """Ungated -- argparse rejects the missing flag before any dispatch branch runs."""
    with pytest.raises(SystemExit) as exc:
        main(["web", "ic"])
    assert exc.value.code == 2


def test_web_ic_rejects_whitespace_only_actor_as_malformed_request(tmp_path, capsys):
    """argparse's own `required=True` only guards ABSENCE, not a present-but-blank value --
    `--actor "   "` must be rejected explicitly, never sailed through to query a blank actor."""
    pytest.importorskip("duckdb")
    code = main(["web", "ic", "--actor", "   ", "--db", str(tmp_path / "s.duckdb")])
    assert code == 1
    out, err = capsys.readouterr()
    assert json.loads(out) == {"error": "malformed_request"}
    assert err == ""


def test_web_ic_missing_store_exits_2_with_the_store_unavailable_marker(tmp_path, capsys):
    """A missing store must never degrade into "actor has zero rows" (exit 0, empty payload) --
    this codebase's ABSENT-!=-PASS doctrine (insight/api/app.py:40-47). Never a raw traceback on
    stderr masquerading as the real signal."""
    pytest.importorskip("duckdb")
    nonexistent = tmp_path / "does-not-exist.duckdb"
    assert not nonexistent.exists()
    code = main(["web", "ic", "--actor", "alice", "--db", str(nonexistent)])
    assert code == 2
    out, err = capsys.readouterr()
    assert out == '{"error": "store_unavailable"}\n'
    assert "Traceback" not in err


def test_web_ic_success_returns_alices_own_payload_with_no_cross_actor_needles(tmp_path, capsys):
    """Gated: seeds a real DuckDB store via the shared alice/bob/carol fixture
    (`insight.tests.ic_fixture.seed_alice_bob_carol`, Task 1/2's shared helper -- reused, not
    copy-pasted a third time) and drives the real CLI end to end. Asserts the stdout TEXT (not
    just the parsed JSON structure) carries none of bob's or carol's exclusive identifiers --
    mirrors `test_dash_ic_no_leak.py`'s own needle list."""
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema
    from insight.tests.ic_fixture import seed_alice_bob_carol

    db_path = tmp_path / "s.duckdb"
    conn = duckdb.connect(str(db_path))
    ensure_schema(conn)
    seed_alice_bob_carol(conn)
    conn.close()

    code = main(["web", "ic", "--actor", "alice", "--db", str(db_path)])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""

    payload = json.loads(out)
    assert payload["actor"] == "alice"
    assert [r["goal_id"] for r in payload["my_queue"]] == ["g-alice-1"]

    # the raw stdout TEXT, not merely the parsed structure -- this is the exact bridge contract
    # the Node side (`fetchIcPayload`, Task 3) reads verbatim off stdout.
    for needle in (
        "carol", "g-carol-1", "g-carol-2", "9103", "303",       # carol: fully absent
        "g-bob-1", "g-bob-2", "9102", "changes_requested", "302",  # bob: own data absent
    ):
        assert needle not in out, f"leaked via {needle!r}"
    assert "bob" in out  # the one sanctioned from_actor mention on issue 301
