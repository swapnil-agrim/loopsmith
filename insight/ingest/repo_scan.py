# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`--repos <glob>` expansion and the `.sdlc` adoption gate (issue #106, E1.S8). See
.sdlc/plans/106.md Design decisions A and D for the full reasoning -- summarized here:

`expand_repos` uses the stdlib `glob` module, NOT `pathlib.Path.glob` (which raises
`NotImplementedError` on an absolute pattern, on every supported Python version) and NOT
`glob.glob(root_dir=...)` (a 3.10+-only kwarg -- this codebase's floor is 3.9). Joining `cwd`
and `pattern` via `os.path.join` before calling `glob.glob` handles both a relative pattern and
an already-absolute one identically, on 3.9+, with one function call.

No `import duckdb` here -- this module is pure stdlib, imported lazily by `insight.__main__`
alongside the other `insight.ingest.*` modules (see test_main_module_does_not_import_duckdb_at_
top_level's `banned` set)."""
import glob
import os
import pathlib


def expand_repos(pattern, cwd):
    """--repos <glob> -> sorted list of matching DIRECTORY Paths, joined against `cwd` via
    os.path.join before calling the stdlib glob.glob -- NOT pathlib.Path.glob, which raises
    NotImplementedError on an absolute pattern (verified live, Python 3.9, this codebase's own
    floor). os.path.join(cwd, pattern) handles a RELATIVE pattern (joined onto cwd) and an
    ALREADY-ABSOLUTE one (os.path.join discards the cwd prefix the moment a later argument is
    itself absolute -- verified) with the SAME call, on any Python >= 3.9 -- no 3.10+-only
    glob.glob(root_dir=...) kwarg (see .sdlc/plans/106.md Design decision A; .sdlc/plans/103.md
    Design decision J is the exact prior incident this avoids repeating).

    Non-directory matches are dropped -- a repo IS a directory, and glob.glob has no
    directories-only mode of its own (a trailing '/' in the pattern is NOT special to it).
    Zero matches -> [] (glob.glob never raises on an empty/malformed pattern, verified; the
    caller in __main__.py decides what a zero-match result means for exit code purposes).
    Sorted for deterministic, reproducible run-to-run output ordering.

    `pattern`'s leading '~'/'~user' is expanded (os.path.expanduser) BEFORE the cwd join --
    code-review fold-in, issue #106: --repos's own --help correctly tells users to single-quote
    the pattern (stopping the SHELL from expanding the glob itself), but a single-quoted '~'
    is ALSO not expanded by the shell, so an unexpanded literal '~' reached this function and
    silently matched zero directories. expanduser() is a no-op for a pattern with no leading
    '~', so this changes nothing for the common case; a non-string pattern still falls through
    to the existing except clause below (expanduser raises TypeError for one exactly as
    glob.glob would)."""
    try:
        pattern = os.path.expanduser(pattern)
        matches = sorted(glob.glob(os.path.join(str(cwd), pattern)))
    except (TypeError, ValueError):
        return []
    return [pathlib.Path(m) for m in matches if pathlib.Path(m).is_dir()]


def is_adopted(project_root):
    """True/False/None: whether <project_root>/.sdlc is a directory -- the same precondition
    ingest_artifacts/ingest_gh_reader already read config/goals from, made explicit and checked
    BEFORE any reader runs, so a non-adopted repo never even reaches them.

    None means "cannot confirm" -- NOT "no .sdlc". BLOCKING finding from code review: `is_dir()`
    only swallows ENOENT/ENOTDIR/EBADF/ELOOP internally (returns False for those); every OTHER
    OSError -- PermissionError (EACCES) chief among them -- propagates. Reproduced live, this
    session, on Python 3.9.6 (this codebase's floor): a `chmod 0o000` sibling directory raised
    PermissionError straight out of __main__.py's list comprehension, BEFORE open_store() was
    even called, crashing the whole --repos run and taking down every other (readable) repo in
    it, including ones sorted ahead of the bad one -- a direct defeat of this story's own
    per-repo failure-isolation requirement (Design decision F).

    Guarded here, at the ONE function every caller routes through -- the same "guard the
    computation, degrade the record" shape insight/ingest/ledger_reader.py's own
    _glob_records/_telemetry_share_is_off already use for this identical class of
    Path.is_dir()/exists() OSError propagation. Returning False here (instead of None) would be
    WORSE than the crash it replaces: a directory this process cannot stat might well contain a
    real .sdlc/ -- collapsing "cannot confirm" into "confirmed not adopted" would silently
    mislabel it SKIP_NO_SDLC, a wrong, misleading fact recorded as if it were a checked one.
    Callers use SKIP_UNREADABLE (below) for this case, kept structurally distinct from
    SKIP_NO_SDLC so a query never conflates "we checked, there's no .sdlc" with "we couldn't
    check at all". See .sdlc/plans/106.md Design decision D and this issue's own code review."""
    try:
        return (pathlib.Path(project_root) / ".sdlc").is_dir()
    except OSError:
        return None


#: Mirrors git_reader.py's own no_git naming shape: names the missing PRECONDITION, not a
#: symptom. A repo with no .sdlc/ directory at all was never adopted -- distinct from "adopted
#: but genuinely empty" (which discover_goal_files already returns 0 goals for, unchanged).
#: See .sdlc/plans/106.md Design decision D.
SKIP_NO_SDLC = "no_sdlc"

#: is_adopted() returned None: the directory could not be stat'd at all (PermissionError or any
#: other non-ENOENT-shaped OSError) -- a DIFFERENT fact than SKIP_NO_SDLC ("we checked, and
#: there is no .sdlc/"). Kept as its own code, never folded into SKIP_NO_SDLC, so a reader of
#: dim_project.skip_reason can tell "confirmed non-adoption" from "adoption status unknown"
#: apart. See is_adopted's own docstring above and this issue's code review.
SKIP_UNREADABLE = "unreadable"
