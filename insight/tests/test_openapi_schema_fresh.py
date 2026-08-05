# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #301 [E16.S3], .sdlc/plans/301.md Decision (a) and (e).

Two independent guards on the committed `insight/web/openapi.json`:

1. `test_committed_openapi_json_matches_the_live_app_schema` -- the freshness half of "generated,
   not hand-edited": a Pydantic model edit (a field added, renamed, or removed) that isn't
   followed by re-running `python3 -m insight.api.export_openapi` fails HERE, loudly, in the
   `insight` CI job (which already installs fastapi/pydantic) -- not silently drifted from what
   the frontend actually type-checks against. Dict equality, not a byte-for-byte text diff: the
   committed file's `indent=2, sort_keys=True` formatting is export_openapi.py's own concern, not
   this test's.

2. `test_label_field_has_no_wire_alias` -- the guard that keeps
   `insight/web/scripts/prove-metric-contract-safety.mjs`'s criterion-2 scenario (a JSON-level
   `label` -> `title` rename standing in for a real Pydantic field rename) honest. That script
   mutates the WIRE property name directly; that is only a faithful stand-in for "someone renamed
   `MetricBase.label` in Python" if `label`'s Python name and wire name are the same today --
   i.e. `label` carries no `Field(alias=...)`, unlike `reliability_class`
   (`Field(alias="reliabilityClass")`) two lines below it in insight/api/models.py. If a future
   goal ever aliases `label`, this test fails first and names exactly why the JS proof would stop
   being faithful.
"""
import pytest

fastapi = pytest.importorskip("fastapi")

import json  # noqa: E402
import pathlib  # noqa: E402

from insight.api.export_openapi import OUT, build_schema  # noqa: E402
from insight.api.models import MetricBase  # noqa: E402


def test_committed_openapi_json_matches_the_live_app_schema():
    assert OUT.is_file(), (
        f"{OUT} does not exist -- run `python3 -m insight.api.export_openapi` from the repo root"
    )
    committed = json.loads(OUT.read_text(encoding="utf-8"))
    live = build_schema()
    assert committed == live, (
        "insight/web/openapi.json is stale -- a Pydantic model changed without re-running "
        "`python3 -m insight.api.export_openapi`"
    )


def test_label_field_has_no_wire_alias():
    field = MetricBase.model_fields["label"]
    assert field.alias is None, (
        "MetricBase.label now carries a Field(alias=...) -- "
        "insight/web/scripts/prove-metric-contract-safety.mjs's criterion-2 scenario mutates the "
        "WIRE property name directly and assumes it equals the Python name `label`; an alias "
        "here means that JSON-level rename no longer stands in faithfully for a real Pydantic "
        "field rename, and the proof script needs to mutate the alias instead"
    )


def test_main_writes_build_schema_to_out(tmp_path, monkeypatch):
    """Covers `main()` directly, the one piece of export_openapi.py the freshness test above
    doesn't exercise (that test reads the real committed OUT, never calls main() to produce it).
    Monkeypatches the module's OUT attribute to a tmp path -- the same style
    insight/tests/test_verify_web.py's `_world()` uses for insight/verify_web.py's own
    module-level WEB/PACKAGE_JSON -- so this never touches the real committed
    insight/web/openapi.json."""
    import insight.api.export_openapi as export_openapi

    scratch = tmp_path / "openapi.json"
    monkeypatch.setattr(export_openapi, "OUT", scratch)
    export_openapi.main()
    assert json.loads(scratch.read_text(encoding="utf-8")) == build_schema()
    assert scratch.read_text(encoding="utf-8").endswith("\n")


def test_export_openapi_out_path_is_insight_web_openapi_json():
    """A cheap direct pin on OUT itself (not exercised by the two tests above, which only read
    through it) -- if a future edit ever points it somewhere else, `npm run typecheck`'s
    `check-schema-fresh.mjs` and this file's own freshness test would start reading two different
    files and silently stop meaning anything together."""
    assert OUT == pathlib.Path(__file__).resolve().parents[1] / "web" / "openapi.json"
