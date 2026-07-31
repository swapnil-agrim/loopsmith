-- name: Rework ratio (proxy)
-- question: How much building is re-building?
-- personas: manager, leadership, cross-functional
-- reliability_class: 1
-- proxy: true
-- guardrail: counts DISTINCT FILES touched by more than one non-merge commit in the alignment-collect window as a fraction of all files touched in that window (rework_ratio = files_touched_more_than_once / total_files_touched) -- chosen because the metric's own catalog name is "ratio", a bounded [0,1] proportion, not a raw magnitude (json_array_length alone, or sum(changes), are both unbounded counts that do not answer "ratio" and conflate window size with rework). CANNOT TELL YOU whether a re-touch was genuine rework (a redo/fix) or ordinary incremental feature work spread across many commits touching the same architecturally-central file (e.g. store.py/__main__.py/packs.py in this repo, touched by nearly every insight PR structurally, not because anything was undone) -- the collector counts commit-touch frequency with no awareness of diff size, commit intent, or whether a change was actually reverted. A file rewritten entirely in one commit counts identically to a file touched by five one-line fixup commits. EMPTY-WINDOW HANDLING (fixed post-review): a pack whose churn_hotspots is [] (a normal state -- alignment-collect.sh emits it whenever the window's non-merge commit count is genuinely 0, via its ordinary code path, not the no_git fail-open path) previously vanished entirely from this view because the final SELECT was driven by the unnested CTE; the packs row is now the LEFT-hand side of the join, so an empty-hotspots pack yields one real row with total_files_touched=0 and rework_ratio=NULL (measured: zero collector entries -- not "never ingested"), matching this repo's own precedent for fact_goal/fact_merge_lead_time absences. NOT A MECHANICAL FLOOR, CORRECTED POST-REVIEW: an earlier draft of this guardrail claimed a window with <=1 commit "forces rework_ratio=0.0 by construction" -- demonstrated FALSE by an independent, author-blind review and pinned by a falsifying test (test_metric_20_rework_ratio.py's window_commit_count_of_1_with_a_changes_5_hotspot_is_not_floored_to_zero): this SQL never reads window_commit_count when computing files_touched_more_than_once/total_files_touched -- rework_ratio is derived ENTIRELY from churn_hotspots, so a pack row with window_commit_count=1 and a single hotspot entry claiming changes=5 yields rework_ratio=1.0, not 0.0. Any floor that DOES hold is a property of the upstream collector (alignment-collect.sh keeping window_commit_count and each hotspot's changes count mutually consistent, since one commit cannot literally touch one file twice) -- NOT a property this view enforces, checks, or can rely on. This view faithfully renders whatever ratio the payload implies, even when window_commit_count contradicts it; a consumer cannot use window_commit_count<=1 as a trustworthy signal that rework_ratio must be 0. RENAME FRAGMENTATION (pre-existing collector limitation, not introduced here): the collector keys purely on a commit's post-rename path, so a file genuinely reworked across a git mv appears as two separate one-touch entries under two names -- undercounting exactly the case this metric exists to catch. Render adjacent to #1 (throughput) per the spec's throughput/quality pairing rule, same as #5. Exact implement-re-entry count needs the emitter (spec's own words, CONV) -- not built here.
WITH packs AS (
    SELECT
        ROW_NUMBER() OVER () AS pack_id,
        collected_ts,
        window_commit_count,
        json_extract(raw_payload, '$.dimensions.d3.churn_hotspots') AS churn_hotspots_json
    FROM fact_collector_pack
    WHERE schema = 'alignment-collect/v1'
),
hotspots AS (
    SELECT
        p.pack_id,
        unnest(
            from_json(p.churn_hotspots_json, '[{"file":"VARCHAR","changes":"INTEGER"}]')
        ) AS hotspot
    FROM packs p
)
SELECT
    p.collected_ts,
    p.window_commit_count,
    count(h.hotspot) FILTER (WHERE h.hotspot.changes > 1) AS files_touched_more_than_once,
    count(h.hotspot) AS total_files_touched,
    ROUND(
        count(h.hotspot) FILTER (WHERE h.hotspot.changes > 1) * 1.0 / NULLIF(count(h.hotspot), 0),
        4
    ) AS rework_ratio
FROM packs p
LEFT JOIN hotspots h ON h.pack_id = p.pack_id
GROUP BY p.pack_id, p.collected_ts, p.window_commit_count
