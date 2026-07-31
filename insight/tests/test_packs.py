# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.packs (issue #100): the schema registry and persistence."""
import json
import pathlib
import stat

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.packs import (  # noqa: E402
    ADAPTER_UNKNOWN_SCHEMA, ingest_collectors, normalize, write_pack,
)
from insight.ingest.store import ensure_schema  # noqa: E402


def _write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- normalize()

def test_normalize_alignment_collect_extracts_window_and_degraded():
    payload = {
        "schema": "alignment-collect/v1",
        "window": {"since_days": 3, "oldest": {"sha": "a", "date": "d1"},
                   "newest": {"sha": "b", "date": "d2"}, "commit_count": 5},
        "degraded": ["no_test_command"],
    }
    fields, extra = normalize("alignment-collect/v1", payload)
    assert fields == {
        "window_since_days": 3, "window_oldest_sha": "a", "window_oldest_date": "d1",
        "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 5,
        "window_merge_count": None, "window_pr_count": None, "window_review_event_count": None,
        "window_check_row_count": None, "degraded_collector": ["no_test_command"],
    }
    assert extra == []


def test_normalize_git_facts_extracts_window_including_merge_count():
    payload = {
        "schema": "git-facts/v1",
        "window": {"since_days": 14, "oldest": {"sha": "a", "date": "d1"},
                   "newest": {"sha": "b", "date": "d2"}, "commit_count": 5, "merge_count": 2},
        "degraded": [],
    }
    fields, extra = normalize("git-facts/v1", payload)
    assert fields == {
        "window_since_days": 14, "window_oldest_sha": "a", "window_oldest_date": "d1",
        "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 5,
        "window_merge_count": 2, "window_pr_count": None, "window_review_event_count": None,
        "window_check_row_count": None, "degraded_collector": [],
    }
    assert extra == []


def test_normalize_git_facts_no_git_is_all_null_with_degraded_code():
    payload = {"schema": "git-facts/v1",
               "window": {"since_days": 14, "oldest": {"sha": None, "date": None},
                          "newest": {"sha": None, "date": None}, "commit_count": None,
                          "merge_count": None},
               "degraded": ["no_git"]}
    fields, extra = normalize("git-facts/v1", payload)
    assert fields["window_commit_count"] is None
    assert fields["window_merge_count"] is None
    assert fields["degraded_collector"] == ["no_git"]
    assert extra == []


def test_normalize_alignment_collect_now_carries_a_null_merge_count():
    """alignment-collect/v1 has no merge concept -- window_merge_count must be present and NULL,
    not simply absent from the fields dict (write_pack now always expects the key -- issue #103)."""
    payload = {"schema": "alignment-collect/v1",
               "window": {"since_days": 3, "oldest": {}, "newest": {}, "commit_count": 5},
               "degraded": []}
    fields, _ = normalize("alignment-collect/v1", payload)
    assert fields["window_merge_count"] is None


def test_write_pack_persists_window_merge_count(conn):
    # NOTE (deviation from .sdlc/plans/104.md's Step 2.1 -- a gap the plan itself missed): this
    # #103-authored test also builds a `fields` dict by hand and was not in the plan's list of
    # three pre-existing tests to update, but write_pack now unconditionally indexes the three
    # new #104 keys too -- found failing with KeyError('window_pr_count') when run as written.
    fields = {"window_since_days": 14, "window_oldest_sha": "a", "window_oldest_date": "d1",
              "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 5,
              "window_merge_count": 2, "window_pr_count": None, "window_review_event_count": None,
              "window_check_row_count": None, "degraded_collector": []}
    write_pack(conn, "proj1", "git-facts/v1", fields, [], '{"schema":"git-facts/v1"}')
    row = conn.execute(
        "select schema, window_commit_count, window_merge_count from fact_collector_pack"
    ).fetchone()
    assert row == ("git-facts/v1", 5, 2)


def test_normalize_gh_facts_extracts_summary_counts():
    payload = {
        "schema": "gh-facts/v1",
        "window": {"since_days": 14, "oldest": {"sha": None, "date": None},
                   "newest": {"sha": None, "date": None}, "commit_count": None,
                   "merge_count": None, "pr_count": 3, "review_event_count": 2,
                   "check_row_count": 8},
        "degraded": [],
    }
    fields, extra = normalize("gh-facts/v1", payload)
    assert fields["window_pr_count"] == 3
    assert fields["window_review_event_count"] == 2
    assert fields["window_check_row_count"] == 8
    assert fields["degraded_collector"] == []
    assert extra == []


def test_normalize_gh_facts_unauthenticated_is_all_null_with_degraded_code():
    payload = {"schema": "gh-facts/v1",
               "window": {"since_days": 14, "oldest": {"sha": None, "date": None},
                          "newest": {"sha": None, "date": None}, "commit_count": None,
                          "merge_count": None, "pr_count": None, "review_event_count": None,
                          "check_row_count": None},
               "degraded": ["gh_unauthenticated"]}
    fields, extra = normalize("gh-facts/v1", payload)
    assert fields["window_pr_count"] is None
    assert fields["degraded_collector"] == ["gh_unauthenticated"]
    assert extra == []


def test_normalize_git_facts_now_carries_null_gh_counts():
    """git-facts/v1 has no PR concept -- the three new gh-facts/v1 columns must be present and
    NULL, not simply absent from the fields dict (write_pack now always expects the keys)."""
    payload = {"schema": "git-facts/v1",
               "window": {"since_days": 14, "oldest": {"sha": "a", "date": "d1"},
                          "newest": {"sha": "b", "date": "d2"}, "commit_count": 5,
                          "merge_count": 1},
               "degraded": []}
    fields, _ = normalize("git-facts/v1", payload)
    assert fields["window_pr_count"] is None
    assert fields["window_review_event_count"] is None
    assert fields["window_check_row_count"] is None


def test_write_pack_persists_gh_facts_counts(conn):
    fields = {"window_since_days": 14, "window_oldest_sha": None, "window_oldest_date": None,
              "window_newest_sha": None, "window_newest_date": None, "window_commit_count": None,
              "window_merge_count": None, "window_pr_count": 3, "window_review_event_count": 2,
              "window_check_row_count": 8, "degraded_collector": []}
    write_pack(conn, "proj1", "gh-facts/v1", fields, [], '{"schema":"gh-facts/v1"}')
    row = conn.execute(
        "select schema, window_pr_count, window_review_event_count, window_check_row_count "
        "from fact_collector_pack"
    ).fetchone()
    assert row == ("gh-facts/v1", 3, 2, 8)


def test_normalize_discovery_scan_is_all_null_window():
    fields, extra = normalize("discovery-scan/v1", {"schema": "discovery-scan/v1", "candidates": []})
    assert fields["window_since_days"] is None
    assert fields["degraded_collector"] == []
    assert extra == []


def test_normalize_none_payload_is_all_null_no_extra_code():
    """payload=None means collectors.run_source already contributed an adapter code — normalize
    must not ALSO add adapter_unknown_schema on top of it."""
    fields, extra = normalize("alignment-collect/v1", None)
    assert fields["degraded_collector"] == []
    assert extra == []


def test_normalize_unknown_schema_returns_unknown_code_and_null_window():
    fields, extra = normalize("totally-unexpected/v1", {"schema": "totally-unexpected/v1"})
    assert extra == [ADAPTER_UNKNOWN_SCHEMA]
    assert fields["window_since_days"] is None
    assert fields["degraded_collector"] == []


# --------------------------------------------------------------------------- write_pack()

def test_write_pack_persists_all_columns(conn):
    fields = {"window_since_days": 1, "window_oldest_sha": "a", "window_oldest_date": "d1",
              "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 2,
              "window_merge_count": None, "window_pr_count": None,
              "window_review_event_count": None, "window_check_row_count": None,
              "degraded_collector": ["no_git"]}
    write_pack(conn, "proj1", "alignment-collect/v1", fields, ["adapter_output_not_json"], '{"x":1}')
    row = conn.execute(
        "select project_id, schema, window_since_days, degraded_collector, degraded_adapter, "
        "raw_payload from fact_collector_pack"
    ).fetchone()
    assert row == ("proj1", "alignment-collect/v1", 1, ["no_git"], ["adapter_output_not_json"], '{"x":1}')


# --------------------------------------------------------------------------- ingest_collectors(): the done_when tests

def test_unknown_schema_is_recorded_and_skipped_not_raised(conn, tmp_path):
    """done_when: 'an unknown schema is recorded and skipped, never fatal.' A fake script stands
    in for alignment-collect and emits a schema nobody registered a normaliser for."""
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                   "#!/bin/sh\necho '{\"schema\":\"bogus-thing/v1\",\"foo\":1}'\n")
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))  # doesn't raise
    bogus = next(r for r in results if r["name"] == "alignment-collect")
    assert bogus["schema"] == "bogus-thing/v1"
    assert bogus["degraded_adapter"] == [ADAPTER_UNKNOWN_SCHEMA]
    row = conn.execute(
        "select schema, degraded_adapter, raw_payload from fact_collector_pack "
        "where schema = 'bogus-thing/v1'"
    ).fetchone()
    assert row == ("bogus-thing/v1", [ADAPTER_UNKNOWN_SCHEMA], '{"schema": "bogus-thing/v1", "foo": 1}')
    # the OTHER two sources still ran and were recorded — one bad schema doesn't abort the loop
    assert len(results) == 3


def test_collector_exit_nonzero_degrades_never_crashes_ingest(conn, tmp_path):
    """done_when: 'a collector that exits non-zero degrades, never crashes ingest.'"""
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh", "#!/bin/sh\nexit 9\n")
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))  # doesn't raise
    scan = next(r for r in results if r["name"] == "discovery-scan")
    assert scan["degraded_adapter"] == ["adapter_exit_nonzero"]
    row = conn.execute(
        "select degraded_adapter, raw_payload from fact_collector_pack where schema = 'discovery-scan/v1'"
    ).fetchone()
    assert row == (["adapter_exit_nonzero"], None)
    assert len(results) == 3


def test_no_collectors_root_degrades_all_three_never_raises(conn, tmp_path):
    results = ingest_collectors(conn, tmp_path, collectors_root=str(tmp_path / "does-not-exist"))
    assert len(results) == 3
    assert all(r["degraded_adapter"] == ["adapter_collector_not_found"] for r in results)
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_ingest_collectors_appends_not_upserts_on_repeated_runs(conn, tmp_path):
    root = tmp_path / "skills"  # nothing there; every source degrades, but still writes a row
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 6  # 3 sources x 2 runs, not deduplicated — this is a log, not a dimension


def test_object_schema_is_recorded_not_crashed(conn, tmp_path):
    # The schema string is used as a dict key downstream, so an object here used to raise
    # TypeError: unhashable type out of ingest entirely — killing the loop, so NO source got a
    # row. Strictly worse than the unknown-schema case the story asks us to survive.
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                  '#!/bin/sh\necho \'{"schema":{"nested":true}}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    assert len(results) == 3  # every source still wrote a row
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_schema_invalid"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_malformed_non_schema_field_is_recorded_not_crashed(conn, tmp_path):
    # A well-typed `schema` gets past every check in collectors.py, so a bad value in any OTHER
    # field only surfaces at the INSERT — window_since_days is INTEGER and DuckDB raises
    # ConversionException on a struct. 100% line coverage did not catch this; only a malformed
    # non-schema field does.
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                  '#!/bin/sh\necho \'{"schema":"alignment-collect/v1","window":'
                  '{"since_days":{"bad":1},"oldest":{},"newest":{},"commit_count":0},'
                  '"degraded":[]}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    assert len(results) == 3
    align = [r for r in results if r["schema"] == "alignment-collect/v1"][0]
    assert align["degraded_adapter"] == ["adapter_internal_error"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3  # the failed INSERT wrote nothing, so the fallback did not duplicate


def test_degraded_as_a_string_is_not_exploded_into_per_character_codes(conn, tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                  '#!/bin/sh\necho \'{"schema":"alignment-collect/v1","degraded":"oops"}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    align = [r for r in results if r["schema"] == "alignment-collect/v1"][0]
    assert align["degraded_collector"] == []  # not ['o', 'o', 'p', 's']


def test_non_utf8_collector_output_degrades_as_not_json(conn, tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                  "#!/bin/sh\nprintf '\\377\\376bad'\n")
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    # Decoded with replacement, so it fails as unparseable JSON — a recorded degradation, not
    # a UnicodeDecodeError escaping run_source into the outer catch-all.
    assert scan["degraded_adapter"] == ["adapter_output_not_json"]


def test_null_schema_is_invalid_not_missing(conn, tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                  '#!/bin/sh\necho \'{"schema":null}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_schema_invalid"]


def test_an_internal_error_on_one_source_still_writes_every_row(conn, tmp_path, monkeypatch):
    # Pins the structural guarantee, not one known failure: whatever breaks inside the adapter
    # for one source, the other sources still land and ingest still returns.
    from insight.ingest import collectors as _collectors
    real = _collectors.run_source

    def boom(source, project_root, root):
        if source.name == "discovery-scan":
            raise RuntimeError("something nobody predicted")
        return real(source, project_root, root)

    monkeypatch.setattr(_collectors, "run_source", boom)
    results = ingest_collectors(conn, tmp_path, collectors_root=str(tmp_path / "nope"))
    assert len(results) == 3
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_internal_error"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_project_id_is_deterministic_and_stable_across_calls(conn, tmp_path):
    root = tmp_path / "skills"
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ids = conn.execute("select distinct project_id from fact_collector_pack").fetchall()
    assert len(ids) == 1


# --------------------------------------------------------------------------- remote-hash project_id_for (issue #106)


def _git_repo(path, remote=None):
    """A real, local, hermetic git repo -- init + one empty commit + (optionally) a FAKE
    remote URL, never a clone, never a network call. Mirrors git_reader.py's own test fixtures
    and this plan's own scratchpad verification."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=path, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)


def test_normalize_remote_url_collapses_ssh_and_https_to_the_same_string():
    from insight.ingest.packs import _normalize_remote_url
    a = _normalize_remote_url("git@github.com:owner/repo.git")
    b = _normalize_remote_url("https://github.com/owner/repo.git")
    assert a == b == "github.com/owner/repo"


def test_normalize_remote_url_collapses_the_github_ssh_host_alias():
    from insight.ingest.packs import _normalize_remote_url
    aliased = _normalize_remote_url("git@github.com-work:owner/repo.git")
    plain = _normalize_remote_url("https://github.com/owner/repo")
    assert aliased == plain == "github.com/owner/repo"


def test_normalize_remote_url_drops_an_explicit_ssh_port():
    """BLOCKING finding from plan review, reproduced live then fixed: the first-draft
    normalizer folded an explicit port's digits into the path, so this and its no-port
    equivalent normalized DIFFERENTLY. See .sdlc/plans/106.md Design decision B."""
    from insight.ingest.packs import _normalize_remote_url
    with_port = _normalize_remote_url("ssh://git@github.com:22/o/r.git")
    without_port = _normalize_remote_url("git@github.com:o/r.git")
    assert with_port == without_port == "github.com/o/r"


def test_normalize_remote_url_strips_userinfo_credentials():
    """BLOCKING finding from plan review, reproduced live then fixed: the first-draft
    credential guard truncated at the FIRST colon before ever checking for '@', so userinfo
    (e.g. a rotating token) was NEVER stripped -- meaning the SAME repo got a DIFFERENT
    project_id every time its embedded token rotated. See .sdlc/plans/106.md Design decision B."""
    from insight.ingest.packs import _normalize_remote_url
    with_token = _normalize_remote_url("https://user:token@github.com/o/r.git")
    without_token = _normalize_remote_url("https://github.com/o/r.git")
    assert with_token == without_token == "github.com/o/r"


def test_normalize_remote_url_returns_none_for_file_scheme():
    """file:// origins are explicitly out of scope (Design decision B) -- returns None rather
    than a garbled-looking string, so the caller falls through to the path-hash fallback."""
    from insight.ingest.packs import _normalize_remote_url
    assert _normalize_remote_url("file:///users/x/repos/repo") is None


def test_normalize_remote_url_returns_none_for_a_bare_filesystem_path():
    from insight.ingest.packs import _normalize_remote_url
    assert _normalize_remote_url("/users/x/repos/repo") is None


def test_normalize_remote_url_folds_host_case_but_preserves_path_case():
    """Non-blocking finding from independent code review: a first-draft normalizer lowercased
    the ENTIRE string (host AND path). Host case-folding is always correct -- DNS hostnames are
    case-insensitive by spec (RFC 3986 section 3.2.2). Path case-folding is NOT: GitHub/GitLab
    happen to treat owner/repo as effectively case-insensitive, but a case-sensitive self-hosted
    git server could have two GENUINELY DIFFERENT repos whose paths differ only in case --
    folding those would be a SILENT, UNDETECTABLE collapse onto the same project_id (cross-
    project data corruption). The alternative failure -- two differently-cased clone URLs of
    the SAME repo getting two different project_ids -- only produces a duplicate, DETECTABLE
    dim_project row. That asymmetry (silent corruption vs. a visible duplicate) is why path case
    is preserved here. See .sdlc/plans/106.md Design decision B and this issue's code review."""
    from insight.ingest.packs import _normalize_remote_url
    assert _normalize_remote_url("https://GitHub.COM/Owner/Repo.git") == "github.com/Owner/Repo"


def test_remote_identity_for_returns_none_none_with_no_remote(tmp_path):
    from insight.ingest.packs import remote_identity_for
    _git_repo(tmp_path / "r")
    assert remote_identity_for(tmp_path / "r") == (None, None)


def test_remote_identity_for_returns_none_none_for_a_non_git_directory(tmp_path):
    from insight.ingest.packs import remote_identity_for
    (tmp_path / "plain").mkdir()
    assert remote_identity_for(tmp_path / "plain") == (None, None)


def test_remote_identity_for_returns_none_none_when_git_is_unresolvable(tmp_path, monkeypatch):
    from insight.ingest.packs import remote_identity_for
    _git_repo(tmp_path / "r", remote="git@github.com:o/r.git")
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    assert remote_identity_for(tmp_path / "r") == (None, None)


def test_remote_identity_for_populates_repo_and_a_real_64_char_sha256(tmp_path):
    from insight.ingest.packs import remote_identity_for
    _git_repo(tmp_path / "r", remote="git@github.com:owner/repo.git")
    repo, sha = remote_identity_for(tmp_path / "r")
    assert repo == "github.com/owner/repo"
    assert len(sha) == 64
    import re
    assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_project_id_for_is_identical_for_two_clones_of_the_same_remote(tmp_path):
    """The correctness improvement over the pre-#106 path-hash scheme -- verified directly:
    dossier §2's own repoA/repoA-clone test showed the OLD scheme gave these DIFFERENT ids."""
    from insight.ingest.packs import project_id_for
    _git_repo(tmp_path / "clone1", remote="git@github.com:owner/repo.git")
    _git_repo(tmp_path / "clone2", remote="https://github.com/owner/repo.git")
    assert project_id_for(tmp_path / "clone1") == project_id_for(tmp_path / "clone2")


def test_project_id_for_differs_for_different_remotes(tmp_path):
    from insight.ingest.packs import project_id_for
    _git_repo(tmp_path / "a", remote="git@github.com:owner/repo-a.git")
    _git_repo(tmp_path / "b", remote="git@github.com:owner/repo-b.git")
    assert project_id_for(tmp_path / "a") != project_id_for(tmp_path / "b")


def test_project_id_for_is_stable_across_repeated_calls(tmp_path):
    from insight.ingest.packs import project_id_for
    _git_repo(tmp_path / "r", remote="git@github.com:owner/repo.git")
    assert project_id_for(tmp_path / "r") == project_id_for(tmp_path / "r")


def test_project_id_for_falls_back_to_the_exact_pre_106_path_hash_when_no_remote(tmp_path):
    """Research Q2/Q3: a remote-less repo's project_id is UNCHANGED by this story -- verified
    against the literal pre-#106 formula, not just 'some fallback'."""
    import hashlib
    from insight.ingest.packs import project_id_for
    _git_repo(tmp_path / "local-only")
    root = tmp_path / "local-only"
    expected = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    assert project_id_for(root) == expected


def test_project_id_for_falls_back_to_path_hash_for_a_file_remote(tmp_path):
    """A file://-or-bare-path origin is out of scope (Design decision B) -- it must fall
    through to the SAME path-hash fallback as no-remote-at-all, never crash, never produce a
    garbled identity string. Verified live against a real repo with a bare-path origin."""
    import hashlib
    from insight.ingest.packs import project_id_for
    _git_repo(tmp_path / "r", remote="/some/local/path/repo.git")
    root = tmp_path / "r"
    expected = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    assert project_id_for(root) == expected
