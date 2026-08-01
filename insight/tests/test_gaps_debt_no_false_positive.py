# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #121's own Design decision 2. Two proofs: (1) a perfectly flat integer series never
fires, at any sample size, provably (quantile_cont of N copies of an identical value equals that
value, so `>` can never hold). (2) the honest measurement of a REALISTIC (i.i.d.-noisy, NOT a
random walk) healthy series: 500 trials/shape, four shapes, matching
test_gaps_threshold_no_false_positive.py's own methodology. Thresholds below are set with
generous margin above the measured numbers (16.6-18.2/7.6-10.4/7.0/10.0% at this plan's own
canonical seeds) so this is a real regression guard, not a coin flip -- if a future change to
this rule's own SQL pushes the fire rate back toward the naive/LAG-only rates every earlier,
rejected criterion produced (86-100%/42-51%), this test fails loudly rather than silently."""
import datetime
import random

import pytest

from insight.gaps.evaluate import evaluate_rule
from insight.gaps.loader import load_gap_rules


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_a_perfectly_flat_series_never_fires(conn):
    """Ten snapshots, one project, every single one exactly 20 candidates -- zero variance.
    Degenerate, not representative -- see the parametrized case below for the realistic
    measurement."""
    registry = load_gap_rules()
    rule = registry["debt_discovery_scan_rising"]
    for i in range(10):
        conn.execute(
            "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, "
            "degraded_adapter, raw_payload) VALUES ('p1', 'discovery-scan/v1', ?, [], "
            "'{\"schema\":\"discovery-scan/v1\",\"candidates\":[" +
            ",".join('{"title":"x"}' for _ in range(20)) + "]}')",
            [datetime.date(2026, 1, 1) + datetime.timedelta(days=i)],
        )
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Debt", "metric": "30", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


@pytest.mark.parametrize(
    "n,baseline,sigma,seed,max_fire_rate",
    [
        (30, 20, 4.0, 202601, 0.35),
        (10, 20, 2.0, 202602, 0.25),
        (7, 20, 1.0, 202603, 0.20),
        (15, 5, 1.5, 202604, 0.25),
    ],
)
def test_realistic_noisy_healthy_series_fires_rarely_measured_not_assumed(
    conn, n, baseline, sigma, seed, max_fire_rate
):
    """500 independently-seeded synthetic healthy projects per shape, i.i.d.
    Normal(baseline, sigma) per snapshot, clipped non-negative, rounded -- NOT a random walk (see
    .sdlc/plans/121.md Design decision 2 for why that distinction matters and what it changes).
    RE-MEASURED AT REVIEW by instrumenting this very test and reading its own counters:
    n=30 -> 92/500 (18.4%); n=10 -> 41/500 (8.2%); n=7 -> 19/500 (3.8%);
    n=15,baseline=5 -> 53/500 (10.6%). An earlier revision of this docstring reported
    83/52/35/50; not one of those four reproduced against the shipped SQL. The generated data
    was never in question -- the naive criterion still measures 99-100% on the identical series --
    so the divergence was in evaluation, and the numbers are corrected here rather than
    re-explained. They are the justification for the run-of-3 requirement, so they have to be
    the real ones. Thresholds here
    are set well above those measured numbers (margin against Monte Carlo wobble across duckdb
    patch versions) but far below the 86-100%/42-51% every earlier, rejected criterion
    produced -- a real regression guard, not a rubber stamp."""
    registry = load_gap_rules()
    rule = registry["debt_discovery_scan_rising"]
    rng = random.Random(seed)
    trials = 500
    rows = []
    for t in range(trials):
        for i in range(n):
            count = max(0, round(rng.gauss(baseline, sigma)))
            payload = ('{"schema":"discovery-scan/v1","candidates":[' +
                       ",".join('{"title":"x"}' for _ in range(count)) + "]}")
            ts = datetime.date(2026, 1, 1) + datetime.timedelta(days=i)
            rows.append((f"t{t}", ts, payload))
    conn.executemany(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, degraded_adapter, "
        "raw_payload) VALUES (?, 'discovery-scan/v1', ?, [], ?)",
        rows,
    )
    finding = evaluate_rule(conn, rule)
    fired_projects = {e["project_id"] for e in finding["evidence"]}
    fire_rate = len(fired_projects) / trials
    assert fire_rate < max_fire_rate, (
        f"n={n}: {len(fired_projects)}/{trials} ({fire_rate*100:.1f}%) synthetic HEALTHY "
        f"projects fired at least one WARN -- expected well under {max_fire_rate*100:.0f}%. If "
        "this creeps back toward 86-100%, the trailing-p85 baseline has regressed toward one of "
        "the rejected criteria."
    )
