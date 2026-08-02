# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""THE proving tests for issue #131's own done_when clause: (a) no individual-grain metric
appears anywhere on the leadership page -- ZERO sanctioned exceptions (Decision 5 of
.sdlc/plans/131.md, stricter than insight.dash.manager's one), and (b) the string "DXI" never
renders inside the Effectiveness tile's own markup (Decision 7). Each check ships with its own
negative control, mirroring test_dash_manager_guardrail.py's own methodology: a check that
cannot fail against a deliberately broken input is not a check."""
import datetime
import pathlib
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl  # noqa: E402
from insight.dash.leadership import (  # noqa: E402
    _impact_rows,
    _quality_row,
    _speed_row,
    render_leadership_view,
)

NOW = datetime.datetime(2026, 8, 1)
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

_SECTION_RE = re.compile(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', re.DOTALL)
_EFFECTIVENESS_TILE_RE = re.compile(r'<div id="effectiveness-tile">(.*?)</div>', re.DOTALL)


def _sections(html_text):
    return dict(_SECTION_RE.findall(html_text))


def _extract_effectiveness_tile(html_text):
    m = _EFFECTIVENESS_TILE_RE.search(html_text)
    assert m, "effectiveness-tile div not found"
    return m.group(1)


# --------------------------------------------------------------------------- (a) privacy, no exemption


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    # A real actor-bearing row DOES exist in the store (mirrors production: the ledger has
    # actor data) -- leadership.py's own fetchers never read fact_event/fact_handoff/dim_actor
    # at all, and this fixture proves that structurally, not merely by absence of code that
    # would leak.
    c.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g-carol-1', ?, 'carol', 'claimed', 1)", [NOW],
    )
    c.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'carol', 'dave', 'insight', 601, 'p1', ?)", [NOW],
    )
    load_fixture_jsonl(c, FIXTURES / "9.jsonl")  # gives panel-impact real content to leak into
    yield c
    c.close()


def _assert_no_individual_grain_leak(html_text, actor_identifiers):
    """Leadership has ZERO sanctioned exceptions (Decision 5) -- no panel is stripped before
    checking, unlike test_dash_manager_guardrail.py's own _SANCTIONED_PANEL carve-out."""
    for actor in actor_identifiers:
        if actor in html_text:
            raise AssertionError(f"individual-grain leak: {actor!r} found on the leadership page")


def test_no_actor_identifier_appears_anywhere_on_the_leadership_page(conn):
    html_text, _ = render_leadership_view(conn, now=NOW)
    _assert_no_individual_grain_leak(html_text, ["carol", "dave"])


def test_every_raw_row_leadership_reads_carries_no_person_identifying_column(conn):
    load_metrics(conn)
    speed = _speed_row(conn)
    if speed is not None:
        assert not any("actor" in k.lower() for k in speed)
    quality = _quality_row(conn)
    if quality is not None:
        assert not any("actor" in k.lower() for k in quality)
    impact_rows = _impact_rows(conn)
    assert impact_rows  # fixture regression guard
    for row in impact_rows:
        assert not any("actor" in k.lower() for k in row)


def test_negative_control_proves_the_leadership_privacy_check_has_teeth(conn):
    """Not shipped code -- proves _assert_no_individual_grain_leak is falsifiable, mirroring
    test_dash_manager_guardrail.py's own negative control shape exactly, minus the sanctioned
    panel it doesn't have."""
    html_text, _ = render_leadership_view(conn, now=NOW)
    _assert_no_individual_grain_leak(html_text, ["carol", "dave"])  # sanity: passes today

    mutated = html_text.replace(
        '<h2>Impact (DX Core-4 #9)</h2>',
        '<h2>Impact (DX Core-4 #9)</h2><span>reported by: carol</span>',
        1,
    )
    assert "carol" in _sections(mutated)["panel-impact"], \
        "fixture regressed: negative control no longer lands inside a real panel"
    with pytest.raises(AssertionError, match="individual-grain leak"):
        _assert_no_individual_grain_leak(mutated, ["carol", "dave"])


# --------------------------------------------------------------------------- (b) DXI structural separation


@pytest.fixture
def plain_conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s2.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _assert_dxi_absent_from_tile(html_text):
    tile = _extract_effectiveness_tile(html_text)
    if "DXI" in tile:
        raise AssertionError(
            "the string 'DXI' rendered inside the Effectiveness tile's own markup "
            "(<div id=\"effectiveness-tile\">) -- it may only appear in the sibling "
            "spec-mandated disclaimer paragraph"
        )


def test_dxi_never_renders_inside_the_effectiveness_tiles_own_markup(plain_conn):
    html_text, _ = render_leadership_view(plain_conn, now=NOW)
    _assert_dxi_absent_from_tile(html_text)  # the check itself
    # sanity: NOT vacuous -- "DXI" legitimately appears elsewhere (the disclaimer)
    assert "DXI" in html_text
    tile = _extract_effectiveness_tile(html_text)
    assert "DXI" not in tile


def test_negative_control_proves_the_dxi_check_has_teeth(plain_conn):
    """Not shipped code -- simulates the exact bug class this test exists to catch: a future
    edit that renders a literal "DXI: <number>" inside the tile div itself."""
    html_text, _ = render_leadership_view(plain_conn, now=NOW)
    _assert_dxi_absent_from_tile(html_text)  # sanity: passes on real output

    mutated = html_text.replace(
        '<div id="effectiveness-tile">',
        '<div id="effectiveness-tile"><span>DXI: 0.0</span>',
        1,
    )
    assert "DXI" in _extract_effectiveness_tile(mutated), \
        "fixture regressed: negative control no longer lands inside the tile div"
    with pytest.raises(AssertionError, match="Effectiveness tile"):
        _assert_dxi_absent_from_tile(mutated)
