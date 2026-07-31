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

    # THE CLASS OF BUG THIS WHOLE FUNCTION KEEPS RE-DISCOVERING A NEW ROUTE INTO, STATED ONCE
    # HERE SO THE NEXT FIX ALSO GENERALISES: every real filesystem operation this function
    # performs -- stat'ing a path, listing a directory, opening/reading a file -- can raise
    # OSError (or a subclass) for reasons that have NOTHING to do with whether a .sql file's
    # CONTENT is well-formed: permission bits, TOCTOU races, an entry that is a directory, not
    # a file. Three separate review rounds each caught a different call site doing this raw:
    # `path.read_text()` outside its try (round 1); `pathlib.Path.glob()` swallowing
    # PermissionError internally and returning `[]` at the directory level (round 2); and now,
    # round 3, TWO more call sites -- `directory.is_dir()` ITSELF (see below), and the same
    # `path.read_text()` from round 1 still only catching UnicodeDecodeError, not the wider
    # OSError family (PermissionError from a mode-444 directory denying the search bit needed
    # to OPEN a file inside it; IsADirectoryError from a `.sql`-named directory entry). The
    # fix below is written at the CLASS level -- every one of these call sites is now either
    # wrapped in `except OSError` or feeds into the header-parse loop's widened except clause
    # -- rather than patching the one instance handed over each time.
    try:
        directory_exists = directory.is_dir()
    except OSError as e:
        # `is_dir()` normally just returns False for a missing path or a broken symlink; it
        # raises when an ANCESTOR in the path chain lacks search permission and the stat()
        # call itself cannot be resolved. Verified live. A different failure shape from
        # "the directory exists but the read/list bits are wrong" below, so named separately.
        raise MetricLoadError(f"cannot access metrics directory {directory}: {e}") from e
    if not directory_exists:
        raise MetricLoadError(
            f"metrics directory does not exist or is not a directory: {directory}"
        )
    try:
        names = os.listdir(directory)
    except OSError as e:
        # `os.listdir` does NOT swallow OSError the way `Path.glob()` does (round 2's fix) --
        # a directory that exists but cannot be LISTED (mode 000: is_dir() is still True,
        # since stat only needs search permission on the PARENT, but listing needs
        # read+execute on the directory itself) is caught here, verified live.
        raise MetricLoadError(f"cannot read metrics directory {directory}: {e}") from e
    # Filtering by `name.endswith(".sql")` preserves the exact case-sensitive-on-POSIX
    # matching `glob("*.sql")` already had.
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
            text = path.read_text(encoding="utf-8-sig")
            headers[path.stem] = parse_header(text, source=str(path))
        except HeaderError as e:
            errors.append(str(e))  # already carries `source` -- do not re-prefix the path
        except (OSError, UnicodeDecodeError) as e:
            # PR review BLOCK, round 3, the CLASS-level fix (see the long comment above this
            # function's directory-resolution code for the full pattern this closes): this
            # used to be `except (HeaderError, UnicodeDecodeError)` only, which is narrower
            # than the failure surface a real filesystem read actually has. Widened to the
            # whole OSError family so PermissionError (a mode-444 directory: is_dir() and
            # listdir() both succeed, but OPENING a file inside it needs the search bit on
            # the directory, which mode 444 denies -- verified live), IsADirectoryError (a
            # `.sql`-named entry that is actually a directory -- an accidental
            # `mkdir metrics/weird.sql`), and a TOCTOU FileNotFoundError (the file vanishes
            # between os.listdir() above and this read) are all aggregated the same way
            # UnicodeDecodeError already was, instead of escaping raw.
            errors.append(f"{path}: {e}")
    if errors:
        raise MetricLoadError(
            "one or more metric files failed to parse:\n  " + "\n  ".join(errors)
        )

    registry = {}
    for path in paths:
        metric_id = path.stem
        view_name = f"metric_{metric_id}"
        try:
            # The read is inside this try too, for the same class of reason as the loop
            # above. This loop's `except Exception` is DELIBERATELY broader than the
            # `(HeaderError, OSError, UnicodeDecodeError)` above it -- re-compared side by
            # side, this is the one remaining, intentional asymmetry between the two loops,
            # not a residual gap: `conn.execute(CREATE VIEW ...)` is a DuckDB call whose
            # exception types (CatalogException, ParserException, BinderException, and
            # others) are not enumerable ahead of time the way this module's own
            # HeaderError and the stdlib's OSError/UnicodeDecodeError are, so narrowing this
            # catch to a fixed tuple would risk a new DuckDB exception type escaping raw the
            # next time duckdb's own exception hierarchy changes. The header-parse loop
            # above stays narrow ON PURPOSE, not from an oversight: it only ever calls
            # `path.read_text()` and `parse_header()`, both fully understood by this module,
            # so a bare Exception there would risk silently swallowing an actual programming
            # bug in `parse_header` instead of letting it surface loudly (Design decision D's
            # fail-hard principle applies to bugs in THIS codebase too, not only to malformed
            # input files).
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
