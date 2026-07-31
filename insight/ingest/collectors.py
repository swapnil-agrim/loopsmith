# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Layer 1 of the collector adapter (issue #100, E1.S2): how to invoke each collector and
classify what came back. See .sdlc/plans/100.md §A for why this is split from packs.py.

Zero DuckDB here — pure subprocess/json/pathlib — so every test in test_collectors.py runs on
any box, with or without duckdb installed, and contributes real coverage regardless.
"""
import collections
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

#: One hung collector must not hang ingest forever. Mirrors pipeline.py's own
#: _CHECK_TIMEOUT_SECS (skills/sdlc-loop/scripts/pipeline.py:36).
_TIMEOUT_SECS = 300

#: Distinguishes "no schema key" from "schema key present but not a string". None won't do:
#: a collector emitting {"schema": null} is the invalid case, not the missing one.
_MISSING = object()

Source = collections.namedtuple(
    "Source",
    "name expected_schema script_rel_parts build_argv uses_json_file ok_exit_codes absent_exit_codes",
)


def _alignment_argv(script, project_root, json_path):
    # ponytail: --since-days 1 matches the script's own default and a daily ingest cadence.
    # It is a real ceiling: run ingest less often than daily and commits older than a day
    # never enter any window. Widen (or plumb a flag) when a cadence is actually chosen.
    return ["bash", str(script), "--since-days", "1"]


def _discovery_argv(script, project_root, json_path):
    return ["bash", str(script)]


def _pipeline_card_argv(script, project_root, json_path):
    return [sys.executable, str(script), "card", str(project_root / ".sdlc"), "--json", str(json_path)]


#: The three collectors from spec §B.3.1. Path parts are relative to a "collectors root" —
#: see resolve_collectors_root — and mirror skills/<skill>/scripts/<file>.sh exactly.
SOURCES = (
    Source("alignment-collect", "alignment-collect/v1",
           ("sdlc-align", "scripts", "alignment-collect.sh"),
           _alignment_argv, False, frozenset({0}), frozenset()),
    Source("discovery-scan", "discovery-scan/v1",
           ("sdlc-loop", "scripts", "discovery-scan.sh"),
           _discovery_argv, False, frozenset({0}), frozenset()),
    # pipeline.py card: exit 0 (all stages pass) or 1 (some stage FAILs) both mean "the card was
    # built successfully and --json was written" (skills/sdlc-loop/scripts/pipeline.py:274-278) —
    # only 3 means ABSENT (no .sdlc/pipeline.json declared).
    Source("pipeline-card", "pipeline-card/v1",
           ("sdlc-loop", "scripts", "pipeline.py"),
           _pipeline_card_argv, True, frozenset({0, 1}), frozenset({3})),
)


def resolve_collectors_root(explicit=None):
    """Precedence: explicit (the --collectors-root CLI flag) > $CLAUDE_PLUGIN_ROOT/skills (set by
    the host only inside a live Claude Code session) > ./skills relative to CWD (the dev-monorepo
    checkout, where insight/ and skills/ are siblings) > None. None is a normal, non-fatal outcome
    — see run_source, which degrades every source individually instead of raising."""
    if explicit is not None:
        p = pathlib.Path(explicit)
        return p if p.is_dir() else None
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        p = pathlib.Path(plugin_root) / "skills"
        if p.is_dir():
            return p
    cwd_skills = pathlib.Path.cwd() / "skills"
    if cwd_skills.is_dir():
        return cwd_skills
    return None


def run_source(source, project_root, collectors_root, timeout=_TIMEOUT_SECS):
    """Locate, invoke, and parse ONE collector. Never raises: every failure mode is reported as
    degraded_adapter codes instead. Returns {'schema', 'payload', 'degraded_adapter'}."""
    if collectors_root is None:
        return {"schema": source.expected_schema, "payload": None,
                "degraded_adapter": ["adapter_collector_not_found"]}
    script = collectors_root.joinpath(*source.script_rel_parts)
    if not script.is_file():
        return {"schema": source.expected_schema, "payload": None,
                "degraded_adapter": ["adapter_collector_not_found"]}

    tmp_dir = tempfile.mkdtemp(prefix="insight-collector-") if source.uses_json_file else None
    try:
        json_path = pathlib.Path(tmp_dir) / "card.json" if tmp_dir else None
        argv = source.build_argv(script, project_root, json_path)
        try:
            proc = subprocess.run(
                # errors="replace": a collector emitting non-UTF-8 bytes would otherwise raise
                # UnicodeDecodeError out of subprocess.run itself, before any guard here. Garbage
                # bytes should fail as "not JSON", which is a recorded degradation.
                argv, cwd=str(project_root), capture_output=True, text=True, errors="replace",
                timeout=timeout,
                env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_root)},
            )
        except subprocess.TimeoutExpired:
            # Distinct from a nonzero exit: neither of these produced an exit code at all, and a
            # collector that HANGS and one that cannot be spawned are different things to go fix.
            return {"schema": source.expected_schema, "payload": None,
                    "degraded_adapter": ["adapter_timeout"]}
        except OSError:
            return {"schema": source.expected_schema, "payload": None,
                    "degraded_adapter": ["adapter_spawn_failed"]}

        if proc.returncode in source.absent_exit_codes:
            return {"schema": source.expected_schema, "payload": None,
                    "degraded_adapter": ["adapter_pipeline_absent"]}
        if proc.returncode not in source.ok_exit_codes:
            return {"schema": source.expected_schema, "payload": None,
                    "degraded_adapter": ["adapter_exit_nonzero"]}

        try:
            raw_text = json_path.read_text(encoding="utf-8") if source.uses_json_file else proc.stdout
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("collector output is not a JSON object")
        except (OSError, ValueError):
            return {"schema": source.expected_schema, "payload": None,
                    "degraded_adapter": ["adapter_output_not_json"]}

        if source.uses_json_file:
            # pipeline.py card's own JSON has no "schema" key at all — build_card() returns only
            # pipeline/stages/gating/verdict (skills/sdlc-loop/scripts/pipeline.py:59-92).
            # Synthesised here; never claimed as collector-emitted.
            payload.setdefault("schema", source.expected_schema)

        # A stdout collector that stops emitting `schema` would otherwise be filed silently
        # under its expected schema — the one case where "trusted from the collector's own
        # JSON" quietly becomes "assumed". None of the three do this today; say so out loud
        # if one ever starts, rather than mis-attributing the pack.
        #
        # The isinstance check is load-bearing, not defensive noise: the schema string is used
        # downstream as a DICT KEY (packs._NORMALIZERS), so a collector emitting an object or
        # array there would raise TypeError: unhashable type — out of ingest entirely.
        found = payload.get("schema", _MISSING)
        if isinstance(found, str):
            return {"schema": found, "payload": payload, "degraded_adapter": []}
        code = "adapter_schema_missing" if found is _MISSING else "adapter_schema_invalid"
        return {"schema": source.expected_schema, "payload": payload, "degraded_adapter": [code]}
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
