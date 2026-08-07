# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`insight web delivery` CLI wiring (issue #312 [E20.S1] Goal B, Task B2,
.sdlc/plans/312.md §3a/§7): the /delivery Server Component's server-to-server bridge to
`insight.api.metrics.collect_metrics()` -- the SAME resolver GET /metrics already calls
(insight/api/app.py:74), not a second, independent absence-conventions implementation.

PER-TEST `pytest.importorskip("duckdb")`, never a module-level skip -- mirrors
test_cli_web_ic.py's own discipline (itself matching .sdlc/plans/310.md amendment SHOULD-FIX 1),
so a duckdb-less machine still runs every OTHER test in the suite rather than hard-failing at
collection.

Deliberately NOT identical to test_cli_web_ic.py in two ways, both load-bearing:
  1. No `--actor` flag exists at all on this subcommand -- there is nothing to test for
     "rejects a missing/blank actor" the way test_web_ic_requires_actor_flag /
     test_web_ic_rejects_whitespace_only_actor_as_malformed_request do.
  2. A missing store is NOT exit 2 -- it is exit 0, all-42-absent, mirroring
     test_api_metrics_route.py::test_missing_store_file_also_returns_all_absent exactly (same
     resolver, same missing-store degrade).
"""
import json

import pytest

from insight.__main__ import build_parser, main


def test_help_lists_web_delivery_subcommand(capsys):
    """Ungated -- building the parser and rendering --help never imports duckdb."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["web", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "delivery" in out


def test_web_delivery_takes_no_actor_flag():
    """Structural guarantee (issue #312 §3a point 3): there is no `--actor` flag for a leak to
    ride on. An attempt to pass one must fail argparse's own "unrecognized arguments" check --
    proves the flag genuinely does not exist, not merely that nothing reads it."""
    with pytest.raises(SystemExit) as exc:
        main(["web", "delivery", "--actor", "someone"])
    assert exc.value.code == 2


def test_web_delivery_missing_store_exits_0_with_all_42_absent(tmp_path, capsys):
    """The deliberate divergence from `web ic`'s exit-2 store_unavailable convention (issue #312
    §3a point 2): a missing store degrades to collect_metrics(None) -- all 42 catalog entries,
    each individually absent, never an error code and never a fabricated value. Mirrors
    test_api_metrics_route.py::test_missing_store_file_also_returns_all_absent, driven through
    the real CLI end to end instead of TestClient."""
    pytest.importorskip("duckdb")
    nonexistent = tmp_path / "does-not-exist.duckdb"
    assert not nonexistent.exists()
    code = main(["web", "delivery", "--db", str(nonexistent)])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    body = json.loads(out)
    assert len(body) == 42
    assert all(m["state"] in ("absent_no_data", "absent_unbuilt") for m in body)
    assert all("value" not in m for m in body)
    assert all("coverage" not in m for m in body)


def test_web_delivery_populated_store_serialises_autonomy_rate_as_measured(tmp_path, capsys):
    """Same fixture shape test_api_metrics_route.py::
    test_populated_store_serialises_autonomy_rate_as_measured_via_the_real_endpoint already uses
    (metric_12, the one catalog id with a registered VALUE_EXTRACTOR) -- driven through the real
    CLI end to end instead of TestClient, proving the bridge's own JSON serialisation
    (`model_dump(by_alias=True)`) round-trips a measured metric's coverage denominator correctly."""
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    db_path = tmp_path / "s.duckdb"
    conn = duckdb.connect(str(db_path))
    ensure_schema(conn)
    conn.execute(
        "CREATE VIEW metric_12 AS "
        "SELECT 3 AS autonomous_done_count, 4 AS terminal_count, 0.75 AS autonomy_rate"
    )
    conn.close()

    code = main(["web", "delivery", "--db", str(db_path)])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    body = {m["id"]: m for m in json.loads(out)}
    assert len(body) == 42

    assert body[12]["state"] == "measured"
    assert body[12]["value"] == 0.75
    assert body[12]["coverage"] == {"numerator": 3, "denominator": 4}
    # Belt-and-suspenders (mirrors test_api_metrics_route.py's own reliability_class check): the
    # wire key is camelCase, never the Python-side snake_case name -- value itself is read from
    # 12.sql's own header (currently reliability_class:1), not asserted here as a fixed literal
    # this test would otherwise drift from the moment that header changes.
    assert "reliability_class" not in body[12]
    assert isinstance(body[12]["reliabilityClass"], int)

    # Everything else stays absent -- only id 12 has a registered extractor (issue #312's own
    # non-negotiable: ~41/42 cells absent is correct, not a bug).
    non_measured = [m for mid, m in body.items() if mid != 12]
    assert all(m["state"] in ("absent_no_data", "absent_unbuilt") for m in non_measured)
