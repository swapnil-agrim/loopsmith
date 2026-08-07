#!/usr/bin/env python3
"""Goal-size classifier — deterministic, zero-LLM detection of an oversized ("epic-shaped") goal
before the loop ever spends a token on it. Same shape as sdlc-model/scripts/predict.py (ordered
signals, first match wins, pure functions, module-constant thresholds), but a different axis: not
WHICH model a goal deserves, WHETHER a goal is actually several goals wearing one issue number.

Lives BESIDE loop.py (not under skills/sdlc-model/, despite the family resemblance to predict.py):
loop.py's own module loader (`_load()`, loop.py:26-28) resolves every sibling module from its OWN
directory only, so a classifier imported cross-skill would need a second loader — this repo's "one
_load() per script, same-directory only" convention deliberately does not have one.

v1 signals, in order (first hit wins): body length (word count, then line count) -> independent
`## ` sections -> top-level checkboxes -> explicit multi-phase structure. Every threshold below is
a provisional constant, not a tuned one — this slice ships fixture-only tests; the real historical
corpus validates (and, if needed, retunes) these numbers in a follow-up slice, exactly as the
`goal_decompose` config explainer tells the operator.

ponytail: same as predict.py's — a known-coarse heuristic with an explicit, versioned upgrade path
(retune the constants, or replace with a corpus-fit model) rather than an LLM call on this path,
which must stay zero-latency, zero-cost, and hermetically testable."""
import re

# --- signal 1: body length ----------------------------------------------------------------------
WORD_THRESHOLD = 800     # body word count strictly above this alone flags the goal
LINE_THRESHOLD = 150     # body line count strictly above this alone flags the goal (a dense
                          # checklist/table can be long-and-thin rather than long-and-wordy)

# --- signal 2: independent top-level sections ---------------------------------------------------
SECTION_THRESHOLD = 3     # `## ` headings (H2 only; a `###` subsection never counts) at or above
                          # this reads as multiple independent scopes stapled into one issue

# --- signal 3: top-level checkboxes ---------------------------------------------------------------
CHECKBOX_THRESHOLD = 4    # non-indented `- [ ]`/`- [x]` (or `*` bullets) at or above this reads as
                          # multiple distinct deliverables, not one goal's own sub-steps

_SECTION_RE = re.compile(r"^##(?!#)[ \t]+\S.*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^[-*][ \t]\[[ xX]\][ \t]+\S", re.MULTILINE)
_PHASE_RE = re.compile(r"\bphase[ \t-]*([0-9]+)\b", re.IGNORECASE)


def classify(body):
    """Classify one goal's BODY (never the title — see loop.py's decompose_check for why the
    caller never passes one). Ordered signals, first match wins. Returns
    `(flagged: bool, reason: str)`; `reason` is always a short, single-line phrase — it lands
    verbatim in a park detail / local action-log field, and both reject an embedded newline, so no
    branch here may ever build a multi-line reason."""
    text = body or ""

    words = len(text.split())
    if words > WORD_THRESHOLD:
        return True, f"body is {words} words (over {WORD_THRESHOLD})"

    lines = len(text.splitlines())
    if lines > LINE_THRESHOLD:
        return True, f"body is {lines} lines (over {LINE_THRESHOLD})"

    sections = len(_SECTION_RE.findall(text))
    if sections >= SECTION_THRESHOLD:
        return True, f"{sections} independent ## sections (>= {SECTION_THRESHOLD})"

    checkboxes = len(_CHECKBOX_RE.findall(text))
    if checkboxes >= CHECKBOX_THRESHOLD:
        return True, f"{checkboxes} top-level checkboxes (>= {CHECKBOX_THRESHOLD})"

    phases = sorted(set(_PHASE_RE.findall(text)), key=int)
    if len(phases) >= 2:
        return True, "explicit multi-phase structure (Phase " + "/".join(phases) + ")"

    return False, ""
