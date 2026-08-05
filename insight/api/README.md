# insight/api/

FastAPI service — a thin transport over the analytics core (`insight.ingest`, `insight.metrics`,
`insight.gaps`), giving the metric-absence contract (design spec §3) runtime teeth via Pydantic.

**E16.S1 (issue #299) and E16.S2 (issue #300) are both implemented.** `insight/api/app.py`
exposes `create_app(db_path=None)` and a module-level `app`, with two routes: `GET /health` and
`GET /metrics`. Both read the DuckDB store read-only through
`insight.ingest.store.open_store_read_only` — no new query layer, no duplicated SQL.

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

## `/metrics`'s contract: the `Metric` union, and what "measured" actually means today

`GET /metrics` returns `list[Metric]`, one entry per catalog id (all 42, sorted), where `Metric`
is `insight.api.models`'s Pydantic discriminated union on `state`:

| `state` | Fields carried | Meaning |
|---|---|---|
| `measured` | `value`, `coverage` (`{numerator, denominator}`) | a real number, with the denominator it was computed over |
| `absent_no_data` | `reason` | the metric's SQL exists but produced nothing yet — time fixes this |
| `absent_unbuilt` | `reason` | nothing can produce this metric yet — only a code change fixes this |

Every field is present with `populate_by_name=True` so both the Python name and the camelCase
wire name (`reliability_class` / `reliabilityClass`) work server-side, but **the wire body itself
is always camelCase** — `reliabilityClass`, never `reliability_class`.

**The absent states carry NO `value` field at all — not `value: null`.** `MetricBase`'s
`extra="forbid"` (inherited by every subclass) means constructing an absent metric with a `value`
kwarg raises `ValidationError`, not "silently accepted or dropped". This is what makes
`GET /metrics` against a cold-start store (fact tables present, empty, no ingest, no dash render)
serialise a body with **no numeral for any metric's value anywhere** — every one of the 42
entries is `absent_no_data` or `absent_unbuilt`, matching this project's founding ABSENT-is-
never-PASS doctrine (spec §3, `/health`'s own contract section above) one layer further down,
now enforced by the type system rather than only stated in a comment.

**Scope limitation, stated here rather than discovered later:** of the 42 catalog metrics, only
**id 12 (Autonomy rate)** has a registered value/coverage extractor
(`insight.api.metrics.VALUE_EXTRACTORS`) — the single concrete, end-to-end proof that `measured`
is real. The other 41 (33 with a built `.sql` file and no extractor registered yet, plus 8 with
no `.sql` file at all) resolve to `absent_unbuilt`. This means the API's "N live" count reads very
differently from the dash panel's own instrumentation board (34 there vs. effectively 1 here)
until each metric gets its own extractor wired — a real follow-up, not a bug in this story. See
`.sdlc/plans/300.md` Decision (b) and Risks for the full reasoning, including why `absent_unbuilt`
deliberately covers both "no SQL file" and "SQL exists, no extractor registered" rather than
inventing a fourth union state for the second case.

Like `/health`, `/metrics` opens the store read-only and never writes — it can only ever report
a `metric_<id>` view that some prior write-mode process (a dash render) already created; an
ingest-only store therefore reports `metric_12` as `absent_no_data` forever even once real data
has landed, until `insight dash` is run at least once against it. See `.sdlc/plans/300.md` Risks
for the full trace.

**One broken metric never erases the other 41.** A `metric_<id>` view whose rows don't match the
shape its extractor indexes degrades *that* metric to `absent_unbuilt` (only a code change fixes
a wrong-shaped `.sql` — ingest never will); the response still returns 200 with all 42 entries.
An endpoint that answers nothing when one metric breaks would be worse than the fabricated `0`
this story replaced: it would erase 41 healthy readings alongside the broken one.

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
