# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Writes the live FastAPI schema to `insight/web/openapi.json`, committed (issue #301 [E16.S3],
.sdlc/plans/301.md Decision a).

Run by hand, never by CI or `npm run typecheck`: `python3 -m insight.api.export_openapi` (from
the repo root, `insight` importable). This is a deliberate choice, not an oversight -- CI's `web`
job (.github/workflows/ci.yml) has no Python install step, only `actions/checkout` + Node setup,
and it must stay that way. So the schema a frontend type-checks against is this committed file,
never a live `app.openapi()` call reached from Node. The committed file's own freshness (does it
still match the live Pydantic models?) is instead a Python-side test,
insight/tests/test_openapi_schema_fresh.py, run in the `insight` CI job, which already has
fastapi/pydantic installed.
"""
import json
import pathlib

from insight.api.app import app

#: insight/web/openapi.json -- WEB, not HERE, because this file lives in insight/api/, one level
#: below insight/ itself.
OUT = pathlib.Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def build_schema():
    """The live schema, as a dict -- pulled out as its own function so
    test_openapi_schema_fresh.py can call it directly rather than re-deriving `app.openapi()`
    itself, and so main() has exactly one thing to test: does it write what build_schema() built."""
    return app.openapi()


def main():
    # sort_keys=True: a stable byte order so a re-export with no real schema change produces an
    # identical file (no diff noise); indent=2 keeps it human-reviewable in a PR diff, the same
    # convention the plan's Decision (a) specifies.
    text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    OUT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
