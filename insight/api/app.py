# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The FastAPI skeleton (issue #299, E16.S1): create_app() and the /health route.

Reads the DuckDB store read-only through insight.ingest.store -- no new query layer, no
duplicated SQL (Decision 3, .sdlc/plans/299.md). No metric endpoints (E16.S2) and no auth
(spec §5.1.1 -- the API is never given a published port; E22.S1's job) belong here.
"""
from fastapi import FastAPI

from insight.ingest.store import has_any_rows, open_store_read_only


def get_connection(db_path=None):
    """Thin wrapper over insight.ingest.store.open_store_read_only -- this is the exact
    function object the /health route below calls, and the one
    insight/tests/test_api_health.py::test_write_through_the_api_connection_fails imports
    directly to prove the API path opens the store read-only, not merely that store.py's own
    primitive does (Task 3.4)."""
    return open_store_read_only(db_path)


def create_app(db_path=None):
    """Build a FastAPI app with a single /health route. `db_path` is captured by the closure at
    call time (None by default) -- open_store_read_only(None) re-resolves via resolve_db_path/CWD
    on every request, never baked in at import time, matching store.py's own documented contract
    ("resolved relative to CWD at run time, not at import time")."""
    app = FastAPI()

    @app.get("/health")
    def health():
        """Cold-start-safe liveness probe (Decision 1, .sdlc/plans/299.md): HTTP 200 in all
        three store states -- the API PROCESS is genuinely healthy in every one of them (it
        started, it can reach the filesystem, it can respond); a missing or empty store is an
        expected pre-ingest state, not an outage.

        CONTRACT, binding on every caller (plan-review amendment 4 -- this is the ABSENT-is-
        never-PASS doctrine, spec §3, applied one HTTP layer up from where E16.S2's Metric union
        will eventually enforce it in Pydantic): the body's `store` field
        ("missing" | "empty" | "populated") is what carries the real signal. A caller MUST branch
        on `store` and MUST NEVER read HTTP 200 alone as "healthy with data" -- collapsing all
        three into a single `{"status": "ok"}` (or reading 200 as implying data is present) is
        exactly the fake-healthy shape spec §3 opens with (the literal `0` shipped for "goals
        landed" against an empty store). See insight/api/README.md for the full contract.
        """
        try:
            conn = get_connection(db_path)
        except FileNotFoundError:
            return {"store": "missing"}
        try:
            populated = has_any_rows(conn)
        finally:
            conn.close()
        return {"store": "populated" if populated else "empty"}

    return app


app = create_app()
