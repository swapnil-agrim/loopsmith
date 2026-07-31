-- name: Throughput
-- question: Are we shipping?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: render adjacent to #5 (change-failure rate) per spec Guardrails section; never at individual grain
-- NOTE for every future .sql author imitating this format: each field above is exactly ONE
-- physical line, however long -- there is no continuation syntax. This very line proves it:
-- it starts with '--' but is not 'key: value' shaped, so the header already ended at the
-- guardrail line above and this is ordinary file commentary, not part of guardrail. Caution,
-- though: a trailing comment that IS shaped like 'Word: rest' (a single word then a colon)
-- lands in extra[] as its own field instead of staying ordinary text -- avoid opening a
-- trailing comment that way. See insight/metrics/header.py's module docstring for the
-- full reasoning on both points.
-- DEVIATION from the plan's literal SQL, recorded live: plain `date_trunc('week', terminal_ts)`
-- returns a Python datetime.date under duckdb 1.4.5 but a datetime.datetime under duckdb 1.5.5
-- (both satisfy pyproject.toml's own "duckdb~=1.4" range) -- caught only by running this
-- story's own cross-interpreter proof against a fresh venv that resolved the newer patch.
-- CAST(... AS DATE) pins the view's output type so `metric_1` is stable for any consumer
-- (a dashboard, a test) regardless of which duckdb patch build is installed.
SELECT
    CAST(date_trunc('week', terminal_ts) AS DATE) AS week,
    count(*) AS done_count
FROM fact_goal
WHERE outcome = 'done'
GROUP BY 1
ORDER BY 1
