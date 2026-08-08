"""Secret-shaped redaction shared by the scripts/ backlog collectors (mirror + cross-check).

Location-only capture rule: never emit a matched secret VALUE — each match becomes a typed
placeholder, not the value. These are the same patterns `hooks/research_capture.py` applies to
web-research excerpts; the hook keeps its own inlined copy so it stays path-independent (a hook and a
skill script can't reliably import across the plugin layout), so keep the two in sync when either
changes. Best-effort (pattern-based, not a guarantee) — which is why anything this feeds (the board
mirror) is ALSO gitignored (defense in depth)."""
import re

#: NO \b anchors on the shape tokens — the prefixes (AKIA / gh[pousr]_ / eyJ…) are specific enough to
#: stand alone, and a leading \b would let a secret GLUED to a preceding word char (e.g. "id=AKIA…")
#: slip through. Bare "token" is deliberately absent (it over-redacts ordinary prose like "token
#: budget"); a real `token: <value>` assignment is caught by the key:value rule instead.
_SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.DOTALL),
     "[REDACTED:private-key]"),
    #: Fallback for a key whose END marker never arrives — truncated at the source, or cut mid-capture.
    #: It has to consume the BODY, not just the header: replacing the header alone published every line
    #: underneath it. Whitespace is `(?:\\[rn]|\s)` because the dominant carriers serialize first —
    #: json.dumps()/str() turn the key's newlines into literal \n ESCAPES, and a real-whitespace pattern
    #: stops dead at the first one. Proc-Type:/DEK-Info: are the RFC1421 encrypted-PEM headers that sit
    #: between header and body; that list is closed and colon-anchored (Subject:/Version:/Comment: are
    #: deliberately absent — they open ordinary prose). The body is a run of long base64 lines, so
    #: `{16,}` stops at short prose and a document merely DISCUSSING the marker keeps its text; the
    #: `{1,15}` tail then takes a mid-line truncation fragment ONLY at end-of-input.
    #:
    #: WHAT THIS DOES AND DOES NOT COVER — the residual is NOT bounded to a fixed number of characters:
    #: consumption walks recognized headers and long base64 runs and STOPS at the first sub-16-char run
    #: or any non-base64 byte inside the body. Everything from that gap onward SURVIVES, however much
    #: of it there is. So a CANONICAL unterminated key — a clean body cut short at the source, which is
    #: the case this fallback exists for — is consumed in full; a MANGLED body (a foreign byte or a
    #: short run partway down) is redacted only as far as the gap, and complete key lines after it are
    #: published. Terminated keys never reach here at all: the DOTALL BEGIN..END pattern above owns
    #: them outright. The same stop rule is why an UNRECOGNIZED header line (e.g. `Comment:`) between
    #: BEGIN and the body halts consumption immediately and leaves the whole body — accepted on
    #: purpose, because the alternative is open-ended header matching that would eat ordinary prose.
    #: Both costs are pinned by test so the next reader meets the real behavior, not a comforting
    #: bound. Widening the run rule, or opening the header list, is a deliberate decision with its own
    #: false-positive price — not a typo to fix in passing.
    #: No possessive/atomic groups: those are 3.11+, and CI still runs 3.10.
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"
                r"(?:(?:\\[rn]|\s)*(?:Proc-Type|DEK-Info):[^\n]{0,120})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{16,})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{1,15}(?=(?:\\[rn]|\s)*$))?"),
     "[REDACTED:private-key]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"), "[REDACTED:gh-token]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\b(?:bearer|basic|digest)\s+[A-Za-z0-9+/=._\-]{8,}"), "[REDACTED:auth]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|"
                r"access[_-]?token|authorization|token|secret|password|passwd|pwd)\b[\"']?\s*[:=]\s*"
                r"[\"']?[^\s\"'<>&]{4,}"),
     r"\1: [REDACTED]"),
)


def scrub(text):
    """Redact secret-shaped substrings, never emitting the matched value. Returns text unchanged when
    empty/None-ish (callers pass strings)."""
    if not text:
        return text
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text
