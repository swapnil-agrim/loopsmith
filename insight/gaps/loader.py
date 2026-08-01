# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap rule loader (issue #116, E3.S1): discover gaps/<id>.sql, parse+validate each header --
never register the query as a live DuckDB view (see .sdlc/plans/116.md Design decision 7: this is
structural validation only, no `conn` at load time; insight/gaps/evaluate.py is where a rule's
query actually runs, against an already-open connection).

No `import duckdb` here -- there is no conn at all in this module; the whole point of Decision 7
is that nothing here touches a database.

Mirrors insight.metrics.loader's directory-resolution robustness (the is_dir()/listdir()
OSError-wrapping, utf-8-sig read, one-raised-error-names-every-offending-file aggregation) but
with a SINGLE phase, not two -- there is no second (view-creation) phase, since Decision 7 means
nothing is ever registered against a database at load time."""
import os
import pathlib

from insight.gaps.header import GapHeaderError, parse_header

#: The real, shipped rules directory -- this file's own parent. Overridable (rules_dir=) purely
#: for hermetic tests against a tmp_path directory.
DEFAULT_RULES_DIR = pathlib.Path(__file__).resolve().parent


class GapLoadError(Exception):
    """Raised by load_gap_rules when one or more .sql files fail to parse. Message names every
    failing file and its reason -- same "aggregate everything, fail hard" posture as
    insight.metrics.loader.MetricLoadError. A single phase only: header-parse is the only thing
    this loader ever does (Decision 7 -- no second, view-creation phase)."""


def load_gap_rules(rules_dir=None):
    """Discover every rules_dir/*.sql (default: the real insight/gaps/ directory), parse+validate
    each header (Decision 3 catches an empty-body rule here). No duckdb, no conn -- this is
    structural validation only, the SQL is never executed (Decision 7; see evaluate.py for where
    it runs). Returns {rule_id: {**header, "query": <full file text>, "source": <path>}}.

    `registry[rule_id]["query"]` is the file's FULL text, header included -- the same trick
    insight.metrics.loader's `CREATE OR REPLACE VIEW {view_name} AS {text}` already relies on:
    `--` header lines are valid SQL line comments, so `conn.execute(rule["query"])` in
    evaluate.py runs correctly with zero body-slicing needed.

    Raises GapLoadError if ANY header fails to parse -- every header-parse failure across the
    directory is named together in that one raise, mirroring MetricLoadError's own "aggregate
    within one phase" posture (this loader has only one phase to begin with)."""
    directory = pathlib.Path(rules_dir) if rules_dir is not None else DEFAULT_RULES_DIR

    # Same class of bug insight.metrics.loader's own module docstring names at length: every
    # real filesystem operation here (stat'ing a path, listing a directory, opening/reading a
    # file) can raise OSError for reasons that have nothing to do with whether a .sql file's
    # CONTENT is well-formed. Every call site below is wrapped accordingly.
    try:
        directory_exists = directory.is_dir()
    except OSError as e:
        raise GapLoadError(f"cannot access gap rules directory {directory}: {e}") from e
    if not directory_exists:
        raise GapLoadError(
            f"gap rules directory does not exist or is not a directory: {directory}"
        )
    try:
        names = os.listdir(directory)
    except OSError as e:
        raise GapLoadError(f"cannot read gap rules directory {directory}: {e}") from e
    # Filtering by `name.endswith(".sql")` preserves the exact case-sensitive-on-POSIX matching
    # `glob("*.sql")` already has, same convention as insight.metrics.loader.
    paths = sorted(directory / name for name in names if name.endswith(".sql"))

    registry = {}
    errors = []
    for path in paths:
        try:
            # utf-8-sig, not plain utf-8: a BOM-prefixed file read as plain utf-8 leaves U+FEFF
            # glued to line 1, defeating the header regex entirely -- matches
            # insight.metrics.loader's own fix for the identical failure.
            text = path.read_text(encoding="utf-8-sig")
            header = parse_header(text, source=str(path))
        except GapHeaderError as e:
            errors.append(str(e))  # already carries `source` -- do not re-prefix the path
        except (OSError, UnicodeDecodeError) as e:
            errors.append(f"{path}: {e}")
        else:
            registry[path.stem] = dict(header, query=text, source=str(path))
    if errors:
        raise GapLoadError(
            "one or more gap rule files failed to parse:\n  " + "\n  ".join(errors)
        )
    return registry
