# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The metric loader (issue #108, E2.S1): discover metrics/<id>.sql, parse+validate each
header, register each conforming file as a real DuckDB view. See .sdlc/plans/108.md Design
decisions D/E for the fail-hard-vs-degrade boundary and why CREATE VIEW is real, not deferred.

No `import duckdb` here -- conn is passed in already open, same convention as
insight/ingest/packs.py.

KNOWN RESIDUE (Design decision E, non-blocking finding 6): a metric_<id> view for a .sql file
that was later renamed or deleted is never dropped by this function -- it only ever
CREATE-OR-REPLACEs views for files that currently exist on disk. Harmless against every
hermetic tmp_path connection this story's own tests use (a fresh file each time); a real,
long-lived store reused across a metric rename would keep answering a stale view. Not fixed
here -- see the plan for why."""
import os
import pathlib

from insight.metrics.header import HeaderError, parse_header

#: The real, shipped catalog directory -- this file's own parent. Overridable (metrics_dir=)
#: purely for hermetic tests against a tmp_path directory.
DEFAULT_METRICS_DIR = pathlib.Path(__file__).resolve().parent


class MetricLoadError(Exception):
    """Raised by load_metrics when one or more .sql files fail to parse or register. Message
    names every failing file and its reason WITHIN THE PHASE THAT FAILED -- see Design
    decision D. A caller that catches this must treat `conn` as unfit for dashboard use; some
    views may already exist.

    PR review fold-in, wording precision (not a behavior change): aggregation is complete
    WITHIN each phase (every header-parse failure is collected before raising; every
    CREATE VIEW failure is collected before raising) but NOT ACROSS the two phases -- a
    directory mixing a bad-header file and a bad-SQL file raises after the header-parse
    phase and never reaches CREATE VIEW at all in that run, so the bad-SQL file's own error
    is not named until the header problem is fixed and load_metrics is run again. This is
    never silently wrong (the caller always gets a real MetricLoadError and a real reason for
    at least one real file), but "names every offending file" without qualification
    overclaimed exhaustiveness across both phases in a single call."""


def load_metrics(conn, metrics_dir=None):
    """Discover every metrics_dir/*.sql (default: the real insight/metrics/ directory), parse
    each header, and -- only if every header is valid -- CREATE OR REPLACE VIEW metric_<id> for
    each, against `conn`. Returns {id: {**header, "view_name": "metric_<id>"}}.

    Raises MetricLoadError if ANY header fails to parse (Decision D: zero views are created in
    that case, and every header-parse failure across the directory is named in that one raise)
    or, only once every header parses, if any CREATE VIEW itself fails (bad SQL, a referenced
    table/column that doesn't exist -- DuckDB validates this eagerly, verified live, see Design
    decision E; every CREATE VIEW failure is likewise named together). See MetricLoadError's own
    docstring for why these two phases are each internally exhaustive but not combined across
    a single call -- a header-phase failure short-circuits before CREATE VIEW is ever
    attempted."""
    directory = pathlib.Path(metrics_dir) if metrics_dir is not None else DEFAULT_METRICS_DIR

    # PR review BLOCK, fixed: `pathlib.Path.glob()` silently swallows OSError/PermissionError
    # internally and yields no matches -- verified live, identical on 3.9/3.10/3.12: a missing
    # OR an unreadable metrics_dir used to make `sorted(directory.glob("*.sql"))` come back
    # `[]`, and load_metrics returned an EMPTY REGISTRY WITH NO EXCEPTION AT ALL -- the one
    # outcome this loader exists to prevent (Design decision D is fail-hard, never degrade).
    # `directory.is_dir()` is checked first for the common "doesn't exist" (or "is a plain
    # file") case, with a message naming the path; `os.listdir` -- which does NOT swallow
    # OSError, unlike glob -- then does the actual enumeration, so a directory that exists but
    # cannot be LISTED (mode 000: is_dir() is still True, since stat only needs search
    # permission on the PARENT, but listing needs read+execute on the directory itself) is
    # caught too, verified live. Filtering by `name.endswith(".sql")` afterward preserves
    # the exact case-sensitive-on-POSIX matching `glob("*.sql")` already had.
    if not directory.is_dir():
        raise MetricLoadError(
            f"metrics directory does not exist or is not a directory: {directory}"
        )
    try:
        names = os.listdir(directory)
    except OSError as e:
        raise MetricLoadError(f"cannot read metrics directory {directory}: {e}") from e
    paths = sorted(directory / name for name in names if name.endswith(".sql"))

    headers = {}
    errors = []
    for path in paths:
        try:
            # utf-8-sig, not plain utf-8 (non-blocking finding 3): a BOM-prefixed file read
            # as plain utf-8 leaves U+FEFF glued to line 1, defeating the header regex
            # entirely and reporting every field as missing -- reproduced live, fixed,
            # re-verified live. Matches tests/test_licence_boundary.py's own precedent for
            # exactly this failure.
            #
            # Pre-PR review BLOCK, fixed: this read used to sit OUTSIDE the try/except
            # below, so a stray non-UTF-8 byte (a smart quote or em-dash pasted from a spec
            # doc, saved by an editor that doesn't normalize to UTF-8) escaped as a raw
            # UnicodeDecodeError carrying no filename -- breaking this module's own
            # documented contract that MetricLoadError "names every offending file and its
            # reason" (Design decision D). Moving the read inside the try, and catching
            # UnicodeDecodeError alongside HeaderError, closes that: any decode failure is
            # now aggregated and named exactly like a header failure.
            text = path.read_text(encoding="utf-8-sig")
            headers[path.stem] = parse_header(text, source=str(path))
        except (HeaderError, UnicodeDecodeError) as e:
            errors.append(f"{path}: {e}" if isinstance(e, UnicodeDecodeError) else str(e))
    if errors:
        raise MetricLoadError(
            "one or more metric files failed to parse:\n  " + "\n  ".join(errors)
        )

    registry = {}
    for path in paths:
        metric_id = path.stem
        view_name = f"metric_{metric_id}"
        try:
            # The read is inside this try too (same reasoning as above -- swept the whole
            # module for any other file operation that could escape the aggregated
            # MetricLoadError contract; this is the other one). In practice this second
            # read of the same path should decode identically to the first loop's, but
            # nothing guarantees the file is unchanged between the two passes, and the
            # broad `except Exception` below already exists for CREATE VIEW failures, so
            # covering the read costs nothing extra.
            text = path.read_text(encoding="utf-8-sig")
            conn.execute(f"CREATE OR REPLACE VIEW {view_name} AS {text}")
        except Exception as e:  # noqa: BLE001 -- wrap+escalate, see Design decision D
            errors.append(f"{path}: {e}")
            continue
        registry[metric_id] = dict(headers[metric_id], view_name=view_name)
    if errors:
        raise MetricLoadError(
            "one or more metric files failed to register as a view:\n  " + "\n  ".join(errors)
        )
    return registry
