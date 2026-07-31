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
import pathlib

from insight.metrics.header import HeaderError, parse_header

#: The real, shipped catalog directory -- this file's own parent. Overridable (metrics_dir=)
#: purely for hermetic tests against a tmp_path directory.
DEFAULT_METRICS_DIR = pathlib.Path(__file__).resolve().parent


class MetricLoadError(Exception):
    """Raised by load_metrics when one or more .sql files fail to parse or register. Message
    names every failing file and its reason -- see Design decision D. A caller that catches
    this must treat `conn` as unfit for dashboard use; some views may already exist."""


def load_metrics(conn, metrics_dir=None):
    """Discover every metrics_dir/*.sql (default: the real insight/metrics/ directory), parse
    each header, and -- only if every header is valid -- CREATE OR REPLACE VIEW metric_<id> for
    each, against `conn`. Returns {id: {**header, "view_name": "metric_<id>"}}.

    Raises MetricLoadError, naming every offending file, if ANY header fails to parse (Decision
    D: zero views are created in that case) or if any CREATE VIEW itself fails (bad SQL, a
    referenced table/column that doesn't exist -- DuckDB validates this eagerly, verified live,
    see Design decision E)."""
    directory = pathlib.Path(metrics_dir) if metrics_dir is not None else DEFAULT_METRICS_DIR
    paths = sorted(directory.glob("*.sql"))

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
