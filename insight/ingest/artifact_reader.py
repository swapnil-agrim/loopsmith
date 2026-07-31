# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Artifact reader (issue #102, E1.S4): goal frontmatter, slice manifests, and the config
snapshot -- read from .sdlc/goals/*.md, .sdlc/plans/<stem>.slices.json, and .sdlc/config.json,
landed into dim_project / fact_goal / fact_slice (the last a #102 design decision -- see
.sdlc/plans/102.md §C).

Reimplements skills/sdlc-loop/scripts/frontmatter.py:parse and skills/sdlc-loop/scripts/
slices.py's manifest normalisation from scratch (never imported -- the plugin/product boundary
in tests/test_import_boundary.py forbids it; same precedent as ledger_reader.py reimplementing
ledger.py:read_all in #101).

READ functions are pure stdlib and duckdb-free (no `import duckdb` anywhere in this file --
`conn` is always passed in already open, same convention as packs.py). WRITE functions never
raise: a goal missing a field records the ABSENCE ("field" not in fm), never a guessed default
(the issue's own done_when); a malformed slices.json or config.json degrades to empty/absent,
never fatal -- one goal's broken artifact must not abort every other goal's ingest. See
.sdlc/plans/102.md for the full design-decision record (A-K).

TWO DELIBERATE DIVERGENCES from the plugin scripts being reimplemented, both documented in
.sdlc/plans/102.md and both load-bearing:
  1. read_slices NEVER RAISES. skills/sdlc-loop/scripts/slices.py's load() raises ValueError on
     malformed JSON or a non-list top level -- correct for an interactive dispatch tool, wrong
     for an ingest reader whose one hard rule is "never fatal, degrade the record". See §I.
  2. Files are opened with encoding="utf-8-sig", errors="replace" (the ledger_reader.py
     convention from #101, carried forward here for the same reason).
"""
import json
import pathlib
import re

from insight.ingest.packs import project_id_for

_FENCE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def parse_frontmatter(text):
    """Flat `key: value` frontmatter block -> dict of whatever keys actually appeared. Absence
    is `"key" not in result`, never a guessed default -- callers must check membership, not
    truthiness (.sdlc/plans/102.md Design decision H). Byte-for-byte port of frontmatter.py's
    regex/split/strip logic."""
    m = _FENCE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"')
    return out


def read_goal_file(path):
    """One goal .md file -> parsed frontmatter dict, or None if the file is unreadable. Never
    raises -- mirrors ledger_reader.py's OSError guard around every file read."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    return parse_frontmatter(text)


def discover_goal_files(sdlc_dir):
    """<sdlc_dir>/goals/*.md, sorted. Scope: LOCAL FILE goals only -- a discovery.source ==
    "github" goal lives as a GitHub issue with no frontmatter text at all, out of scope for a
    frontmatter reader. See .sdlc/plans/102.md Design decision E."""
    goals_dir = pathlib.Path(sdlc_dir) / "goals"
    if not goals_dir.is_dir():
        return []
    return sorted(goals_dir.glob("*.md"))


def goal_record(sdlc_dir, goal_path):
    """One goal file -> the flat dict of fact_goal's #102-owned columns, or None if the file
    could not even be read. goal_id: frontmatter `id` when present and non-empty, else the
    file's own stem -- the one field that can't be recorded as absent (Design decision H)."""
    fm = read_goal_file(goal_path)
    if fm is None:
        return None
    stem = pathlib.Path(goal_path).stem
    plan_path = pathlib.Path(sdlc_dir) / "plans" / f"{stem}.md"
    return {
        "goal_id": fm.get("id") or stem,
        "title": fm.get("title"),
        "lane": fm.get("lane"),
        "source": fm.get("source"),
        "status": fm.get("status"),
        "verify_command": fm.get("verify_command"),
        "done_when_present": "done_when" in fm,
        "plan_artifact_present": plan_path.is_file(),
    }


def _as_list(value):
    """Same tolerance as slices.py's own _as_list: a single string where a list belongs is
    accepted (the commonest hand-edit slip), not rejected. Blanks dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def read_slices(sdlc_dir, goal_stem):
    """<sdlc_dir>/plans/<goal_stem>.slices.json -> normalised list of slice dicts, [] when
    absent, unreadable, malformed JSON, or a non-list top level. NEVER RAISES -- deliberately
    diverges from slices.py's load(), which raises on the last two. See .sdlc/plans/102.md §I.

    Two entries are deduped (last occurrence wins, IN PLACE -- keeps its original manifest
    position rather than moving to the end) ONLY when they share the same real, author-declared,
    NON-EMPTY `id`. An `id`-less entry NEVER enters that dedup path -- and, critically, it is
    NOT stored with `id: ""` either (a first draft of this fix did exactly that; two "" rows
    still collide on fact_slice's PRIMARY KEY at write_slices, one layer down from read_slices'
    own return value -- see .sdlc/plans/102.md §I for the full two-round history). Instead each
    `id`-less entry gets a SYNTHETIC id derived from its own position, `f"_pos{i}"` -- a real
    fact about the manifest (where this entry sits), never fabricated content, mirroring Design
    decision H's `goal_id` fallback exactly. The dedup check runs on `declared_id` (the raw,
    author-written value) BEFORE the synthetic fallback is applied, so two `id`-less entries can
    never collide with each other (different positions -> different `_posN` strings, by
    construction) and never get treated as "the same slice" the way two real matching ids do."""
    path = pathlib.Path(sdlc_dir) / "plans" / f"{goal_stem}.slices.json"
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    index_by_id = {}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        declared_id = str(item.get("id") or "").strip()
        sid = declared_id or f"_pos{i}"
        normalised = {
            "id": sid,
            "title": str(item.get("title") or "").strip(),
            "size": str(item.get("size") or "small").strip() or "small",
            "status": str(item.get("status") or "pending").strip() or "pending",
            "needs": _as_list(item.get("needs")),
            "files": _as_list(item.get("files")),
        }
        if declared_id and declared_id in index_by_id:
            out[index_by_id[declared_id]] = normalised   # real duplicate id: overwrite in place
        else:
            if declared_id:
                index_by_id[declared_id] = len(out)
            out.append(normalised)                        # synthetic id, or first time seeing this id
    return out


def read_config_snapshot(sdlc_dir):
    """<sdlc_dir>/config.json's raw text, VERBATIM, if and only if it parses as valid JSON --
    else None (absent, not guessed). The raw text is stored, not a json.dumps(json.loads(...))
    round trip -- preserves the file's own formatting. See .sdlc/plans/102.md §J."""
    path = pathlib.Path(sdlc_dir) / "config.json"
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    try:
        json.loads(raw)
    except ValueError:
        return None
    return raw


# --------------------------------------------------------------------------- write (conn passed in, already open)

_GOAL_UPSERT_SQL = """
    INSERT INTO fact_goal
      (project_id, goal_id, title, lane, source, done_when_present, plan_artifact_present,
       status, verify_command)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (project_id, goal_id) DO UPDATE SET
      title = excluded.title,
      lane = excluded.lane,
      source = excluded.source,
      done_when_present = excluded.done_when_present,
      plan_artifact_present = excluded.plan_artifact_present,
      status = excluded.status,
      verify_command = excluded.verify_command
"""


def write_goal(conn, project_id, record):
    """Upsert: columns this story owns are overwritten every run; every OTHER fact_goal column
    (outcome, pr, claimed_ts, ...) is untouched, because it is not named in the SET clause --
    see .sdlc/plans/102.md Design decision A."""
    conn.execute(_GOAL_UPSERT_SQL, [
        project_id, record["goal_id"], record["title"], record["lane"], record["source"],
        record["done_when_present"], record["plan_artifact_present"],
        record["status"], record["verify_command"],
    ])


def write_slices(conn, project_id, goal_id, slices):
    """Full resync: DELETE this goal's existing fact_slice rows, then INSERT the current
    manifest -- correctly reflects a slice REMOVED from the plan since the last ingest, which a
    plain upsert could never do. See .sdlc/plans/102.md Design decision A."""
    conn.execute("DELETE FROM fact_slice WHERE project_id = ? AND goal_id = ?", [project_id, goal_id])
    for s in slices:
        conn.execute(
            "INSERT INTO fact_slice (project_id, goal_id, slice_id, title, size, status, "
            "needs, files) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [project_id, goal_id, s["id"], s["title"], s["size"], s["status"],
             s["needs"], s["files"]],
        )


_PROJECT_UPSERT_SQL = """
    INSERT INTO dim_project (project_id, config_json, first_seen, last_seen)
    VALUES (?, ?, now(), now())
    ON CONFLICT (project_id) DO UPDATE SET
      config_json = excluded.config_json,
      last_seen = excluded.last_seen
"""


def write_project_snapshot(conn, project_id, config_json):
    """first_seen is set only on the FIRST insert (absent from the SET clause -- untouched on
    conflict); last_seen updates every run. repo/remote_url_sha256/north_star_present stay
    NULL, out of scope for #102 -- see .sdlc/plans/102.md Design decision F."""
    conn.execute(_PROJECT_UPSERT_SQL, [project_id, config_json])


def ingest_artifacts(conn, project_root, sdlc_dir=None):
    """The write orchestration `insight ingest` calls (issue #102). Never raises: an unreadable
    goal file is skipped (read_goal_file -> None), a broken slices.json degrades to []
    (read_slices), a broken config.json degrades to None (read_config_snapshot) -- the "guard
    the computation, degrade the record" contract ledger_reader.py established in #101.

    One goal's write failing for an unforeseen reason is wrapped in an EXPLICIT transaction
    (BEGIN/COMMIT, ROLLBACK on exception) so that goal's writes are all-or-nothing -- without
    it, DuckDB auto-commits each statement, and write_goal succeeding followed by write_slices
    later failing for that SAME goal would leave a partially-written, uncounted row behind
    instead of cleanly excluding that goal. See .sdlc/plans/102.md Design decision L for the
    concrete bug this closes and the empirical verification that ROLLBACK undoes the earlier
    statement too, not just the failing one."""
    project_root = pathlib.Path(project_root)
    sdlc_dir = pathlib.Path(sdlc_dir) if sdlc_dir is not None else project_root / ".sdlc"
    project_id = project_id_for(project_root)

    config_json = read_config_snapshot(sdlc_dir)
    write_project_snapshot(conn, project_id, config_json)

    goal_count, slice_count = 0, 0
    for goal_path in discover_goal_files(sdlc_dir):
        record = goal_record(sdlc_dir, goal_path)
        if record is None:
            continue
        conn.execute("BEGIN TRANSACTION")
        try:
            write_goal(conn, project_id, record)
            slices = read_slices(sdlc_dir, pathlib.Path(goal_path).stem)
            write_slices(conn, project_id, record["goal_id"], slices)
            conn.execute("COMMIT")
        except Exception:
            # A well-typed record can still surprise DuckDB at INSERT time (an unforeseen
            # value shape); one goal's write must not abort every other goal's ingest -- same
            # reasoning as packs.ingest_collectors' own per-source try/except. ROLLBACK undoes
            # write_goal too, so this goal is cleanly excluded rather than half-landed.
            conn.execute("ROLLBACK")
            continue
        goal_count += 1
        slice_count += len(slices)
    return {"goals": goal_count, "slices": slice_count, "config_present": config_json is not None}
