# insight/api/

FastAPI service — a thin transport over the analytics core (`insight.ingest`, `insight.metrics`,
`insight.gaps`), giving the metric-absence contract (design spec §3) runtime teeth via Pydantic.
Nothing lives here yet; **E16.S1** authors the FastAPI skeleton and `/health` endpoint.

Python source under this directory carries the same BUSL marker as the rest of `insight/` — see
`insight/HEADER.txt` and `insight/README.md`. No new marker convention is needed for `.py` here.

## A packaging trap E16.S1 must not walk into

`insight/pyproject.toml:35`'s `packages` list is an explicit allowlist —
`["insight", "insight.ingest", "insight.metrics", "insight.gaps", "insight.dash"]` — not a glob and
not "everything under insight/". There is no `insight.api` entry. The first real
`insight/api/__init__.py` will sit in source, import cleanly under `pip install -e insight/` (which
never consults `packages`), and then be silently absent from a real `pip install`-built wheel: the
same bug class already hit and documented for `insight.metrics`'s SQL fixtures (issue #108),
`insight.gaps`'s SQL fixtures (issue #116), and `insight.dash`'s font licence files (issue #262) —
each time a subpackage or asset landed without a matching entry in this same allowlist, and a
built-wheel install (not the editable install CI actually exercises) is the only thing that would
have caught it. E16.S1 must add `"insight.api"` to `packages` in the same commit that adds
`insight/api/__init__.py`.
