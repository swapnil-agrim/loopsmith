-- name: Flow distribution
-- question: New capability vs debt vs risk -- what kind of work is actually in flight?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: source coverage depends on the adopter's goal-frontmatter discipline -- on this repo's own real data, source is 0/19 populated (#109 research dossier), so every real goal buckets under a single NULL-source group today; render lane's own distribution alongside source rather than assuming source is always meaningful.
SELECT
    source,
    lane,
    count(*) AS goal_count,
    ROUND(count(*) * 1.0 / SUM(count(*)) OVER (), 4) AS share
FROM fact_goal
GROUP BY source, lane
