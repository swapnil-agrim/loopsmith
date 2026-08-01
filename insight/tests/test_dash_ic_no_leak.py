# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The proving test for issue #126's own Task 2 -- "no cross-actor row leaks into the IC view".

Fixture: three actors. alice is the resolved actor. bob is alice's legitimate hand-off
counterparty (bob -> alice, open) -- the ONE sanctioned exception (Decision 5 of
.sdlc/plans/126.md): his name may appear as `from_actor` on that one row, and nothing else about
him may appear anywhere. carol has NO relationship to alice at all and must be totally invisible --
not her name, not any of her goal ids, not her PR number, not her verdict text.

Assertions run on the WHOLE RENDERED HTML STRING (`assert "carol" not in html_text`, etc.) --
never only on the parsed JSON payload -- because the data is inlined in a
`<script type="application/json">` block that is readable via View Source regardless of whether
any JS ever parses it. A defence-in-depth pass additionally checks the RAW ROWS returned by each
query, independent of rendering, so a bug that filtered correctly in SQL but rendered wrong (or
vice versa) is caught by whichever half it actually breaks.

The negative control at the bottom is not shipped code -- it exists solely to prove this file's
own assertion methodology is falsifiable, not a tautology that would pass against any output: the
exact same fixture, rendered through a deliberately unfiltered query, must FAIL the same "carol is
absent" check that the positive tests above rely on.
"""
import datetime

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.dash.ic import (  # noqa: E402
    _MY_QUEUE_SQL,
    _blocked_on_me_rows,
    _my_queue_rows,
    _verdicts_given_rows,
    render_ic_view,
)

#: NAIVE UTC, deliberately -- see insight/tests/test_dash_ic.py's own NOW comment: duckdb's python
#: driver silently converts a tz-AWARE datetime parameter to the LOCAL system's naive wall-clock
#: time on insert, which would desynchronize the exact opened_ts equality check below from what
#: was actually inserted.
NOW = datetime.datetime(2026, 8, 1)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)

    # fact_event: alice has one open claim; bob has an open claim AND a park; carol has an open
    # claim AND a park too -- carol must be totally invisible in alice's rendered view.
    c.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES "
        "('proj1', 'g-alice-1', ?, 'alice', 'claimed', 1), "
        "('proj1', 'g-bob-1',   ?, 'bob',   'claimed', 1), "
        "('proj1', 'g-bob-2',   ?, 'bob',   'parked',  1), "
        "('proj1', 'g-carol-1', ?, 'carol', 'claimed', 1), "
        "('proj1', 'g-carol-2', ?, 'carol', 'parked',  1)",
        [
            NOW - datetime.timedelta(days=5), NOW - datetime.timedelta(days=1),
            NOW - datetime.timedelta(days=2), NOW - datetime.timedelta(days=6),
            NOW - datetime.timedelta(days=7),
        ],
    )

    # fact_handoff: bob -> alice (OPEN, the one legitimate exception), alice -> bob (SETTLED, must
    # not show), carol -> bob (nothing to do with alice at all, must not show).
    c.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts, ack_ts, ack_state, settled_ts) VALUES "
        "('proj1', 'bob', 'alice', 'insight', 301, 'p1', ?, NULL, NULL, NULL), "
        "('proj1', 'alice', 'bob', 'insight', 302, 'p2', ?, ?, 'resolved', ?), "
        "('proj1', 'carol', 'bob', 'insight', 303, 'p1', ?, NULL, NULL, NULL)",
        [NOW, NOW, NOW, NOW, NOW],
    )

    # fact_pr_review: one verdict each, alice/bob/carol as reviewer.
    c.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES "
        "('proj1', 9101, 'gh', 'e1', 'alice', 'approved', ?), "
        "('proj1', 9102, 'gh', 'e2', 'bob', 'changes_requested', ?), "
        "('proj1', 9103, 'gh', 'e3', 'carol', 'approved', ?)",
        [NOW, NOW, NOW],
    )
    yield c
    c.close()


def test_blocked_on_me_is_exactly_one_row_from_bob_before_any_rendering(conn):
    """Asserted on the query result directly, not eyeballed in the rendered string -- matches the
    plan's own live-proven assertion shape."""
    rows = _blocked_on_me_rows(conn, "alice")
    assert rows == [{
        "from_actor": "bob", "to_actor": "alice", "area": "insight", "issue": 301,
        "priority": "p1", "opened_ts": NOW,
    }]


def test_carol_is_fully_absent_from_alices_rendered_ic_view(conn):
    html_text, _ = render_ic_view(conn, "alice", project_id="proj1", now=NOW)
    for needle in ("carol", "g-carol-1", "g-carol-2", "9103", "303"):
        assert needle not in html_text, f"carol leaked via {needle!r}"


def test_bobs_own_individual_data_is_absent_except_the_one_sanctioned_from_actor_mention(conn):
    html_text, _ = render_ic_view(conn, "alice", project_id="proj1", now=NOW)
    for needle in ("g-bob-1", "g-bob-2", "9102", "changes_requested", "302"):
        assert needle not in html_text, f"bob's own data leaked via {needle!r}"
    # bob's settled outbound hand-off (alice -> bob, issue 302) must not appear either -- checked
    # above via "302" -- and the one legitimate mention (from_actor on issue 301) is proven by the
    # query-level test above, not re-derived here from string counting (a JSON payload + an HTML
    # table both legitimately mention the same one real row).
    assert "bob" in html_text  # the one sanctioned mention must still be present


def test_every_raw_row_from_every_query_carries_only_alices_own_identifier(conn):
    """Defence in depth, independent of the rendered string: a bug that filtered correctly in SQL
    but rendered wrong (or vice versa) is caught by whichever half it actually breaks."""
    for row in _my_queue_rows(conn, "alice"):
        assert row["actor_id"] == "alice"
    for row in _blocked_on_me_rows(conn, "alice"):
        assert row["to_actor"] == "alice"
    for row in _verdicts_given_rows(conn, "alice"):
        assert row["pr_number"] == 9101


def test_render_ic_view_summary_counts_match_the_scoped_fixture(conn):
    _, summary = render_ic_view(conn, "alice", project_id="proj1", now=NOW)
    assert summary["my_queue_count"] == 1
    assert summary["blocked_on_me_count"] == 1
    assert summary["verdicts_given_count"] == 1


# --------------------------------------------------------------------------- negative control


def _assert_carol_absent(html_fragment):
    """The SAME assertion the positive tests above rely on -- shared, so the positive and negative
    tests can never drift into checking different things."""
    assert "carol" not in html_fragment, "carol leaked"


def test_negative_control_proves_the_leak_methodology_has_teeth(conn):
    """Not shipped code -- exists solely to prove `_assert_carol_absent` is falsifiable. Runs the
    my_queue query with its `actor_id = ?` predicate deliberately removed (the exact bug class
    Decision 4 of .sdlc/plans/126.md exists to prevent) and asserts the SAME check now FAILS."""
    unfiltered_sql = _MY_QUEUE_SQL.replace("AND actor_id = ?", "")
    cur = conn.execute(unfiltered_sql)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    actors = {r["actor_id"] for r in rows}
    assert "carol" in actors, "fixture regressed: negative control no longer proves anything"

    rendered_fragment = " ".join(f"{r['actor_id']} {r['goal_id']}" for r in rows)
    with pytest.raises(AssertionError):
        _assert_carol_absent(rendered_fragment)
