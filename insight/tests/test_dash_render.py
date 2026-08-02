# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.render (issue #124, E4.S1). See .sdlc/plans/124.md sections K/L for
the two plan-review blocking findings these tests pin as regressions (a bare `count(*)` being
fooled by a bare-aggregate metric view's phantom row; a single "cold" signal conflating "never
ingested" with a real onboarding-week store), and section F for the escaping proof."""
import html
import json
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.gaps.report import build_report  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import MetricLoadError  # noqa: E402
from insight.dash.render import (  # noqa: E402
    CoverageDenominatorMissing,
    assert_self_contained,
    coverage_denominator_html,
    extract_coverage,
    render_dashboard,
)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _data_script(html_text):
    m = re.search(
        r'<script type="application/json" id="insight-dash-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m, "inlined data script not found in rendered page"
    return json.loads(m.group(1))


def _onboarding_table(html_text):
    m = re.search(
        r'<table id="cold-start-metrics">.*?</table>', html_text, re.DOTALL,
    )
    assert m, "cold-start onboarding table not found in rendered page"
    return m.group(0)


def test_render_dashboard_against_real_metric_catalog_and_gaps(conn):
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] == 25
    expected_verdict = build_report(conn)["verdict"]["overall"]
    assert summary["gaps_verdict"] == expected_verdict


def test_live_measured_count_overrides_the_stale_dark_label(tmp_path, conn):
    """Metric 26 (Verify reliability) and 35 (Lease contention) are both labelled `dark` in the
    real catalog today but neither has a `_count`-suffixed column (.sdlc/plans/124.md section K),
    so this assertion is unaffected by, and independent of, the _measured() fix. Populate both
    metrics' underlying source tables with a real row and assert the rendered payload shows
    has_data True for at least one of them while labelled_dark also stays True."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', now(), 'a1', 'claimed', 1)"
    )
    html_text, _ = render_dashboard(conn, "s.duckdb")
    payload = _data_script(html_text)
    metric_35 = next(m for m in payload["metrics"] if m["id"] == "35")
    assert metric_35["labelled_dark"] is True
    assert metric_35["has_data"] is True


def test_escaping_survives_a_script_breakout_payload(tmp_path, conn):
    """Reuses .sdlc/plans/124.md section F's exact </script> breakout payload, planted as a
    metric's own `-- name:` header field -- the simplest hermetic route to attacker-controlled
    text reaching the rendered page."""
    payload_str = '</script><script>window.__pwned=true;</script>'
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "1.sql").write_text(
        f"-- name: {payload_str}\n-- question: ?\n-- personas: manager\n"
        "-- reliability_class: 1\n-- guardrail: g\n"
        "SELECT 1 AS n\n",
        encoding="utf-8",
    )
    html_text, _ = render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
    assert_self_contained(html_text)  # no literal <script> breakout introduced
    payload = _data_script(html_text)
    metric_1 = next(m for m in payload["metrics"] if m["id"] == "1")
    assert metric_1["name"] == payload_str  # round-trips losslessly through json_script/json.loads


def test_assert_self_contained_passes_on_real_output(conn):
    html_text, _ = render_dashboard(conn, "s.duckdb")
    assert_self_contained(html_text)  # must not raise


def test_assert_self_contained_catches_a_planted_external_reference():
    with pytest.raises(AssertionError):
        assert_self_contained('<script src="https://cdn.example.com/x.js"></script>')
    with pytest.raises(AssertionError):
        assert_self_contained('<link href="//fonts.googleapis.com/css?family=Roboto">')


def test_never_ingested_store_renders_the_never_ingested_banner(conn):
    """A fresh ensure_schema'd store, zero rows anywhere, INCLUDING dim_project -- the exact
    plan-review repro, .sdlc/plans/124.md sections K/L. This is the regression test for
    Blocking 1: against the pre-fix blanket count(*) heuristic this store would have looked
    non-empty (metrics 3/11/12/14's own phantom aggregate row), and this assertion would fail."""
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is False
    assert summary["has_data"] is False
    assert "this store has never been ingested" in html_text


def test_onboarding_week_store_renders_the_distinct_no_data_yet_banner_not_never_ingested(conn):
    """A real dim_project row (a real `insight ingest` ran) but zero rows anywhere else --
    spec's own 'cold start' onboarding week. Regression test for Blocking 2: a single-signal
    design cannot distinguish this fixture from the previous one and would render the wrong
    banner here."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/fresh-repo', true, now(), now())"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is False
    assert "Ingested, nothing measurable yet" in html_text
    assert "this store has never been ingested" not in html_text


def test_metrics_3_11_12_14_never_falsely_report_data_on_an_empty_store(conn):
    """Pins the per-metric fix, not only its store-wide consequence -- these four are bare-
    aggregate views with no GROUP BY, so a naive count(*) always returned 1 for each regardless
    of population (.sdlc/plans/124.md section K)."""
    html_text, _ = render_dashboard(conn, "s.duckdb")
    payload = _data_script(html_text)
    by_id = {m["id"]: m for m in payload["metrics"]}
    for metric_id in ("3", "11", "12", "14"):
        assert by_id[metric_id]["has_data"] is False, metric_id


def test_target_scenario_collectors_populated_ledger_off_renders_the_ledger_enablement_banner(conn):
    """#128's own stated target: ingested, collectors ran (fact_goal + fact_collector_pack +
    fact_merge_lead_time all populated), but the ledger has never written a row. This is also the
    fixture that falsifies the round-1-revised `other_has_data` (allowlist-complement) design --
    see .sdlc/plans/128.md's 'why the allowlist-complement condition is wrong' section: metric 26
    (bare fact_goal count) and metric 41 (fact_collector_pack, schema alignment-collect/v1) both
    read has_data=True here despite neither being a cold-start metric, which would leave
    other_has_data=True and banner="" under that design even after it "landed"."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present, plan_artifact_present) "
        "VALUES ('p1', 'g1', true, true)"
    )
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5,\"plan_existed_pct\":80},\"d5\":"
        "{\"commits_with_review_pct\":60}}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is True
    assert "this store has never been ingested" not in html_text
    assert "Ingested, nothing measurable yet" not in html_text
    assert "Ledger not enabled" in html_text
    assert "sync.py bootstrap" in html_text
    assert "turn it on with one config change" in html_text
    assert_self_contained(html_text)


def test_collector_only_store_renders_the_ledger_enablement_banner(conn):
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is True
    assert "this store has never been ingested" not in html_text
    assert "Ingested, nothing measurable yet" not in html_text
    assert "Ledger not enabled" in html_text
    assert "sync.py bootstrap" in html_text
    assert "turn it on with one config change" in html_text
    assert_self_contained(html_text)


def test_collector_only_store_never_shows_a_bare_zero_for_the_still_unmeasured_cold_start_metrics(conn):
    """Same fixture as the collector-only test above (only fact_merge_lead_time populated).
    Metric 42 is ALSO populated by this fixture -- 42.sql's merge_stats CTE reads
    fact_merge_lead_time directly -- so the still-unmeasured set here is 6 ids, not 7:
    4, 5, 9, 20, 24, 30 (NOT 42)."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    html_text, _ = render_dashboard(conn, "s.duckdb")
    payload = _data_script(html_text)
    by_id = {m["id"]: m for m in payload["metrics"]}
    table = _onboarding_table(html_text)
    for metric_id in ("4", "5", "9", "20", "24", "30"):
        assert by_id[metric_id]["measured"] == 0, metric_id
        assert by_id[metric_id]["has_data"] is False, metric_id
        assert re.search(rf"<td>{metric_id}</td><td>[^<]*</td><td>no data yet</td>", table), metric_id


def test_fully_warm_store_with_ledger_data_renders_no_banner_at_all(conn):
    """Passes against both today's unmodified code (falls to banner="" for the old,
    coincidentally-right reason) and the fixed code (deliberately takes the final else) -- a
    non-regression guard for the fully-warm case, not evidence the fix works on its own."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', now(), 'a1', 'claimed', 1)"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is True
    assert "this store has never been ingested" not in html_text
    assert "Ingested, nothing measurable yet" not in html_text
    assert "Ledger not enabled" not in html_text


# --------------------------------------------------------------------------- issue #134 (E5.S4):
# the config.html?field=<dotted.path> deep-link contract (Decision 4, .sdlc/plans/134.md).


def test_config_deep_link_builds_a_relative_url_with_the_field_pre_selected():
    from insight.dash.render import CONFIG_UI_PATH, config_deep_link
    assert CONFIG_UI_PATH == "config.html"
    assert config_deep_link("work.require_review") == "config.html?field=work.require_review"


def test_config_deep_link_url_encodes_a_field_value_needing_escaping():
    from insight.dash.render import config_deep_link
    assert config_deep_link("a b") == "config.html?field=a%20b"


def test_assert_self_contained_passes_on_a_config_deep_link():
    assert_self_contained('<a href="config.html?field=work.require_review">Fix in config</a>')


# --------------------------------------------------------------------------- issue #134 (E5.S4):
# the four-part gap card (what / evidence / metric / action), replacing _render_gaps_table.

#: Matches the finding shape build_report now produces (rule_id/name/config_field on top of
#: make_finding's class/metric/action/severity/evidence).
_FINDING = {
    "rule_id": "coverage_verify_no_command",
    "name": "Goal has no verify command",
    "class": "Coverage",
    "metric": "26",
    "action": "set the goal's verify_command frontmatter field, or the project's verify.command config",
    "severity": "WARN",
    "evidence": [{"project_id": "p2", "goal_id": "g3"}],
    "config_field": None,
}


def test_render_gap_card_contains_all_four_parts():
    from insight.dash.render import _render_gap_card
    card = _render_gap_card(_FINDING)
    assert 'class="gap-card-what"' in card and _FINDING["name"] in card
    # evidence rows are html.escape(str(row)) -- same str(dict) convention render_report() uses,
    # HTML-escaped since (unlike render_report's plain text) this is markup (.sdlc/plans/134.md
    # "Concretely, what the card HTML is").
    assert 'class="gap-card-evidence"' in card
    assert html.escape(str(_FINDING["evidence"][0])) in card
    assert 'class="gap-card-metric"' in card and _FINDING["metric"] in card
    assert 'class="gap-card-action"' in card
    assert html.escape(_FINDING["action"]) in card
    assert f'data-rule-id="{_FINDING["rule_id"]}"' in card


def test_render_gap_card_omits_the_config_link_when_not_config_fixable():
    from insight.dash.render import _render_gap_card
    card = _render_gap_card(_FINDING)  # config_field=None
    assert "gap-card-config-link" not in card


def test_render_gap_card_includes_the_config_link_when_config_fixable():
    from insight.dash.render import _render_gap_card
    card = _render_gap_card({**_FINDING, "config_field": "verify.command"})
    assert 'class="gap-card-config-link"' in card
    assert 'href="config.html?field=verify.command"' in card


def test_a_real_config_fixable_warn_finding_renders_as_a_complete_card_through_render_dashboard(conn):
    """Task 4's integration test: the same fixture test_gap_rule_coverage_verify_no_command.py's
    own test_an_empty_string_verify_command_is_no_command_not_covered uses, run through the FULL
    render_dashboard pipeline (not just the standalone _render_gap_card unit) -- proves the
    wiring in the actual HTML template, not only the function in isolation."""
    conn.execute("INSERT INTO dim_project (project_id, config_json) VALUES ('p9', '{}')")
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, verify_command) VALUES ('p9', 'g9', '')"
    )
    html_text, _ = render_dashboard(conn, "s.duckdb")
    m = re.search(
        r'<div class="gap-card" data-rule-id="coverage_verify_no_command">.*?</div>',
        html_text, re.DOTALL,
    )
    assert m, "coverage_verify_no_command card not found in rendered page"
    card = m.group(0)
    assert 'class="gap-card-what"' in card
    assert 'class="gap-card-evidence"' in card
    assert 'class="gap-card-metric"' in card
    assert 'class="gap-card-action"' in card
    assert 'href="config.html?field=verify.command"' in card
    assert_self_contained(html_text)  # still passes with the new deep link present


# [R2] Anchors corrected -- see .sdlc/plans/134.md's "How a test locates each part". Each regex
# must capture a node holding ONLY that part's data, never a container that also holds a label or
# an icon: the ORIGINAL draft anchored "what" on the whole <h3> (which also carries the status
# SVG's own <text>) and "metric" on the whole <p> (which also carries the fixed "Metric this gap
# would move:" label) -- both cases silently passed even when name/metric were blanked, which is
# exactly the vacuity this test exists to prevent.
def _gap_card_parts(card_html):
    parts = {}
    what_m = re.search(r'<span class="gap-card-name">(.*?)</span>', card_html, re.DOTALL)
    parts["what"] = re.sub(r"<[^>]+>", "", what_m.group(1)).strip() if what_m else ""
    evidence_m = re.search(r'<ul class="gap-card-evidence">(.*?)</ul>', card_html, re.DOTALL)
    parts["evidence"] = re.sub(r"<[^>]+>", "", evidence_m.group(1)).strip() if evidence_m else ""
    metric_m = re.search(r'<p class="gap-card-metric">.*?<code>(.*?)</code>', card_html, re.DOTALL)
    parts["metric"] = re.sub(r"<[^>]+>", "", metric_m.group(1)).strip() if metric_m else ""
    action_m = re.search(r'<p class="gap-card-action">(.*?)</p>', card_html, re.DOTALL)
    action_text = action_m.group(1) if action_m else ""
    # strip the optional trailing <a class="gap-card-config-link">...</a> before checking
    # non-empty -- or a card with ONLY a config link (no action text) would pass.
    action_text = re.sub(r'<a class="gap-card-config-link".*?</a>', "", action_text, flags=re.DOTALL)
    parts["action"] = re.sub(r"<[^>]+>", "", action_text).strip()
    return parts


def _card_has_all_four_parts(card_html):
    # `all(v)` alone still reports a part PRESENT when its value is only invisible characters:
    # "\u200b" (zero-width space) is truthy, is not whitespace, and survives strip(), so the part
    # renders as a visually blank line while the check says it is there. That is the same
    # "technically present, semantically absent" bug the anchor fix above addressed, one layer
    # down at content instead of markup -- found by #134's PR review. insight.gaps.header's own
    # required-field gate had the identical blind spot and is fixed at the root there.
    return all(_printable(v) for v in _gap_card_parts(card_html).values())


def _printable(value):
    return any(ch.isprintable() and not ch.isspace() for ch in value)


def test_every_shipped_rule_renders_a_card_with_all_four_parts():
    """Task 3's own done_when, proven over the REAL catalog: for every one of the 11 shipped
    rules, a synthetic finding built from that rule's own real header (name/class/metric/
    action/severity) plus one fabricated evidence row renders a card carrying all four parts.
    Proves the wiring holds catalog-wide, not just for one hand-picked example."""
    from insight.dash.render import _render_gap_card
    from insight.gaps.loader import load_gap_rules
    for rule_id, rule in load_gap_rules().items():
        finding = {**rule, "rule_id": rule_id, "evidence": [{"x": "1"}],
                   "config_field": rule["extra"].get("config_field")}
        assert _card_has_all_four_parts(_render_gap_card(finding)), rule_id


@pytest.mark.parametrize("missing_key,broken_value", [
    ("name", ""), ("evidence", []), ("metric", ""), ("action", ""),
    # [R3, from PR review] invisible-but-truthy values: a part that renders as a blank line is
    # absent to a reader, so it must be absent to the check too.
    ("name", "\u200b"), ("metric", "\u200b"), ("action", "\u200b"),
    ("name", "   "), ("metric", "\u00a0"),
])
def test_a_card_missing_any_one_part_is_detected_as_missing(missing_key, broken_value):
    """The negative control: proves _card_has_all_four_parts is not vacuously True. Each
    sub-case breaks exactly one part; the other three stay intact, so a helper that always
    returned True (or checked the wrong tag) would still pass the positive test above but
    fail here. [R2] This is the corrected version -- the FIRST draft's version (anchored on the
    whole <h3>/<p>) silently passed for name and metric; see .sdlc/plans/134.md."""
    from insight.dash.render import _render_gap_card
    finding = {**_FINDING, missing_key: broken_value}
    assert not _card_has_all_four_parts(_render_gap_card(finding))


def test_a_broken_metrics_catalog_raises_metric_load_error(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "1.sql").write_text(
        "-- name: Bad\n-- question: ?\n-- personas: manager\n-- reliability_class: 1\n"
        "SELECT 1\n",  # missing required guardrail header field
        encoding="utf-8",
    )
    with pytest.raises(MetricLoadError):
        render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)


# --------------------------------------------------------------------------- Task 7 (issue #125,
# E4.S2, Decision 6): migrating _STYLE's ad-hoc badge/dot hexes onto insight.dash.colors' tokens.

#: The 9 literal hex strings _STYLE used to bake in directly (research brief, .sdlc/plans/125.md
#: section F) -- the direct proof the migration actually happened, not just that colours "look
#: right" some other way.
_OLD_ADHOC_HEXES = (
    "#e6f4ea", "#1a7f37", "#fff3cd", "#7a5b00", "#ffe0e0",
    "#8a1c1c", "#eee", "#555", "#6e7781",
)


def test_style_no_longer_contains_the_old_adhoc_hexes():
    from insight.dash.render import _STYLE
    for hexval in _OLD_ADHOC_HEXES:
        assert hexval not in _STYLE, f"{hexval!r} should have been migrated onto a colors.py token"


def test_style_declares_dark_mode_scope():
    from insight.dash.render import _STYLE
    assert "@media (prefers-color-scheme: dark)" in _STYLE
    assert '[data-theme="dark"]' in _STYLE


def test_assert_self_contained_still_passes_on_real_output(conn):
    """Explicit named case (Task 7's own proving-test list) -- the migration touches every
    <style> rule in the file, the one most likely to regress this invariant."""
    html_text, _ = render_dashboard(conn, "s.duckdb")
    assert_self_contained(html_text)  # must not raise


# --------------------------------------------------------------------------- Issue #129 (E4.S6):
# coverage-qualified rendering. Spec lines 125-128: every class-2 metric value renders with its
# coverage denominator adjacent; a class-2 value with no denominator is a bug, not a number (D8:
# raise, not render).


def test_extract_coverage_returns_none_for_a_class_1_row():
    row = {"class1_count": 1, "class2_count": 0, "total_count": 1, "coverage_pct": 1.0}
    assert extract_coverage("1", 1, row) is None


def test_extract_coverage_returns_none_when_row_is_none():
    assert extract_coverage("7", 2, None) is None


def test_extract_coverage_returns_the_four_values_for_a_class_2_row_that_carries_them():
    row = {
        "class1_count": 62, "class2_count": 38, "total_count": 100, "coverage_pct": 0.62,
        "unrelated_column": "ignored",
    }
    assert extract_coverage("900", 2, row) == {
        "class1_count": 62, "class2_count": 38, "total_count": 100, "coverage_pct": 0.62,
    }


def test_extract_coverage_treats_a_null_coverage_pct_as_a_legitimate_value_not_a_missing_column():
    row = {"class1_count": 0, "class2_count": 0, "total_count": 0, "coverage_pct": None}
    assert extract_coverage("900", 2, row) == row


def test_extract_coverage_raises_coverage_denominator_missing_when_a_class_2_row_lacks_the_columns():
    with pytest.raises(CoverageDenominatorMissing):
        extract_coverage("900", 2, {"wip_count": 3})


def test_coverage_denominator_html_returns_empty_string_for_none():
    assert coverage_denominator_html(None) == ""


def test_coverage_denominator_html_renders_the_span_with_percentage_and_counts():
    html_frag = coverage_denominator_html(
        {"class1_count": 62, "class2_count": 38, "total_count": 100, "coverage_pct": 0.62}
    )
    assert 'class="coverage-denom"' in html_frag
    assert "62%" in html_frag
    assert "62 of 100 rows class-1" in html_frag
    assert "38 class-2" in html_frag


def test_coverage_denominator_html_renders_n_a_for_a_null_coverage_pct():
    html_frag = coverage_denominator_html(
        {"class1_count": 0, "class2_count": 0, "total_count": 0, "coverage_pct": None}
    )
    assert "n/a" in html_frag


def test_metric_catalog_renders_a_coverage_denominator_next_to_a_class_2_metrics_value(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "900.sql").write_text(
        "-- name: Hypothetical class-2 metric (synthetic, issue #129 fixture)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_dashboard's registry-wide catalog "
        "table renders a coverage denominator for a class-2 metric; not a real shipped metric\n"
        "SELECT 41 AS efficiency_pct,\n"
        "  62 AS class1_count, 38 AS class2_count, 100 AS total_count, 0.62::DOUBLE AS coverage_pct\n",
        encoding="utf-8",
    )
    html_text, _ = render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
    row_html = re.search(r'<tr><td>900</td>.*?</tr>', html_text, re.DOTALL).group(0)
    assert 'class="coverage-denom"' in row_html
    assert "62%" in row_html
    assert "62 of 100 rows class-1" in row_html
    assert "38 class-2" in row_html


def test_metric_catalog_raises_when_a_class_2_metric_has_no_coverage_columns(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "901.sql").write_text(
        "-- name: Hypothetical class-2 metric with no coverage figure (synthetic, issue #129 fixture)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_dashboard raises "
        "CoverageDenominatorMissing when a class-2 metric's view lacks the four coverage-"
        "denominator columns; not a real shipped metric\n"
        "SELECT 5 AS event_count\n",
        encoding="utf-8",
    )
    with pytest.raises(CoverageDenominatorMissing):
        render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
