# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Scans every insight/metrics/*.sql file for a `kind` literal (`kind = 'x'` or `kind IN (...)`)
and confirms it is a real member of insight/contract/vocabulary.json's entries_kinds/event_kinds
union (issue #298, [E15.S4]; dossier finding: seven metrics SQL files hardcode ledger kinds as
bare string literals with nothing computing both sides and comparing them -- Step 9 is what
closes that).

UNION, NOT STREAM-AWARE (named residue, see insight/contract's implement-phase report): this
cannot catch a metrics file using an entries-stream kind where an events-stream kind was
intended, or vice versa -- both sets are checked as one union, matching
tests/test_vocabulary_coverage.py's own precedent of naming a real, accepted limitation rather
than overclaiming precision."""
import json
import pathlib
import re

VOCAB = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "contract" / "vocabulary.json")
    .read_text(encoding="utf-8")
)
KNOWN_KINDS = set(VOCAB["entries_kinds"]) | set(VOCAB["event_kinds"])
METRICS_DIR = pathlib.Path(__file__).resolve().parents[1] / "metrics"

_KIND_EQ = re.compile(r"kind\s*=\s*'([a-z_]+)'", re.IGNORECASE)
_KIND_IN = re.compile(r"kind\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_QUOTED = re.compile(r"'([a-z_]+)'")

# Known, deliberate forward-references: a kind literal in a metrics SQL file that does not (yet)
# exist in the contract vocabulary, each with its own already-existing in-file guardrail comment
# (see insight/metrics/19.sql's own extensive docstring for why it's there and what closes it).
# An unlisted forward-reference fails loudly -- this must never grow silently.
KNOWN_FORWARD_REFERENCES = {
    "19.sql": {"run_stop"},
}


def _kinds_in(text):
    found = set(_KIND_EQ.findall(text))
    for group in _KIND_IN.findall(text):
        found.update(_QUOTED.findall(group))
    return found


def test_every_sql_kind_literal_is_a_real_contract_vocabulary_member():
    offenders = {}
    for path in sorted(METRICS_DIR.glob("*.sql")):
        found = _kinds_in(path.read_text(encoding="utf-8"))
        unknown = found - KNOWN_KINDS - KNOWN_FORWARD_REFERENCES.get(path.name, set())
        if unknown:
            offenders[path.name] = sorted(unknown)
    assert not offenders, (
        f"metrics SQL file(s) reference a kind literal not in insight/contract/vocabulary.json "
        f"and not in KNOWN_FORWARD_REFERENCES: {offenders}"
    )


def test_the_forward_reference_allowlist_is_not_stale():
    """The mirror check: every allowlisted (file, kind) pair must still actually be UNKNOWN to
    the contract and still actually appear in that file -- an allowlist entry that stopped being
    true (the kind was added to EVENT_KINDS, or the line was deleted) should be noticed, not sit
    there forever granting a pass nothing needs anymore."""
    for filename, kinds in KNOWN_FORWARD_REFERENCES.items():
        text = (METRICS_DIR / filename).read_text(encoding="utf-8")
        for kind in kinds:
            assert kind not in KNOWN_KINDS, f"{kind!r} is now a real vocabulary member -- remove it from KNOWN_FORWARD_REFERENCES"
            assert kind in _kinds_in(text), f"{filename} no longer references {kind!r} -- remove this allowlist entry"
