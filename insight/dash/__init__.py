# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The dashboard: static site shell (issue #124, E4.S1) -- `insight dash` builds one
self-contained `index.html` (insight/dash/render.py) from the real metric catalog + gap findings
report, optionally served over loopback HTTP (insight/dash/serve.py). Chart primitives (S2) and
persona-specific views (S3/S4) land on top of this shell in later stories, not yet here.
"""
