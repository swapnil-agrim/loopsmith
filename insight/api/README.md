# insight/api/

FastAPI service — a thin transport over the analytics core (`insight.ingest`, `insight.metrics`,
`insight.gaps`), giving the metric-absence contract (design spec §3) runtime teeth via Pydantic.

**E16.S1 (issue #299) is implemented.** `insight/api/app.py` exposes `create_app(db_path=None)`
and a module-level `app`, with one route: `GET /health`. It reads the DuckDB store read-only
through `insight.ingest.store.open_store_read_only` — no new query layer, no duplicated SQL. Next
is **E16.S2**: the `Metric` discriminated union (spec §3) as Pydantic models, and the metric
routes that actually serve numbers.

Python source under this directory carries the same BUSL marker as the rest of `insight/` — see
`insight/HEADER.txt` and `insight/README.md`. No new marker convention is needed for `.py` here.

## `/health`'s contract: what 200-for-every-state means, and does not mean

`/health` returns HTTP `200` in all three store states:

| Store state | Response body |
|---|---|
| `.sdlc/insight.duckdb` does not exist | `{"store": "missing"}` |
| File exists, opens fine, zero rows across every table | `{"store": "empty"}` |
| File exists, opens fine, at least one row somewhere | `{"store": "populated"}` |

The status code stays `200` in every case because the **API process** is genuinely healthy in all
three — it started, it can reach the filesystem, it can respond. A missing or empty store is a
normal, expected pre-ingest state, not an outage; collapsing it into a non-2xx would trip liveness
alerting on every fresh clone.

**This means the status code alone tells a caller nothing about whether there is data.** The
`store` field is where the real signal lives, and it is binding:

- **A caller MUST branch on `store`.**
- **A caller MUST NEVER read HTTP `200` alone as "healthy with data."**

Collapsing all three states into a bare `{"status": "ok"}`, or treating any `200` response as
proof data is present, is exactly the fake-healthy shape this project's own founding incident
warns against (design spec §3's opening example: a literal `0` shipped for "goals landed" against
an empty store, because the absence branch never executed in review). This doctrine — **ABSENT is
never PASS** — is deferred one HTTP layer up from where it will eventually be enforced by the type
system: E16.S2's `Metric` discriminated union encodes it in Pydantic, for actual metric values.
`/health` carries no metric, only a presence marker, but the same discipline applies to reading it.
The same statement is repeated as a comment directly on the `/health` route in `insight/api/app.py`,
so a reader of the code, not only this doc, sees it too.

## A packaging trap E16.S1 walked into for real, and fixed

`insight/pyproject.toml`'s `packages` list is an explicit allowlist, not a glob and not
"everything under insight/". Before E16.S1, there was no `insight.api` entry — the first real
`insight/api/__init__.py` would have sat in source, imported cleanly under `pip install -e
insight/` (which never consults `packages`), and then been silently absent from a real `pip
install`-built wheel: the same bug class already hit and documented for `insight.metrics`'s SQL
fixtures (issue #108), `insight.gaps`'s SQL fixtures (issue #116), `insight.dash`'s font licence
files (issue #262), and `insight.contract`'s golden fixtures (issue #298). E16.S1 added
`"insight.api"` to `packages` **and** `"insight.api" = ["*.md"]` to `package-data` (for this very
README) in the same commit that added `insight/api/__init__.py` — the discipline the four prior
incidents show is missing when the two land separately.
