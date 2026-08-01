# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The proving test for issue #127's own Task 3 (the story's own done_when clause) and Task 7:
"no individual-grain metric appears except aging WIP and handoff response" (spec Section 6's own
Guardrails). This is THE acceptance criterion for the manager view, not an incidental regression
test -- see .sdlc/plans/127.md Decision 3.

A flat `"dana" not in html_text` check (#126's own IC-view methodology) CANNOT work here: the
aging-WIP panel is SUPPOSED to contain a real actor id -- it is the one sanctioned exception. Any
check that doesn't first exempt that one panel is either vacuously permissive (skip the check
entirely) or a guaranteed false failure on real data. This file's methodology is PANEL-SCOPED,
mirroring test_dash_ic_no_leak.py's own shape:

1. STRUCTURAL: every panel is wrapped in its own `<section id="panel-...">`. A regex extracts
   each panel's own substring from the full rendered page.
2. THE CHECK: strip the ONE sanctioned panel (`panel-wip-aging`) out of the full HTML string,
   then assert no known real actor identifier appears in what's left. Run against the WHOLE
   rendered string, not just the parsed JSON payload -- the inlined
   `<script type="application/json">` block is readable via View Source regardless of whether any
   JS ever parses it (same reasoning #126's own no-leak test gives).
3. DEFENCE IN DEPTH, independent of rendering: the RAW rows returned by every non-sanctioned
   fetcher carry no person-identifying column at all.
4. THE JSON PAYLOAD, separately: the payload sits OUTSIDE every `<section>` (the plan-review nit
   folded into manager.py's own module docstring as a named contract) -- checked on its own,
   distinct from the section-stripping check, since section-scoping cannot protect a script tag
   that lives outside every section.
5. NEGATIVE CONTROL, not shipped code: deliberately splices an actor-identifying string into the
   rendered `panel-handoff-graph` substring (a non-sanctioned panel) and proves the SAME check
   used by the positive tests now fails with AssertionError -- the methodology is falsifiable,
   not a tautology that would pass against any output.
"""
import datetime
import json
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.dash.charts import _aging_wip_rows, _handoff_by_area_rows  # noqa: E402
from insight.dash.manager import (  # noqa: E402
    _park_rate_row,
    _wip_row,
    render_manager_view,
)

NOW = datetime.datetime(2026, 8, 1)

#: The one sanctioned individual-grain panel (spec's exception #10, aging WIP). Every other
#: panel id in this file's fixture must carry NO real actor identifier.
_SANCTIONED_PANEL = "panel-wip-aging"

_SECTION_RE = re.compile(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', re.DOTALL)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)

    # dana has one open claim -- the ONE legitimate individual-grain fact this page ever shows
    # (aging WIP). erin is dana's hand-off counterparty -- a real actor name that must NEVER
    # appear anywhere on this page, because the handoff graph is area-grain only (Decision 1):
    # unlike ic.py's "blocked on me" exception, the manager view's handoff graph has no sanctioned
    # per-row counterparty exception at all.
    c.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g-dana-1', ?, 'dana', 'claimed', 1)",
        [NOW - datetime.timedelta(days=5)],
    )
    c.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'dana', 'erin', 'insight', 501, 'p1', ?)",
        [NOW],
    )
    yield c
    c.close()


def _sections(html_text):
    return dict(_SECTION_RE.findall(html_text))


def _assert_no_individual_grain_leak(html_text, actor_identifiers, sanctioned_panel=_SANCTIONED_PANEL):
    """THE shared check every positive test AND the negative control below both call -- so the
    positive and negative tests can never drift into checking different things (mirrors #126's
    own `_assert_carol_absent` pattern). Strips `sanctioned_panel`'s own substring out of the full
    rendered string, then asserts none of `actor_identifiers` appears in what remains."""
    sections = _sections(html_text)
    remainder = html_text
    if sanctioned_panel in sections:
        # Remove the panel's FULL <section ...>...</section> markup, not just the inner text --
        # mirrors how a reader would genuinely exclude "the one sanctioned panel" from the page.
        m = re.search(
            rf'<section id="{re.escape(sanctioned_panel)}"[^>]*>.*?</section>', html_text, re.DOTALL,
        )
        remainder = html_text[: m.start()] + html_text[m.end():]
    for actor in actor_identifiers:
        if actor in remainder:
            raise AssertionError(
                f"individual-grain leak: {actor!r} found outside the sanctioned panel "
                f"{sanctioned_panel!r}"
            )


# --------------------------------------------------------------------------- 1/2: structural + panel-scoped check

def test_actor_appears_only_inside_the_sanctioned_aging_wip_panel(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    sections = _sections(html_text)
    assert _SANCTIONED_PANEL in sections

    # the positive check: nothing survives outside the sanctioned panel
    _assert_no_individual_grain_leak(html_text, ["dana", "erin"])

    # NOT vacuously empty -- dana genuinely appears inside the one sanctioned panel
    assert "dana" in sections[_SANCTIONED_PANEL]


def test_handoff_graph_panel_carries_only_area_not_actor(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-handoff-graph"]
    assert "insight" in panel  # the area name -- the ONLY grain this panel is allowed to show
    assert "dana" not in panel
    assert "erin" not in panel


def test_burndown_and_park_taxonomy_and_review_cycles_panels_carry_no_actor(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    sections = _sections(html_text)
    for panel_id in ("panel-burndown", "panel-park-taxonomy", "panel-review-cycles"):
        assert "dana" not in sections[panel_id]
        assert "erin" not in sections[panel_id]


# --------------------------------------------------------------------------- 3: defence in depth on raw rows

def test_every_non_sanctioned_raw_row_carries_no_person_identifying_column(conn):
    load_metrics(conn)
    handoff_rows = _handoff_by_area_rows(conn)
    assert handoff_rows  # fixture regression guard -- the check below is vacuous if this is empty
    for row in handoff_rows:
        assert set(row) == {"area", "handoff_count"}

    wip_row = _wip_row(conn)
    if wip_row is not None:
        assert set(wip_row) == {"week_start", "wip_count"}

    park_row = _park_rate_row(conn)
    if park_row is not None:
        assert not any("actor" in key for key in park_row)


def test_aging_wip_rows_the_sanctioned_fetcher_is_the_only_one_that_legitimately_carries_actor_id(conn):
    """Not a leak check -- the opposite assertion, stated explicitly so nobody mistakes an actor
    column here for a regression: `_aging_wip_rows` (metric_10, reused unmodified from #125) is
    the ONE fetcher this page calls that is SUPPOSED to carry `actor_id`."""
    load_metrics(conn)
    rows = _aging_wip_rows(conn)
    assert rows
    assert all("actor_id" in row for row in rows)


# --------------------------------------------------------------------------- 4: the inlined JSON payload

def _extract_payload(html_text):
    m = re.search(
        r'<script type="application/json" id="insight-manager-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m, "inlined manager data script not found"
    return m.group(1), json.loads(m.group(1))


def test_the_inlined_json_payload_sits_outside_every_section(conn):
    """The plan-review nit's own premise, checked directly: the data script tag is NOT inside any
    `<section>...</section>` span, so section-scoping cannot protect whatever it carries."""
    html_text, _ = render_manager_view(conn, now=NOW)
    raw_payload, _ = _extract_payload(html_text)
    for _panel_id, panel_html in _sections(html_text).items():
        assert raw_payload not in panel_html


def test_the_inlined_json_payload_carries_no_actor_identifier(conn):
    """Separate from the section-stripping check above (Decision 3, point 2) -- the named
    contract in manager.py's own module docstring: the payload carries no key derived from
    `_aging_wip_rows` (which carries `actor_id`), only count-only shapes. Checked on the RAW
    script text (not just the parsed dict), matching #126's own "View Source, not just what JS
    would parse" posture."""
    html_text, _ = render_manager_view(conn, now=NOW)
    raw_payload, payload = _extract_payload(html_text)
    assert "dana" not in raw_payload
    assert "erin" not in raw_payload
    # the named, count-only contract itself
    assert "aging_wip_count" in payload
    assert isinstance(payload["aging_wip_count"], int)
    assert "aging_wip_rows" not in payload
    assert "aging_rows" not in payload

    def _walk_no_actor_id_key(node):
        if isinstance(node, dict):
            assert "actor_id" not in node, "actor_id key found in the inlined JSON payload"
            assert not any("actor" in str(k).lower() for k in node), \
                f"an actor-shaped key leaked into the payload: {list(node)}"
            for v in node.values():
                _walk_no_actor_id_key(v)
        elif isinstance(node, list):
            for item in node:
                _walk_no_actor_id_key(item)

    _walk_no_actor_id_key(payload)


# --------------------------------------------------------------------------- 5: negative control

def test_negative_control_proves_the_panel_scoped_check_has_teeth(conn):
    """Not shipped code -- exists solely to prove `_assert_no_individual_grain_leak` is
    falsifiable, not a tautology that would pass against any output. Simulates the exact bug
    class this test file exists to catch: a future edit that adds an actor column to a
    NON-sanctioned panel's fetcher (here, spliced directly into the rendered
    `panel-handoff-graph` substring, a non-sanctioned panel)."""
    html_text, _ = render_manager_view(conn, now=NOW)

    # sanity: the SAME check currently passes on the real, unmutated output
    _assert_no_individual_grain_leak(html_text, ["dana", "erin"])

    mutated = html_text.replace(
        '<h2>Handoff graph (by area)</h2>',
        '<h2>Handoff graph (by area)</h2><span>from_actor: dana</span>',
        1,
    )
    assert "dana" in _sections(mutated)["panel-handoff-graph"], \
        "fixture regressed: negative control no longer lands inside a non-sanctioned panel"

    with pytest.raises(AssertionError, match="individual-grain leak"):
        _assert_no_individual_grain_leak(mutated, ["dana", "erin"])
