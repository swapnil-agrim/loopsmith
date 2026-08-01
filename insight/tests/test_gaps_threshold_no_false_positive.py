# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #119's own Task 5. Two proofs: (1) a perfectly flat series never fires, at any sample
size, provably (quantile_cont of N copies of an identical value equals that value, so `>` can
never hold -- a degenerate but true fact, not representative of a realistic noisy series, named
as such per .sdlc/plans/119.md's own caution against over-reading a flat fixture -- see that
plan's own 'a perfectly flat step-up can fail to fire at all' edge case, the same root cause read
from the opposite, wanted direction here). (2) the honest measurement of a REALISTIC (noisy)
healthy series: 500 trials/shape, three shapes, matching the methodology .sdlc/plans/119.md's
own 'Measured numbers' section used live. Thresholds below are set with generous margin above the
measured numbers (20.0/11.2/7.4% at the plan's own canonical seeds) so this is a real regression
guard, not a coin flip -- if a future change to the rule's own SQL pushes the fire rate back
toward the 90-100%/78-93% rates every earlier, rejected criterion produced, this test fails
loudly rather than silently."""
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


def test_a_perfectly_flat_slow_but_stable_series_never_fires(conn):
    """Seven measured merges, one project, every single one exactly 432000 seconds (5 days) --
    zero variance. Hand-computed and verified live: every row from the 2nd measured merge onward
    has trailing_p85 == 432000.0 == its own lead_time_seconds, and `>` is strict, so no row ever
    breaches -- population 6, evidence []. Degenerate, not representative -- see part 2, below,
    for the realistic measurement."""
    registry = load_gap_rules()
    rule = registry["threshold_lead_time_breach"]
    for i in range(7):
        conn.execute(
            "INSERT INTO fact_merge_lead_time (project_id, merge_sha, merge_ts, "
            "lead_time_seconds) VALUES (?, ?, ?, ?)",
            ["p1", f"s{i}", datetime.datetime(2026, 1, 1) + datetime.timedelta(days=2 * i),
             432000],
        )
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Threshold", "metric": "3", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


@pytest.mark.parametrize(
    "n,cv,seed,max_fire_rate",
    [
        (30, 0.20, 202601, 0.35),
        (10, 0.10, 202602, 0.25),
        (7, 0.05, 202603, 0.20),
    ],
)
def test_realistic_noisy_healthy_series_fires_rarely_measured_not_assumed(
    conn, n, cv, seed, max_fire_rate
):
    """500 independently-seeded synthetic projects per shape, Normal(432000, 432000*cv) clipped
    positive, the same three shapes and generator .sdlc/plans/119.md's own 'Measured numbers'
    section used live. This session's own canonical run at these exact seeds: N=30/cv=.20 ->
    100/500 (20.0%); N=10/cv=.10 -> 56/500 (11.2%); N=7/cv=.05 -> 37/500 (7.4%) -- all measured
    via this exact query, through evaluate_rule, not a hand-rolled reimplementation. Thresholds
    here are set well above those measured numbers (generous margin against Monte Carlo wobble
    across duckdb patch versions) but FAR below the 90-100%/78-93% rates every earlier, rejected
    criterion produced -- a real regression guard, not a rubber stamp."""
    registry = load_gap_rules()
    rule = registry["threshold_lead_time_breach"]
    rng = random.Random(seed)
    trials = 500
    rows = []
    for t in range(trials):
        for i in range(n):
            value = max(1, round(rng.gauss(432000, 432000 * cv)))
            rows.append((f"t{t}", f"s{i:03d}", datetime.datetime(2026, 1, 1) +
                          datetime.timedelta(days=i), value))
    conn.executemany(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, merge_ts, "
        "lead_time_seconds) VALUES (?, ?, ?, ?)",
        rows,
    )
    finding = evaluate_rule(conn, rule)
    fired_projects = {e["project_id"] for e in finding["evidence"]}
    fire_rate = len(fired_projects) / trials
    assert fire_rate < max_fire_rate, (
        f"N={n} cv={cv}: {len(fired_projects)}/{trials} ({fire_rate*100:.1f}%) synthetic "
        f"HEALTHY projects fired at least one WARN -- expected well under "
        f"{max_fire_rate*100:.0f}%. If this creeps back toward 78-100%, the run-length filter "
        "has regressed toward one of the earlier, rejected criteria."
    )
