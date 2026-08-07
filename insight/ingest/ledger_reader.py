# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Ledger reader (issue #101, E1.S3): the union of ledger/entries/*.jsonl and
ledger/events/*.jsonl, oldest-first, plus .sdlc/events/*.jsonl when telemetry.share is off.

READS ONLY -- this module still performs no DuckDB writes itself (no `import duckdb` anywhere
below). `insight/ingest/ledger_writer.py` (issue #105, E1.S7) is the persistence story .sdlc/
plans/101.md §F named as "later": it imports `read_all_with_reliability` (below), not `read_all`
directly, so it can tag each record with the reliability class its OWN source stream carries
(spec §3) without every other `read_all` caller having to filter an extra key out of each dict.
`read_all` itself is untouched by that story -- same signature, same return shape, same tests.

Reimplements skills/sdlc-loop/scripts/ledger.py:read_all from scratch (never imported — the
plugin/product boundary in tests/test_import_boundary.py forbids it, and this module is zero
duckdb, following collectors.py's shape, so insight/tests/test_ledger_reader.py runs on any box).

FIVE DELIBERATE DIVERGENCES from read_all, all load-bearing, all explained in .sdlc/plans/101.md.
Every one converges on the same move: guard the COMPUTATION, degrade the RECORD -- rather than
enumerating one more exception type. 1-3 are listed here; 4 and 5 are documented at their sites.
  1. Files are opened with encoding="utf-8-sig", errors="replace" (never "utf-8" alone) --
     read_all's plain "utf-8" + catch-only-OSError lets a UnicodeDecodeError (a ValueError, NOT an
     OSError) kill the entire read, contradicting its own "never fatal" docstring; a BOM also eats
     read_all's first line silently. See plan §A.
  2. json.loads is wrapped in except (ValueError, RecursionError), not just ValueError -- a deeply
     nested line raises RecursionError, which is a RuntimeError and NOT a ValueError, so read_all's
     bare `except ValueError` lets it propagate and discard every record already read from every
     file. The depth that trips it is interpreter-dependent -- roughly 1000 on <=3.11 but about
     10000 on 3.12+, whose C parser raises far later -- which is precisely why this catches the
     TYPE instead of testing a threshold. read_all has this hole today. See plan §E step 1.1 and
     the "deeply_nested" fixture.
  3. The (ts, actor, seq) sort key coerces ts/actor through str() (missing/None/"" -> "") rather
     than comparing raw values -- read_all's raw compare raises TypeError the moment two records
     disagree on ts's TYPE (e.g. one string, one number), which is fatal for the whole sort, not
     just one line. See plan §D.
"""
import json
import pathlib


def _seq(entry):
    """Identical to ledger._seq: the tail of `id` after the last ':', 0 if not all-digits.
    `str(...)` on a non-string id (a number, a list) never raises -- it just won't look like a
    valid tail and falls through to 0.

    Divergence 4. `tail.isdigit()` is NOT enough to make int() safe: since 3.10.7/3.11, int()
    refuses an over-long digit string (the CVE-2020-10735 fix) and raises ValueError. Both CI
    interpreters are past that. An `id` tail of 5000 digits takes out the whole sort -- every
    record, every file -- which is what `ledger._seq` still does.

    Catching beats comparing against the limit: the threshold is not a constant (any caller can
    move it with sys.set_int_max_str_digits), so a hardcoded number would be silently wrong for
    anyone who lowers it. Degrading only `seq` here, rather than letting _sort_key's catch-all
    take the whole key, keeps a record with an absurd id ordered by its real ts."""
    ident = str(entry.get("id", ""))
    tail = ident.rsplit(":", 1)[-1]
    if not tail.isdigit():
        return 0
    try:
        return int(tail)
    except ValueError:
        return 0


def _sort_key(entry):
    """The whole key, guarded as one unit. Every crash this reader has had was a sort key that
    raised on some value nobody enumerated: a mixed type, then a 4301-digit id. Enumerating the
    next one is a losing game -- an unsortable record sorts first instead of destroying the read."""
    try:
        return (_sort_str(entry.get("ts")), _sort_str(entry.get("actor")), _seq(entry))
    except Exception:
        return ("", "", 0)


def _sort_str(value):
    """Coerce a sort-key field to str so mixed-type ts/actor values across records/files can
    still be compared -- see plan §D. Missing/None/"" all collapse to "" (sorts first), matching
    read_all's default-first ordering without its cross-type TypeError crash."""
    return "" if value in (None, "") else str(value)


def _read_jsonl_records(path):
    """One file -> list of kept dicts. Every tolerance rule lives here ONCE, so all three globs
    (entries, ledger events, local events) get identical treatment -- see plan §E."""
    out = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out  # missing, a directory, unreadable, or any other OS-level failure: skip, not fatal
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            # Divergence 2, same reasoning as the others. A deeply nested
            # deep makes json.loads raise RecursionError, which is a RuntimeError — NOT a
            # ValueError — so `except ValueError` misses it and it propagates out of read_all,
            # discarding every record already accumulated from every file. `ledger.read_all` has
            # this exact hole; a plan-reviewer built the line and crashed both. "Skipped not
            # fatal" is the done_when, so it is caught here.
            continue
        if isinstance(item, dict) and item.get("kind"):
            out.append(item)
    return out


def _glob_records(directory):
    """Divergence 5, same shape as the other four: guard the computation, degrade the record.

    `Path.exists()` swallows only ENOENT/ENOTDIR/EBADF/ELOOP — every other OSError propagates, so a
    directory the process cannot stat (mode 000, or a component over NAME_MAX) escaped `read_all`
    and aborted the caller. The consumer is `insight ingest --repos <glob>`, a multi-repo walk,
    where one unreadable checkout would take down the entire ingest. `glob()` already returns []
    for a missing directory, so the existence check was never needed.
    """
    try:
        paths = sorted(directory.glob("*.jsonl"))
    except OSError:
        return []
    out = []
    for path in paths:
        out.extend(_read_jsonl_records(path))
    return out


def _telemetry_share_is_off(sdlc_dir):
    """See plan §B. Fails open to False (sharing ON, the shipped default) on any missing/
    unreadable/malformed config, or a missing/wrong-typed telemetry/share key -- only an explicit
    JSON `false` turns sharing off."""
    try:
        raw = (pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8-sig", errors="replace")
        config = json.loads(raw)
    except (OSError, ValueError):
        return False
    telemetry = config.get("telemetry") if isinstance(config, dict) else None
    share = telemetry.get("share") if isinstance(telemetry, dict) else None
    return share is False


def read_all(sdlc_dir):
    """The union of ledger/entries/*.jsonl and ledger/events/*.jsonl under sdlc_dir, oldest first,
    plus sdlc_dir/events/*.jsonl (the local .sdlc/events/ path) additionally when telemetry.share
    is off (spec §A.2/§B.2: ADDITIVE, never a fallback -- see plan §B). Never raises."""
    base = pathlib.Path(sdlc_dir)
    records = []
    records.extend(_glob_records(base / "ledger" / "entries"))
    records.extend(_glob_records(base / "ledger" / "events"))
    if _telemetry_share_is_off(base):
        records.extend(_glob_records(base / "events"))
    records.sort(key=_sort_key)
    return records


def seq_of(entry):
    """The ledger id's own per-author sequence number -- promoted from private (issue #105,
    same move .sdlc/plans/104.md Design decision G already made for git_reader.to_utc_naive)
    so insight/ingest/ledger_writer.py's resume cursor reuses the SAME hardened parser
    _sort_key already trusts (an absurd or malformed id tail degrades to 0, never raises --
    see _seq's own docstring for the exact tolerance rules: divergence 4 from ledger.py's own
    read_all), rather than a second, drifting reimplementation of "the tail after the last
    ':'." Behaviour is byte-for-byte _seq -- this is a name, not a rewrite."""
    return _seq(entry)


#: The `stream` tag (issue #380) read_all_with_reliability stamps on every record, one value per
#: source glob. 'entries'/'events' are ledger.py's own STREAMS constants, verbatim. 'local-events'
#: names the third glob -- the local `<sdlc>/events/` directory, read only when telemetry.share is
#: off -- which ledger.py has no constant for because nothing in the plugin writes it; the reader
#: accepts it, so it needs a name of its own here.
STREAM_ENTRIES = "entries"
STREAM_EVENTS = "events"
STREAM_LOCAL_EVENTS = "local-events"


def read_all_with_reliability(sdlc_dir):
    """Same union and the same oldest-first (ts, actor, seq) sort as read_all, but each
    returned dict gains two extra keys.

    'reliability_class': 1 for a record read from ledger/entries/ (Python-controlled -- an actor
    cannot forget to write a lifecycle line, spec §3's Class 1), 2 for one from ledger/events/ or
    sdlc_dir/events/ (agent-emitted, best-effort, Class 2).

    'stream' (issue #380): which of the three globs the record was actually READ from --
    'entries', 'events' or 'local-events'. reliability_class cannot stand in for it: the second
    and third globs are BOTH class 2, yet they are different directories, and ledger.py derives a
    record's `seq` from the line count of the one file it is appending to -- so each glob is its
    own independent counter. insight/ingest/ledger_writer.py's resume cursor keys on this, and
    without it the streams' seq spaces collide and the shorter one is silently swallowed whole.

    Both tags are stamped from the glob, never read from the record. A ledger line is a
    hand-editable file, and one carrying its own "stream": "entries" while sitting in
    ledger/events/ must not be able to talk itself onto another counter's watermark -- so the
    keyword arguments below deliberately OVERRIDE any same-named key already in the record.

    This is the ONE place the distinction is made, so ledger_writer.py (and anything after it)
    never has to re-derive "which stream did this come from" from a merged list that has already
    forgotten -- read_all's own return value carries no such marker, by design (every other
    current caller -- open_claims, render, team, addressed_to's ledger.py equivalents -- treats
    the union as one undifferentiated stream, and adding an unrequested key to read_all's own
    dicts would be an unrelated, untested behaviour change to a function three other stories
    already depend on byte-for-byte).

    NOT a rewrite of read_all's own internals: same three globs (ledger/entries, ledger/events,
    sdlc_dir/events gated on telemetry.share is off), same _glob_records/_telemetry_share_is_off
    helpers, same _sort_key. Only the per-record tags and the point at which they are attached are
    new. See issue #105 and the module docstring above.

    An earlier revision of this docstring noted that ledger.py had no `stream` parameter yet
    ("that is #136, E6.S1") and that every real write therefore landed in ledger/entries/. That is
    no longer true: #136 (PR #241) added `stream=` to ledger.py -- its own done_when reads "ids
    stay monotonic per (actor, stream)" -- and PR #242 made sync.py publish the second stream to
    the shared ledger branch, which is what put both streams' records in front of this reader."""
    base = pathlib.Path(sdlc_dir)
    tagged = []
    for rec in _glob_records(base / "ledger" / "entries"):
        tagged.append(dict(rec, reliability_class=1, stream=STREAM_ENTRIES))
    for rec in _glob_records(base / "ledger" / "events"):
        tagged.append(dict(rec, reliability_class=2, stream=STREAM_EVENTS))
    if _telemetry_share_is_off(base):
        for rec in _glob_records(base / "events"):
            tagged.append(dict(rec, reliability_class=2, stream=STREAM_LOCAL_EVENTS))
    tagged.sort(key=_sort_key)
    return tagged
