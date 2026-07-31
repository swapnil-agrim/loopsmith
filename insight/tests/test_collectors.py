# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.collectors (issue #100): invocation + failure classification only.
No duckdb import anywhere in this file — these tests run and count toward coverage on any box."""
import json
import os
import pathlib
import stat

import pytest

from insight.ingest import collectors

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# --------------------------------------------------------------------------- resolve_collectors_root

def test_explicit_flag_wins_over_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))  # would resolve if honored
    explicit = tmp_path / "explicit-skills"
    explicit.mkdir()
    assert collectors.resolve_collectors_root(str(explicit)) == explicit


def test_explicit_flag_to_a_missing_dir_is_none(tmp_path):
    assert collectors.resolve_collectors_root(str(tmp_path / "nope")) is None


def test_plugin_root_env_used_when_no_explicit_flag(tmp_path, monkeypatch):
    (tmp_path / "skills").mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    assert collectors.resolve_collectors_root(None) == tmp_path / "skills"


def test_cwd_skills_used_when_no_flag_and_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    assert collectors.resolve_collectors_root(None) == tmp_path / "skills"


def test_none_when_nothing_resolves(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)  # empty tmp_path, no ./skills
    assert collectors.resolve_collectors_root(None) is None


# --------------------------------------------------------------------------- run_source: not found

def test_run_source_none_root_degrades_not_found(tmp_path):
    result = collectors.run_source(collectors.SOURCES[0], tmp_path, None)
    assert result == {"schema": "alignment-collect/v1", "payload": None,
                       "degraded_adapter": ["adapter_collector_not_found"]}


def test_run_source_missing_script_degrades_not_found(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    result = collectors.run_source(collectors.SOURCES[1], tmp_path, root)
    assert result["degraded_adapter"] == ["adapter_collector_not_found"]
    assert result["payload"] is None


# --------------------------------------------------------------------------- run_source: stdout-based happy path + failures

def test_run_source_alignment_collect_happy_path(tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                   '#!/bin/sh\necho \'{"schema":"alignment-collect/v1","window":{"since_days":1,'
                   '"oldest":{"sha":"a","date":"2026-01-01"},"newest":{"sha":"b","date":"2026-01-02"},'
                   '"commit_count":3},"degraded":["no_test_command"],"commits":[],"dimensions":{}}\'\n')
    result = collectors.run_source(collectors.SOURCES[0], tmp_path, root)
    assert result["schema"] == "alignment-collect/v1"
    assert result["degraded_adapter"] == []
    assert result["payload"]["degraded"] == ["no_test_command"]


def test_run_source_stdout_json_without_schema_key_is_flagged_not_assumed(tmp_path):
    # A stdout collector that stops emitting `schema` still gets filed under its expected
    # schema — there is nothing better to file it under — but says so, so the pack is never
    # silently mis-attributed to a contract the collector no longer honours.
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                   '#!/bin/sh\necho \'{"candidates":[]}\'\n')
    result = collectors.run_source(collectors.SOURCES[1], tmp_path, root)
    assert result["schema"] == "discovery-scan/v1"
    assert result["degraded_adapter"] == ["adapter_schema_missing"]
    assert result["payload"] == {"candidates": []}


def test_run_source_exit_nonzero_degrades(tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh", "#!/bin/sh\nexit 7\n")
    result = collectors.run_source(collectors.SOURCES[1], tmp_path, root)
    assert result == {"schema": "discovery-scan/v1", "payload": None,
                       "degraded_adapter": ["adapter_exit_nonzero"]}


def test_run_source_output_not_json_degrades(tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                   "#!/bin/sh\necho 'not json at all'\n")
    result = collectors.run_source(collectors.SOURCES[0], tmp_path, root)
    assert result["degraded_adapter"] == ["adapter_output_not_json"]


def test_run_source_json_array_not_object_degrades_not_json(tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh", "#!/bin/sh\necho '[1,2,3]'\n")
    result = collectors.run_source(collectors.SOURCES[1], tmp_path, root)
    assert result["degraded_adapter"] == ["adapter_output_not_json"]


def test_run_source_execute_failure_degrades_exit_nonzero(tmp_path):
    """A synthetic Source whose argv names a binary that cannot exist — exercises the OSError
    branch deterministically, independent of any real interpreter's presence/absence."""
    bogus = collectors.Source(
        "bogus", "bogus/v1", ("nope.sh",),
        lambda script, project_root, json_path: ["/definitely/does/not/exist/bin-xyz"],
        False, frozenset({0}), frozenset(),
    )
    root = tmp_path / "skills"
    _write_script(root / "nope.sh", "#!/bin/sh\ntrue\n")
    result = collectors.run_source(bogus, tmp_path, root)
    assert result["degraded_adapter"] == ["adapter_exit_nonzero"]


def test_run_source_timeout_degrades_exit_nonzero(tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh", "#!/bin/sh\nsleep 5\n")
    result = collectors.run_source(collectors.SOURCES[0], tmp_path, root, timeout=0.2)
    assert result["degraded_adapter"] == ["adapter_exit_nonzero"]


# --------------------------------------------------------------------------- run_source: --json-file based (pipeline-card)

def test_run_source_pipeline_card_happy_path_stamps_synthetic_schema(tmp_path):
    root = tmp_path / "skills"
    _write_script(
        root / "sdlc-loop" / "scripts" / "pipeline.py",
        "import sys, json\n"
        "out = sys.argv[sys.argv.index('--json') + 1]\n"
        "open(out, 'w').write(json.dumps({'pipeline': 'p', 'stages': [], 'gating': 'none', "
        "'verdict': {'failing_stages': []}}))\n"
        "print('card rendered')\n",
    )
    result = collectors.run_source(collectors.SOURCES[2], tmp_path, root)
    assert result["schema"] == "pipeline-card/v1"  # synthesised, not read from stdout/file
    assert result["payload"]["pipeline"] == "p"
    assert result["degraded_adapter"] == []


def test_run_source_pipeline_card_absent_is_exit_3_not_a_failure(tmp_path):
    root = tmp_path / "skills"
    _write_script(
        root / "sdlc-loop" / "scripts" / "pipeline.py",
        "import sys\nprint('NO-PIPELINE', file=sys.stderr)\nsys.exit(3)\n",
    )
    result = collectors.run_source(collectors.SOURCES[2], tmp_path, root)
    assert result == {"schema": "pipeline-card/v1", "payload": None,
                       "degraded_adapter": ["adapter_pipeline_absent"]}


def test_run_source_pipeline_card_exit_1_is_still_success(tmp_path):
    """exit 1 = a stage FAILed, but the card was still built and written — not a degrade."""
    root = tmp_path / "skills"
    _write_script(
        root / "sdlc-loop" / "scripts" / "pipeline.py",
        "import sys, json\n"
        "out = sys.argv[sys.argv.index('--json') + 1]\n"
        "open(out, 'w').write(json.dumps({'stages': [], 'verdict': {'failing_stages': ['x']}}))\n"
        "sys.exit(1)\n",
    )
    result = collectors.run_source(collectors.SOURCES[2], tmp_path, root)
    assert result["degraded_adapter"] == []
    assert result["payload"]["verdict"]["failing_stages"] == ["x"]


# --------------------------------------------------------------------------- real-script integration (one per collector)
#
# These invoke the ACTUAL scripts under this repo's own skills/ tree (found the same way
# tests/test_import_boundary.py finds ROOT), not a fixed install path — safe because all three
# collectors are documented read-only/deterministic/no-network (verified in issue #100's research).

@pytest.fixture
def real_collectors_root():
    root = REPO_ROOT / "skills"
    if not root.is_dir():
        pytest.skip("skills/ not present in this checkout")
    return root


@pytest.fixture
def git_project(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_real_alignment_collect_runs_end_to_end(real_collectors_root, git_project):
    result = collectors.run_source(collectors.SOURCES[0], git_project, real_collectors_root)
    assert result["schema"] == "alignment-collect/v1"
    assert result["degraded_adapter"] == []
    assert "window" in result["payload"]


def test_real_discovery_scan_runs_end_to_end(real_collectors_root, git_project):
    result = collectors.run_source(collectors.SOURCES[1], git_project, real_collectors_root)
    assert result["schema"] == "discovery-scan/v1"
    assert result["degraded_adapter"] == []
    assert result["payload"]["candidates"] == []


def test_real_pipeline_card_absent_end_to_end(real_collectors_root, git_project):
    """git_project has no .sdlc/pipeline.json — the common case."""
    result = collectors.run_source(collectors.SOURCES[2], git_project, real_collectors_root)
    assert result == {"schema": "pipeline-card/v1", "payload": None,
                       "degraded_adapter": ["adapter_pipeline_absent"]}
