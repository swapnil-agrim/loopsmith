# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.metrics.loader (issue #108, E2.S1). See .sdlc/plans/108.md Design
decision D for the fail-hard-vs-degrade boundary this file exercises."""
import os

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import MetricLoadError, load_metrics  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _write(path, text):
    path.write_text(text, encoding="utf-8")


_GOOD_1 = (
    "-- name: Throughput\n-- question: Are we shipping?\n-- personas: manager\n"
    "-- reliability_class: 1\n-- guardrail: pair with #5\n"
    "SELECT count(*) AS n FROM fact_goal WHERE outcome = 'done'\n"
)
_GOOD_2 = (
    "-- name: WIP\n-- question: How much is in flight?\n-- personas: manager\n"
    "-- reliability_class: 1\n-- guardrail: never individual grain\n"
    "SELECT count(*) AS n FROM fact_goal WHERE terminal_ts IS NULL\n"
)
_MISSING_GUARDRAIL = (
    "-- name: Bad\n-- question: ?\n-- personas: manager\n-- reliability_class: 1\n"
    "SELECT 1\n"
)
_BAD_SQL = (
    "-- name: Bad SQL\n-- question: ?\n-- personas: manager\n-- reliability_class: 1\n"
    "-- guardrail: z\n"
    "SELECT * FROM this_table_does_not_exist\n"
)


def test_registers_every_conforming_file_as_a_view(tmp_path, conn):
    _write(tmp_path / "1.sql", _GOOD_1)
    _write(tmp_path / "2.sql", _GOOD_2)
    registry = load_metrics(conn, metrics_dir=tmp_path)
    assert set(registry) == {"1", "2"}
    assert registry["1"]["view_name"] == "metric_1"
    assert registry["1"]["name"] == "Throughput"
    assert conn.execute("SELECT * FROM metric_1").fetchall() == [(0,)]
    assert conn.execute("SELECT * FROM metric_2").fetchall() == [(0,)]


def test_a_metric_missing_a_guardrail_header_fails_the_loader(tmp_path, conn):
    """The issue's own done-when, verbatim."""
    _write(tmp_path / "1.sql", _MISSING_GUARDRAIL)
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=tmp_path)
    assert "1.sql" in str(exc.value)
    assert "guardrail" in str(exc.value)


def test_a_missing_guardrail_registers_no_views_at_all(tmp_path, conn):
    """Decision D: header-parse failures are checked BEFORE any CREATE VIEW is attempted --
    even a co-located, otherwise-valid file must not get a view when its sibling fails."""
    _write(tmp_path / "1.sql", _MISSING_GUARDRAIL)
    _write(tmp_path / "2.sql", _GOOD_2)
    with pytest.raises(MetricLoadError):
        load_metrics(conn, metrics_dir=tmp_path)
    names = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'"
    ).fetchall()]
    assert "metric_2" not in names


def test_two_bad_header_files_are_both_named_in_one_raised_error(tmp_path, conn):
    _write(tmp_path / "1.sql", _MISSING_GUARDRAIL)
    _write(tmp_path / "2.sql", "-- name: X\nSELECT 1\n")
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=tmp_path)
    assert "1.sql" in str(exc.value) and "2.sql" in str(exc.value)


def test_valid_header_but_invalid_sql_is_caught_at_create_view_time(tmp_path, conn):
    _write(tmp_path / "1.sql", _BAD_SQL)
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=tmp_path)
    assert "1.sql" in str(exc.value)


def test_view_name_is_metric_underscore_id_not_the_bare_numeral(tmp_path, conn):
    _write(tmp_path / "7.sql", _GOOD_1)
    load_metrics(conn, metrics_dir=tmp_path)
    assert conn.execute("SELECT * FROM metric_7").fetchall() == [(0,)]


def test_load_metrics_defaults_to_the_real_insight_metrics_directory(conn):
    """No metrics_dir override -- proves the default resolves to the real, shipped
    insight/metrics/ directory, exercised for real in Task 4's test_metric_1 too."""
    registry = load_metrics(conn)
    assert "1" in registry


def test_a_bom_prefixed_metric_file_still_parses(tmp_path, conn):
    """Non-blocking finding 3, folded in after plan review: a file read as plain utf-8 leaves
    a literal BOM glued to line 1, which silently defeats the header regex and reports every
    field as missing -- reproduced live before the fix. utf-8-sig strips it transparently."""
    import codecs
    (tmp_path / "1.sql").write_bytes(codecs.BOM_UTF8 + _GOOD_1.encode("utf-8"))
    registry = load_metrics(conn, metrics_dir=tmp_path)
    assert registry["1"]["name"] == "Throughput"


def test_a_non_utf8_byte_anywhere_in_the_file_is_caught_and_named_not_raised_raw(tmp_path, conn):
    """Pre-PR review BLOCK, reproduced live: both read_text(encoding="utf-8-sig") calls sat
    OUTSIDE the try/except HeaderError in each loop, so a stray non-UTF-8 byte (a smart quote
    or em-dash pasted from a spec doc, saved by an editor that doesn't normalize to UTF-8)
    escaped as a raw UnicodeDecodeError carrying no filename -- breaking this module's own
    documented contract ("names every offending file and its reason", Design decision D). A
    caller written strictly against MetricLoadError (`except MetricLoadError:`) would not catch
    it. Fixed by moving the read inside the same try that already catches HeaderError."""
    (tmp_path / "1.sql").write_bytes(
        b"-- name: Bad\xff\n-- question: Y?\n-- personas: manager\n"
        b"-- reliability_class: 1\n-- guardrail: z\nSELECT 1\n"
    )
    _write(tmp_path / "2.sql", _GOOD_2)  # a co-located good file must not mask the bad one
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=tmp_path)
    assert "1.sql" in str(exc.value)


def test_a_nonexistent_metrics_dir_raises_metric_load_error_not_an_empty_registry(tmp_path, conn):
    """PR review BLOCK, reproduced live on 3.9/3.10/3.12: `pathlib.Path.glob()` silently
    swallows OSError/PermissionError internally and yields no matches, so
    `sorted(directory.glob("*.sql"))` on a missing directory used to return `[]` and
    `load_metrics` returned an EMPTY REGISTRY WITH NO EXCEPTION AT ALL -- from the one module
    whose entire design decision (D) is to fail hard rather than degrade. A misconfigured
    `metrics_dir=` override, or a bad deployment path, made the whole catalog silently vanish.
    Fixed by checking `directory.is_dir()` explicitly before any glob/listdir call."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=missing)
    assert str(missing) in str(exc.value)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs a non-root posix user; root can read mode-000 directories")
def test_an_unreadable_metrics_dir_raises_metric_load_error_not_an_empty_registry(tmp_path, conn):
    """The other half of the same BLOCK: a directory that EXISTS (so `is_dir()` is True --
    stat only needs search permission on the PARENT, not the directory itself) but cannot be
    LISTED (mode 000 denies read+execute on the directory itself) hits the exact same
    glob-swallows-OSError hole. `os.listdir` raises PermissionError here, verified live; this
    asserts that surfaces as MetricLoadError, not another silent empty registry."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "1.sql").write_text(_GOOD_1, encoding="utf-8")
    os.chmod(blocked, 0o000)
    try:
        with pytest.raises(MetricLoadError) as exc:
            load_metrics(conn, metrics_dir=blocked)
        assert str(blocked) in str(exc.value)
    finally:
        os.chmod(blocked, 0o755)  # restore so pytest's own tmp_path cleanup can remove it


def test_metrics_dir_pointing_at_a_plain_file_raises_metric_load_error(tmp_path, conn):
    """Rounding out the same fix: metrics_dir can be misconfigured to point at a FILE (a typo
    dropping the trailing directory segment), not just a missing/unreadable directory --
    `is_dir()` is False for a plain file too, so the same guard covers this shape for free."""
    not_a_dir = tmp_path / "1.sql"
    not_a_dir.write_text(_GOOD_1, encoding="utf-8")
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=not_a_dir)
    assert str(not_a_dir) in str(exc.value)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs a non-root posix user; root can read mode-444 directories")
def test_a_mode_444_directory_raises_metric_load_error_not_a_raw_permission_error(tmp_path, conn):
    """PR review block cycle 3, THIRD escape route for the SAME class of bug (see the module
    docstring's "the pattern is the finding" note): mode 444 (read-but-not-search) is the case
    that behaves DIFFERENTLY from mode 000. `is_dir()` and `os.listdir()` both succeed --
    listing entries only needs the read bit -- so the directory-level guard added for mode 000
    does not fire at all here. The failure only happens per-file, inside `path.read_text()`,
    because OPENING a file requires the search (execute) bit on its PARENT directory, which
    mode 444 denies. Verified live: this raised a raw PermissionError with no MetricLoadError
    before the header-parse loop's except clause was widened from
    `(HeaderError, UnicodeDecodeError)` to also catch `OSError`."""
    blocked = tmp_path / "blocked444"
    blocked.mkdir()
    (blocked / "1.sql").write_text(_GOOD_1, encoding="utf-8")
    os.chmod(blocked, 0o444)
    try:
        with pytest.raises(MetricLoadError) as exc:
            load_metrics(conn, metrics_dir=blocked)
        assert "1.sql" in str(exc.value)
    finally:
        os.chmod(blocked, 0o755)  # restore so pytest's own tmp_path cleanup can remove it


def test_a_sql_named_entry_that_is_actually_a_directory_raises_metric_load_error(tmp_path, conn):
    """Second trigger of the same root cause, no permission bits needed at all: an accidental
    `mkdir metrics/weird.sql` (a `.sql`-suffixed DIRECTORY, not a file) makes `path.read_text()`
    raise a raw `IsADirectoryError` -- same unguarded read, same fix (widening the except
    clause to OSError) closes it."""
    (tmp_path / "weird.sql").mkdir()
    with pytest.raises(MetricLoadError) as exc:
        load_metrics(conn, metrics_dir=tmp_path)
    assert "weird.sql" in str(exc.value)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs a non-root posix user; root can traverse mode-000 ancestors")
def test_an_unreadable_ancestor_makes_is_dir_itself_raise_and_it_must_not_escape(tmp_path, conn):
    """Found by the sweep the review asked for, not handed over: `directory.is_dir()` -- the
    very first call this function makes -- is ITSELF unguarded. `is_dir()` normally just
    returns False for a nonexistent/broken-symlink path, but when an ANCESTOR directory in the
    chain lacks search permission, the underlying stat() call cannot even be resolved and
    `is_dir()` raises PermissionError directly, verified live. This is a different call site
    from the mode-444/mode-000 cases above (those are about `metrics_dir` itself or the files
    inside it); this one is about `metrics_dir`'s PARENT being unreadable."""
    blocked_parent = tmp_path / "blocked_parent"
    inner = blocked_parent / "inner"
    inner.mkdir(parents=True)
    (inner / "1.sql").write_text(_GOOD_1, encoding="utf-8")
    os.chmod(blocked_parent, 0o000)
    try:
        with pytest.raises(MetricLoadError) as exc:
            load_metrics(conn, metrics_dir=inner)
        assert str(inner) in str(exc.value)
    finally:
        os.chmod(blocked_parent, 0o755)  # restore so pytest's own tmp_path cleanup can remove it


def test_the_three_metrics_modules_never_import_duckdb():
    """Pins the property Design decisions C/D/E all rest on -- header.py/loader.py/testing.py
    take an already-open conn wherever they need one and never `import duckdb` themselves
    (same convention as insight/ingest/packs.py). True today but previously unguarded, unlike
    insight/__main__.py's own equivalent pin in test_cli.py.

    THE GUARD'S REAL BOUNDARY, written down before 24 more metric files lean on this
    convention (PR review, block cycle 3): `ast.walk` DOES recurse into nested scopes, so a
    deferred `import duckdb` inside a function body IS caught here -- attacked and confirmed
    to hold. What is NOT caught: `importlib.import_module("duckdb")` and
    `__import__("duckdb")` -- neither produces an `ast.Import`/`ast.ImportFrom` node, so both
    slip past this specific check, confirmed against the real detection logic above. This is
    not a defect in any of the three shipped modules today (none of them use either dynamic
    form) -- it is the same scope boundary `tests/test_import_boundary.py`'s own module
    docstring already states for the plugin/product boundary check ("DYNAMIC IMPORTS ...
    ARE DELIBERATELY NOT COVERED"), stated here too so a future contributor doesn't read a
    green run of this test as proof against a dynamic-import route it was never designed to
    catch."""
    import ast
    import pathlib

    metrics_dir = pathlib.Path(__file__).resolve().parents[1] / "metrics"
    for name in ("header.py", "loader.py", "testing.py"):
        path = metrics_dir / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])
        assert "duckdb" not in imported, f"{name} must not import duckdb -- conn is passed in already open"
