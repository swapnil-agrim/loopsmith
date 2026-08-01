# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the process-level 'no network at build time' guard (issue #124, E4.S1, Decision 4a).
See .sdlc/plans/124.md sections G/M for the live prototyping this pins as a regression test:
`no_network()` was widened TWICE after independent plan review found real evasion routes a
narrower guard missed (round 1: socket.getaddrinfo() / subprocess.Popen-based spawns; round 2:
os.system(), which calls libc's system() directly and never constructs a subprocess.Popen).

`no_network()` itself lives HERE, not in insight/dash/ -- it is test infrastructure, never
shipped/imported by production code, the same footing isolate_path_empty/isolate_path_no_gh
(insight/tests/test_cli.py) already have."""
import contextlib
import os
import socket
import subprocess
import urllib.request

import pytest

from insight.__main__ import main


class NetworkBlockedError(Exception):
    """Raised by no_network()'s patched functions when the guarded code attempts an outbound
    connection, a DNS lookup, a subprocess spawn, or an os.system() call."""


def _blocked_connect(self, *a, **k):
    raise NetworkBlockedError("socket.connect()/connect_ex() called during a render that must "
                               "be network-free")


def _blocked_getaddrinfo(*a, **k):
    raise NetworkBlockedError("socket.getaddrinfo() (DNS resolution) called during a render "
                               "that must be network-free")


def _blocked_popen_init(self, *a, **k):
    raise NetworkBlockedError("subprocess.Popen() called during a render that must be "
                               "network-free (and process-free -- insight.dash.render never "
                               "legitimately shells out)")


def _blocked_system(cmd):
    raise NetworkBlockedError("os.system() called during a render that must be network-free "
                               "(and process-free -- bypasses subprocess.Popen entirely, calls "
                               "libc system() directly)")


@contextlib.contextmanager
def no_network():
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo, real_popen_init, real_system = (
        socket.getaddrinfo, subprocess.Popen.__init__, os.system,
    )
    socket.socket.connect = socket.socket.connect_ex = _blocked_connect
    socket.getaddrinfo = _blocked_getaddrinfo
    subprocess.Popen.__init__ = _blocked_popen_init
    os.system = _blocked_system
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex
        socket.getaddrinfo = real_getaddrinfo
        subprocess.Popen.__init__ = real_popen_init
        os.system = real_system


def _cheating_urlopen():
    urllib.request.urlopen("http://example.com", timeout=1)


def _cheating_os_system():
    # Fixed literal command, no user input -- this fixture exists ONLY to prove no_network()
    # blocks os.system() itself (round-2 plan-review finding, .sdlc/plans/124.md section M);
    # not a code path insight.dash ever exercises for real.
    os.system("true")


def _honest():
    return "ok, no sockets or subprocesses touched"


def test_no_network_blocks_a_cheating_renderer_and_restores_after():
    with no_network():
        with pytest.raises(NetworkBlockedError):
            _cheating_urlopen()
        with pytest.raises(NetworkBlockedError):
            _cheating_os_system()
        assert _honest() == "ok, no sockets or subprocesses touched"

    # restored -- all four patched surfaces are back to their real originals
    assert socket.socket.connect is not _blocked_connect
    assert socket.socket.connect_ex is not _blocked_connect
    assert socket.getaddrinfo is not _blocked_getaddrinfo
    assert subprocess.Popen.__init__ is not _blocked_popen_init
    assert os.system is not _blocked_system


def test_dash_build_makes_no_network_calls_end_to_end(tmp_path, monkeypatch):
    """The load-bearing test: a full, in-process `main(["dash", ...])` call (no --serve -- a
    blocking server is a distinct concern from the *build* step this test targets), wrapped in
    no_network(). Must complete with exit code 0 and a real index.html on disk -- i.e. the
    entire real render path (load_metrics, the _measured() queries, build_report, string
    assembly, the file write) provably makes zero calls of the four kinds no_network() catches."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    with no_network():
        code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "index.html").exists()


def test_dash_build_makes_no_network_calls_against_a_warm_store_too(tmp_path, monkeypatch):
    """Same pattern, against a store seeded with real facts (a dim_project row, a
    fact_merge_lead_time row, a fact_pr_check row) -- proves the guard holds on the *warm* path
    too (real gaps findings, non-trivial per-metric queries), not only the trivially-empty one."""
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    from insight.ingest.store import ensure_schema
    conn = duckdb.connect(str(target))
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\":{\"require_review\":\"approval\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    conn.execute(
        "INSERT INTO fact_pr_check (project_id, pr_number, check_name, conclusion) "
        "VALUES ('p1', 101, 'ci', 'success')"
    )
    conn.close()

    with no_network():
        code = main(["dash", "--db", str(target), "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "index.html").exists()
