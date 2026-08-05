# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Regression guard on the CATALOG hoist (issue #300 [E16.S2], Decision (a)).

`CATALOG` (the 42-metric id -> label mapping) moves from `insight.dash.panel` to
`insight.metrics.catalog`, its natural shared home now that `insight.api` needs it too.
`panel.py` keeps working by importing the name rather than defining it -- this file pins that
the re-export is real (the same object, not a copy) so a future edit that reintroduces a second,
drifting CATALOG in panel.py fails loudly here rather than silently."""
from insight.dash.panel import CATALOG as PANEL_CATALOG
from insight.metrics.catalog import CATALOG


def test_catalog_has_42_entries():
    assert len(CATALOG) == 42


def test_panel_reexports_the_same_catalog_object():
    assert PANEL_CATALOG is CATALOG
