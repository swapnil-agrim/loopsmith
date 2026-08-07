# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Shared alice/bob/carol fixture-writing helper for `insight.dash.ic`'s leak-proof test suites
(issue #310 [E19.S2] Task 1) -- factored out of `test_dash_ic_no_leak.py`'s own `conn` fixture so
`test_dash_ic.py`, `test_dash_ic_no_leak.py`, and `test_cli_web_ic.py` can all reuse the EXACT
same three `INSERT` statements rather than re-deriving (and risking drifting) the fixture shape a
second or third time.

Fixture shape (unchanged from `test_dash_ic_no_leak.py`'s original inline version): alice is the
resolved actor. bob is alice's legitimate hand-off counterparty (bob -> alice, open) -- the ONE
sanctioned exception (see `insight/dash/ic.py`'s module docstring): his name may appear as
`from_actor` on that one row, and nothing else about him may appear anywhere. carol has NO
relationship to alice at all and must be totally invisible -- not her name, not any of her goal
ids, not her PR number, not her verdict text.

NOW is naive UTC, deliberately -- see `test_dash_ic.py`'s own NOW comment: duckdb's python driver
silently converts a tz-AWARE datetime parameter to the local system's naive wall-clock time on
insert, which would desynchronize any exact timestamp-equality check against what was actually
inserted.
"""
import datetime

NOW = datetime.datetime(2026, 8, 1)


def seed_alice_bob_carol(conn):
    """Populate an already-`ensure_schema`'d connection with the alice/bob/carol fixture, against
    project_id `"proj1"`. Byte-for-byte the same three `INSERT` statements
    `test_dash_ic_no_leak.py`'s `conn` fixture used before this helper existed."""
    # fact_event: alice has one open claim; bob has an open claim AND a park; carol has an open
    # claim AND a park too -- carol must be totally invisible in alice's rendered view.
    conn.execute(
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
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts, ack_ts, ack_state, settled_ts) VALUES "
        "('proj1', 'bob', 'alice', 'insight', 301, 'p1', ?, NULL, NULL, NULL), "
        "('proj1', 'alice', 'bob', 'insight', 302, 'p2', ?, ?, 'resolved', ?), "
        "('proj1', 'carol', 'bob', 'insight', 303, 'p1', ?, NULL, NULL, NULL)",
        [NOW, NOW, NOW, NOW, NOW],
    )

    # fact_pr_review: one verdict each, alice/bob/carol as reviewer.
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES "
        "('proj1', 9101, 'gh', 'e1', 'alice', 'approved', ?), "
        "('proj1', 9102, 'gh', 'e2', 'bob', 'changes_requested', ?), "
        "('proj1', 9103, 'gh', 'e3', 'carol', 'approved', ?)",
        [NOW, NOW, NOW],
    )
