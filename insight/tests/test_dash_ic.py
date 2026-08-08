# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.ic (issue #126, E4.S3, Task 2): the four queries + render_ic_view, and
the cold-start honesty extensions from the plan-review nits (actor-never-appeared banner;
blocked-on-me's own never-measured-vs-nothing-outstanding split)."""
import datetime
import json
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import assert_self_contained  # noqa: E402
from insight.gaps.report import json_default  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.dash.ic import (  # noqa: E402
    _actor_ever_appeared,
    _blocked_on_me_rows,
    _cost_row,
    _handoff_ever_ingested,
    _my_queue_kind_ever_ingested,
    _my_queue_rows,
    _park_count,
    _park_ever_ingested,
    _verdicts_given_rows,
    _verdicts_ever_ingested,
    collect_ic_payload,
    render_ic_view,
)
# issue #310 [E19.S2] Task 1: the alice/bob/carol fixture used by test_dash_ic_no_leak.py, reused
# here (not re-derived) for the two new collect_ic_payload tests below -- see ic_fixture.py's own
# docstring.
from insight.tests.ic_fixture import seed_alice_bob_carol  # noqa: E402

#: NAIVE UTC, deliberately -- fact_event.ts/fact_handoff.opened_ts are TIMESTAMP (no tz) columns,
#: and duckdb's python driver silently converts a tz-AWARE datetime parameter to the LOCAL
#: system's naive wall-clock time on insert (live-verified: binding tzinfo=utc on a machine in
#: IST stores 05:30, not 00:00) -- so every timestamp used for both inserting AND for
#: render_ic_view's own `now=` must be naive to compare like-for-like.
NOW = datetime.datetime(2026, 8, 1)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _insert_event(conn, project_id, goal_id, ts, actor, kind, reliability_class=1):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [project_id, goal_id, ts, actor, kind, reliability_class],
    )


def _data_script(html_text):
    m = re.search(
        r'<script type="application/json" id="insight-ic-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m, "inlined data script not found in rendered page"
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- my queue


def test_my_queue_returns_every_open_claim_for_the_actor_not_just_the_oldest(conn):
    """metric_10's own per-actor reduction (ROW_NUMBER ... WHERE rn = 1) would drop one of these
    two -- this pins the un-reduced variant this story ships instead."""
    _insert_event(conn, "p1", "g1", NOW - datetime.timedelta(days=5), "alice", "claimed")
    _insert_event(conn, "p1", "g2", NOW - datetime.timedelta(days=1), "alice", "claimed")
    rows = _my_queue_rows(conn, "alice")
    assert {r["goal_id"] for r in rows} == {"g1", "g2"}
    assert all(r["actor_id"] == "alice" for r in rows)


def test_my_queue_excludes_other_actors(conn):
    _insert_event(conn, "p1", "g1", NOW - datetime.timedelta(days=5), "alice", "claimed")
    _insert_event(conn, "p1", "g2", NOW - datetime.timedelta(days=1), "bob", "claimed")
    rows = _my_queue_rows(conn, "alice")
    assert [r["goal_id"] for r in rows] == ["g1"]


# --------------------------------------------------------------------------- blocked on me


def test_blocked_on_me_excludes_settled_handoffs(conn):
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts, settled_ts) VALUES "
        "('p1', 'bob', 'alice', 'insight', 301, 'p1', ?, NULL), "
        "('p1', 'carl', 'alice', 'insight', 302, 'p1', ?, ?)",
        [NOW, NOW, NOW],
    )
    rows = _blocked_on_me_rows(conn, "alice")
    assert [r["issue"] for r in rows] == [301]


def test_blocked_on_me_excludes_handoffs_addressed_to_someone_else(conn):
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES "
        "('p1', 'bob', 'carol', 'insight', 301, 'p1', ?), "
        "('p1', 'alice', 'bob', 'insight', 302, 'p1', ?)",
        [NOW, NOW],
    )
    rows = _blocked_on_me_rows(conn, "alice")
    assert rows == []


def test_handoff_ever_ingested_distinguishes_never_measured_from_nothing_outstanding(conn):
    assert _handoff_ever_ingested(conn, "p1") is False
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts, settled_ts) VALUES ('p1', 'bob', 'carol', 'insight', 301, 'p1', ?, ?)",
        [NOW, NOW],
    )
    assert _handoff_ever_ingested(conn, "p1") is True

    html_never, _ = render_ic_view(conn, "someone-else-entirely", project_id="p2", now=NOW)
    html_nothing_outstanding, _ = render_ic_view(conn, "alice", project_id="p1", now=NOW)
    never_copy = "No hand-off has ever been recorded for this project yet"
    nothing_copy = "Nothing blocked on you right now."
    assert never_copy in html_never
    assert nothing_copy not in html_never
    assert nothing_copy in html_nothing_outstanding
    assert never_copy not in html_nothing_outstanding


# --------------------------------------------------------------------------- my parks


def test_park_count_is_a_real_zero_not_a_phantom_row(conn):
    cur = conn.execute(
        "SELECT count(*) FROM fact_event WHERE kind = 'parked' AND reliability_class = 1 "
        "AND actor_id = ?", ["alice"],
    )
    assert cur.fetchone() == (0,)
    assert _park_count(conn, "alice") == 0


def test_park_count_counts_only_the_resolved_actor(conn):
    _insert_event(conn, "p1", "g1", NOW, "alice", "parked")
    _insert_event(conn, "p1", "g2", NOW, "bob", "parked")
    _insert_event(conn, "p1", "g3", NOW, "bob", "parked")
    assert _park_count(conn, "alice") == 1
    assert _park_count(conn, "bob") == 2


# --------------------------------------------------------------------------- my gate verdicts (given)


def test_verdicts_given_never_includes_verdicts_on_the_actors_own_prs(conn):
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES "
        "('p1', 1, 'gh', 'e1', 'alice', 'approved', ?), "
        "('p1', 2, 'gh', 'e2', 'bob', 'changes_requested', ?)",
        [NOW, NOW],
    )
    rows = _verdicts_given_rows(conn, "alice")
    assert [r["pr_number"] for r in rows] == [1]
    assert all(r["verdict"] != "changes_requested" for r in rows)


# --------------------------------------------------------------------------- my cost


def test_cost_row_is_absent_by_construction_when_all_three_columns_are_null(conn):
    _insert_event(conn, "p1", "g1", NOW, "alice", "claimed")
    row = _cost_row(conn, "alice")
    assert row == (None, None, None, 0)
    html_text, summary = render_ic_view(conn, "alice", now=NOW)
    assert summary["cost_absent"] is True
    assert "tokens_in/tokens_out/cost_cents have zero writers" in html_text


def test_cost_row_is_genuinely_live_when_a_column_is_populated(conn):
    """Simulates a future emitter landing on tokens_in -- proves the query isn't hardcoded to
    always return ABSENT."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class, "
        "tokens_in) VALUES ('p1', 'g1', ?, 'alice', 'claimed', 1, 500)", [NOW],
    )
    row = _cost_row(conn, "alice")
    assert row == (500, None, None, 1)
    html_text, summary = render_ic_view(conn, "alice", now=NOW)
    assert summary["cost_absent"] is False
    assert "tokens_in/tokens_out/cost_cents have zero writers" not in html_text


def test_cost_row_ignores_reliability_class_2_rows(conn):
    """Mirrors test_dash_manager.py's own test_reason_class_measured_count_ignores_reliability_class_2
    -- same doctrine (spec line 563: a NOW read must not trust a reliability_class=2 row), same
    shape of proof, applied to the one ic.py fetcher that didn't have it yet (issue #129 D7)."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class, "
        "tokens_in) VALUES ('p1', 'g1', ?, 'alice', 'claimed', 2, 500)", [NOW],
    )
    assert _cost_row(conn, "alice") == (None, None, None, 0)


# --------------------------------------------------------------------------- cold start: actor never appeared


def test_actor_ever_appeared_true_via_fact_event(conn):
    _insert_event(conn, "p1", "g1", NOW, "alice", "claimed")
    assert _actor_ever_appeared(conn, "alice") is True


def test_actor_ever_appeared_true_via_fact_handoff_only(conn):
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'bob', 'alice', 'insight', 301, 'p1', ?)", [NOW],
    )
    assert _actor_ever_appeared(conn, "alice") is True


def test_actor_ever_appeared_true_via_fact_pr_review_only(conn):
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 1, 'gh', 'e1', 'alice', 'approved', ?)", [NOW],
    )
    assert _actor_ever_appeared(conn, "alice") is True


def test_actor_never_appeared_renders_the_suspect_config_banner(conn):
    """Regression test for plan-review nit 1: a stale/typo'd ledger.actor must not render a
    clean, indistinguishable-from-genuinely-idle page."""
    _insert_event(conn, "p1", "g1", NOW, "someone-real", "claimed")
    assert _actor_ever_appeared(conn, "totally-unknown") is False
    html_text, summary = render_ic_view(conn, "totally-unknown", now=NOW)
    assert summary["actor_ever_appeared"] is False
    assert "has never appeared in this project" in html_text
    assert "ledger.actor" in html_text


def test_genuinely_idle_actor_who_has_appeared_does_not_get_the_suspect_banner(conn):
    """A real actor with zero open claims, zero blocked-on, zero parks, zero verdicts -- but who
    DID appear once (e.g. only as a PR reviewer) -- must render the ordinary empty states, not
    the suspect-config banner."""
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 1, 'gh', 'e1', 'idle-actor', 'approved', ?)", [NOW],
    )
    html_text, summary = render_ic_view(conn, "idle-actor", now=NOW)
    assert summary["actor_ever_appeared"] is True
    assert "has never appeared in this project" not in html_text


# --------------------------------------------------------------------------- issue #315 [E20.S4] D1:
# per-table ever-ingested signals are NOT the same fact as actor_ever_appeared


def test_actor_known_only_via_fact_pr_review_does_not_ever_ingest_my_queue_or_park(conn):
    """Mirrors test_actor_ever_appeared_true_via_fact_pr_review_only's own fixture exactly: an
    actor known ONLY via fact_pr_review makes _actor_ever_appeared True while fact_event stays
    completely untouched. Under the (rejected) round-1 single-gate design, "My queue" and "My
    parks" would render a confident, measured 0 for a table nobody has ever written a row to for
    ANY actor -- the exact ABSENT-reads-as-PASS bug done-when 4 exists to prevent, recurring at
    table granularity. The corrected per-table signals must stay False here."""
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 1, 'gh', 'e1', 'alice', 'approved', ?)", [NOW],
    )
    assert _actor_ever_appeared(conn, "alice") is True
    assert _my_queue_kind_ever_ingested(conn) is False
    assert _park_ever_ingested(conn) is False


def test_actor_known_only_via_fact_event_does_not_ever_ingest_verdicts(conn):
    """The mirror case: an actor known ONLY via fact_event (a plain 'claimed' row) makes
    _actor_ever_appeared True while fact_pr_review stays completely untouched -- "My gate verdicts
    given" must not render a measured 0 in that state either."""
    _insert_event(conn, "p1", "g1", NOW, "alice", "claimed")
    assert _actor_ever_appeared(conn, "alice") is True
    assert _verdicts_ever_ingested(conn) is False


def test_ever_ingested_flags_go_true_once_the_matching_table_has_any_row_for_anyone(conn):
    """The positive case, so the three new signals are proven to actually light up and not just
    stay permanently False: once a fact_event row exists for ANY actor (not necessarily the
    resolved one) and the resolved actor has separately appeared, the corresponding ever_ingested
    flag is True -- these are table-wide facts, not actor-scoped ones, mirroring
    _handoff_ever_ingested's own already-established semantics exactly."""
    # bob's row makes the tables "ever ingested" -- alice never appears in either yet.
    _insert_event(conn, "p1", "g-bob-1", NOW, "bob", "claimed")
    _insert_event(conn, "p1", "g-bob-2", NOW, "bob", "parked")
    assert _my_queue_kind_ever_ingested(conn) is True
    assert _park_ever_ingested(conn) is True

    # alice appears via a route unrelated to fact_event (fact_pr_review), so
    # actor_ever_appeared(alice) is now True too -- both halves of the AND are satisfied.
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 2, 'gh', 'e2', 'alice', 'approved', ?)", [NOW],
    )
    assert _actor_ever_appeared(conn, "alice") is True
    assert _verdicts_ever_ingested(conn) is True

    payload = collect_ic_payload(conn, "alice", now=NOW)
    assert payload["actor_ever_appeared"] is True
    assert payload["my_queue_ever_ingested"] is True
    assert payload["park_ever_ingested"] is True
    assert payload["verdicts_ever_ingested"] is True
    # alice herself has no open claim and no park -- her own counts are real, measured
    # zeros/empties, not because the tables were never ingested. Her verdict IS real (she is the
    # one who gave it, via the fact_pr_review insert immediately above).
    assert payload["my_queue"] == []
    assert payload["park_count"] == 0
    assert [r["pr_number"] for r in payload["verdicts_given"]] == [2]


def test_ever_ingested_flags_are_project_scoped_when_project_id_is_given(conn):
    """Mirrors test_handoff_ever_ingested_distinguishes_never_measured_from_nothing_outstanding's
    own project-scoping discipline for the three new signals."""
    _insert_event(conn, "p1", "g1", NOW, "bob", "claimed")
    assert _my_queue_kind_ever_ingested(conn, "p1") is True
    assert _my_queue_kind_ever_ingested(conn, "p2") is False
    assert _park_ever_ingested(conn, "p1") is False

    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 1, 'gh', 'e1', 'bob', 'approved', ?)", [NOW],
    )
    assert _verdicts_ever_ingested(conn, "p1") is True
    assert _verdicts_ever_ingested(conn, "p2") is False


# --------------------------------------------------------------------------- render_ic_view shell


def test_empty_my_queue_reuses_the_existing_absent_svg_unmodified(conn):
    html_text, _ = render_ic_view(conn, "alice", now=NOW)
    assert 'aria-label="Aging WIP: no open claims"' in html_text
    assert "no open claims measured" in html_text


def test_render_ic_view_output_is_self_contained(conn):
    _insert_event(conn, "p1", "g1", NOW - datetime.timedelta(days=3), "alice", "claimed")
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'bob', 'alice', 'insight', 301, 'p1', ?)", [NOW],
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, actor, verdict, "
        "event_ts) VALUES ('p1', 1, 'gh', 'e1', 'alice', 'approved', ?)", [NOW],
    )
    html_text, _ = render_ic_view(conn, "alice", now=NOW)
    assert_self_contained(html_text)  # must not raise


def test_render_ic_view_payload_round_trips_through_the_json_script_block(conn):
    _insert_event(conn, "p1", "g1", NOW - datetime.timedelta(days=3), "alice", "claimed")
    html_text, summary = render_ic_view(conn, "alice", now=NOW)
    payload = _data_script(html_text)
    assert payload["actor"] == "alice"
    assert len(payload["my_queue"]) == summary["my_queue_count"] == 1


# --------------------------------------------------------------------------- collect_ic_payload (issue #310 Task 1)


def test_collect_ic_payload_matches_render_ic_views_internal_payload(conn):
    """Calls `collect_ic_payload` directly against the alice/bob/carol fixture
    (`test_dash_ic_no_leak.py`'s own fixture, reused via `ic_fixture.seed_alice_bob_carol` --
    not re-derived) and asserts its shape/values match what `render_ic_view`'s own inlined
    `<script>` block reports for the same inputs -- the two must never drift apart, since
    `render_ic_view` now calls this very function (Task 1's pure-refactor claim)."""
    seed_alice_bob_carol(conn)
    payload = collect_ic_payload(conn, "alice", project_id="proj1", now=NOW)

    assert payload["actor"] == "alice"
    assert payload["actor_ever_appeared"] is True
    assert payload["generated_at"] == NOW.isoformat()
    assert [r["goal_id"] for r in payload["my_queue"]] == ["g-alice-1"]
    assert payload["blocked_on_me"] == [{
        "from_actor": "bob", "to_actor": "alice", "area": "insight", "issue": 301,
        "priority": "p1", "opened_ts": NOW,
    }]
    assert payload["handoff_ever_ingested"] is True
    assert payload["park_count"] == 0
    assert [r["pr_number"] for r in payload["verdicts_given"]] == [9101]
    assert payload["cost"] == {
        "tokens_in": None, "tokens_out": None, "cost_cents": None, "n": 0,
    }

    # render_ic_view, given the identical inputs, must report the identical payload through its
    # own inlined <script> block -- proving the two paths did not silently diverge. Compared via
    # a JSON round-trip on BOTH sides (not raw equality) because `payload` still carries real
    # `datetime` objects (e.g. `blocked_on_me[0]["opened_ts"]`) while the rendered block already
    # went through `json_script`'s own datetime-to-string serialisation.
    html_text, summary = render_ic_view(conn, "alice", project_id="proj1", now=NOW)
    rendered_payload = _data_script(html_text)
    payload_via_json = json.loads(json.dumps(payload, default=json_default))
    assert rendered_payload == payload_via_json
    assert summary["my_queue_count"] == len(payload["my_queue"])
    assert summary["blocked_on_me_count"] == len(payload["blocked_on_me"])
    assert summary["verdicts_given_count"] == len(payload["verdicts_given"])


def test_collect_ic_payload_never_carries_carols_needles(conn):
    """Payload-level leak test (issue #310 Task 1) -- independent of `render_ic_view`'s HTML
    string. `collect_ic_payload`'s return value is the exact object the new `insight web ic` CLI
    bridge (Task 2) ships over the wire, so it earns its own whole-string check: serialise it with
    the same `json_default` the CLI bridge uses and assert none of carol's needles appear anywhere
    in the serialised text -- mirrors `test_dash_ic_no_leak.py`'s own needle list exactly."""
    seed_alice_bob_carol(conn)
    payload = collect_ic_payload(conn, "alice", project_id="proj1", now=NOW)
    serialized = json.dumps(payload, default=json_default)
    for needle in ("carol", "g-carol-1", "g-carol-2", "9103", "303"):
        assert needle not in serialized, f"carol leaked via {needle!r}"
