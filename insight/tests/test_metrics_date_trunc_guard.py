# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Mechanical guard for the date_trunc duckdb-version divergence (#108's retro assigned this to
#109). Pure stdlib, no duckdb import -- scans insight/metrics/*.sql as text.

LIMITATION, stated explicitly (see .sdlc/plans/109.md Design decision G): this is a FILE-level
check, not a per-occurrence one. A tighter "date_trunc( must be immediately wrapped by
CAST(...AS DATE)" rule was tried and rejected -- it produces a false positive against metric 7's
own correct SQL, which casts the *outer* generate_series(...)/unnest(...) expression to DATE
rather than each date_trunc(...) call individually (verified live: generate_series with an
INTERVAL step promotes DATE bounds back to TIMESTAMP internally, so an individual per-call cast
does not actually pin the final type -- only the outer cast does). This guard therefore only
proves a file that calls date_trunc() also mentions AS DATE somewhere -- it does not prove every
occurrence is individually safe. That is a real, accepted limitation, not an oversight.

POST-PR-REVIEW BLOCKING FIX: an independent, author-blind review demonstrated the original
version of this guard checked the RAW file text, comments included -- so a file's own
explanatory prose (e.g. `1.sql`'s header commentary, which spells out "CAST(... AS DATE)" as
plain English while explaining the DEVIATION) could satisfy the substring check even when the
QUERY BODY'S real cast had been stripped out. Reproduced live: deleting the real
`CAST(date_trunc(...) AS DATE)` from a scratch copy of `1.sql`'s SELECT while leaving its header
comment untouched left the OLD guard passing -- a false negative on exactly the regression this
test exists to catch. Fixed by stripping every `-- ...` line comment (SQL's own line-comment
syntax) before searching, so only the executable query body is examined -- a comment's wording
can no longer satisfy or defeat the check either way. KNOWN, ACCEPTED LIMITATION of the comment
stripper itself: a literal `--` embedded inside a string literal would be mis-treated as a
comment starting mid-line; none of this catalog's files need one today, and this guard only
ever reads this project's own metric SQL, not arbitrary external input, so the risk is
theoretical here -- named rather than silently assumed away."""
import pathlib

METRICS_DIR = pathlib.Path(__file__).parent.parent / "metrics"


def _sql_body_only(text):
    """Strip SQL `-- ...` line comments (header fields AND any trailing/inline commentary)
    so a comment's own wording can never satisfy -- or defeat -- a check meant to examine the
    executable query. Everything from the first `--` on a physical line onward is dropped;
    everything before it survives."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_every_date_trunc_using_metric_file_also_casts_to_date():
    offenders = []
    for path in sorted(METRICS_DIR.glob("*.sql")):
        body = _sql_body_only(path.read_text(encoding="utf-8-sig"))
        if "date_trunc(" in body and "AS DATE)" not in body:
            offenders.append(path.name)
    assert offenders == [], (
        f"{offenders} call date_trunc() without ever casting to DATE anywhere in the QUERY BODY "
        "(comments excluded) -- date_trunc's return type (date vs datetime) has changed across "
        "duckdb versions before (see .sdlc/plans/108.md's own DEVIATION note in 1.sql); wrap the "
        "result in CAST(... AS DATE) so the view's output type is pinned regardless of the "
        "installed duckdb patch. Note: a file's own explanatory COMMENTS do not count -- only "
        "the real SQL body does (see this test's own module docstring for why)."
    )
