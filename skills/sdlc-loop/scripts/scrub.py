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
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"), "[REDACTED:private-key]"),
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
