# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""insight.api: the FastAPI service (issue #299, E16.S1) -- a thin transport over the analytics
core (insight.ingest, insight.metrics, insight.gaps), giving the metric-absence contract (design
spec §3) runtime teeth via Pydantic (E16.S2). See insight/api/README.md for the /health contract
and insight/api/app.py for the app factory."""
