# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""THE proving test for issue #133's own zero-exception privacy posture (Decision 2 of
.sdlc/plans/133.md, matching insight.dash.leadership's own stricter-than-manager posture): no
individual-grain metric appears anywhere on the cross-functional page. Ships with its own
negative control, mirroring test_dash_leadership_guardrail.py's own methodology exactly: a check
that cannot fail against a deliberately broken input is not a check."""
import datetime
import pathlib
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl  # noqa: E402

NOW = datetime.datetime(2026, 8, 2)
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

_SECTION_RE = re.compile(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', re.DOTALL)


def _sections(html_text):
    return dict(_SECTION_RE.findall(html_text))


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    # A real actor-bearing row DOES exist in the store (mirrors production: the ledger has
    # actor data) -- cross_functional.py's own fetchers never read fact_event/fact_handoff/
    # dim_actor at all, and this fixture proves that structurally, not merely by absence of code
    # that would leak.
    c.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g-carol-1', ?, 'carol', 'claimed', 1)", [NOW],
    )
    c.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'carol', 'dave', 'insight', 601, 'p1', ?)", [NOW],
    )
    load_fixture_jsonl(c, FIXTURES / "24.jsonl")  # gives the matrix real content to leak into
    yield c
    c.close()


def _assert_no_individual_grain_leak(html_text, actor_identifiers):
    """Cross-functional has ZERO sanctioned exceptions (Decision 2) -- no panel is stripped
    before checking, matching test_dash_leadership_guardrail.py's own posture."""
    for actor in actor_identifiers:
        if actor in html_text:
            raise AssertionError(f"individual-grain leak: {actor!r} found on the cross-functional page")


def test_no_actor_identifier_appears_anywhere_on_the_cross_functional_page(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    html_text, _ = render_cross_functional_view(conn, now=NOW)
    _assert_no_individual_grain_leak(html_text, ["carol", "dave"])


def test_gate_matrix_rows_carry_no_person_identifying_column(conn):
    from insight.dash.cross_functional import _gate_matrix_rows

    load_metrics(conn)  # [R2] materialize metric_24 -- without this the fetcher raises
                        # CatalogException, not an assertion. Plan review hit this by running it.
    rows = _gate_matrix_rows(conn)
    assert rows
    for row in rows:
        assert not any("actor" in k.lower() for k in row)


def test_negative_control_proves_the_cross_functional_privacy_check_has_teeth(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    html_text, _ = render_cross_functional_view(conn, now=NOW)
    _assert_no_individual_grain_leak(html_text, ["carol", "dave"])  # sanity: passes today

    mutated = html_text.replace(
        '<h2>Gate coverage matrix (proxy, #24)</h2>',
        '<h2>Gate coverage matrix (proxy, #24)</h2><span>reported by: carol</span>',
        1,
    )
    assert "carol" in _sections(mutated)["panel-gate-matrix"], \
        "fixture regressed: negative control no longer lands inside a real panel"
    with pytest.raises(AssertionError, match="individual-grain leak"):
        _assert_no_individual_grain_leak(mutated, ["carol", "dave"])
