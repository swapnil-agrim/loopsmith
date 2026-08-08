#!/usr/bin/env python3
"""PostToolUse research-capture hook (optional KG feature). For every WebSearch/WebFetch, append a
provenance breadcrumb so external research auto-accumulates into the knowledge corpus — but ONLY when
the current project opted in via .sdlc/config.json -> knowledge_graph.enabled.

This hook ships in the plugin and fires on web tools in EVERY project, so it is fail-open and
side-effect-only: it never blocks or errors a tool call, and it is a fast no-op for any project that
did not opt in (no .sdlc, KG disabled, missing/garbage config). Breadcrumbs land under
.sdlc/knowledge/research/web/<microsecond-stamp>-<slug>.md (stamp = collision-safe)."""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

_WEB_TOOLS = {"WebSearch", "WebFetch"}

#: How much of the (scrubbed) response we keep — a provenance summary, never the raw page.
_EXCERPT_CHARS = 400

#: Secret-shaped substrings, redacted before anything reaches disk. Same rule the location-only risk
#: collectors follow: NEVER write the matched substring — each match becomes a typed placeholder, not
#: the value. Token-shape patterns are quote-insensitive so they fire inside JSON bodies too. This is
#: best-effort (pattern-based, not a guarantee), which is why `.sdlc/knowledge/` is also gitignored.
_SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.DOTALL),
     "[REDACTED:private-key]"),
    # Fallback for a key whose END marker never arrives — truncated at the source, or cut mid-capture.
    # It has to consume the BODY, not just the header. Whitespace is `(?:\\[rn]|\s)` because a dict
    # tool_response is json.dumps'd below, which turns the key's newlines into literal \n ESCAPES; a
    # real-whitespace pattern stops at the first one and publishes the rest. Proc-Type:/DEK-Info: are
    # the RFC1421 encrypted-PEM headers between header and body (closed, colon-anchored list). `{16,}`
    # stops the body run at short prose, so a page discussing the marker keeps its text; the `{1,15}`
    # tail takes a truncation fragment ONLY at end-of-input.
    #
    # WHAT THIS DOES AND DOES NOT COVER — the residual is NOT bounded to a fixed number of characters:
    # consumption walks recognized headers and long base64 runs and STOPS at the first sub-16-char run
    # or any non-base64 byte inside the body, and everything from that gap onward SURVIVES, however
    # much of it there is. A CANONICAL unterminated key (a clean body cut short at the source — the
    # case this fallback exists for) is consumed in full; a MANGLED body (foreign byte or short run
    # partway down) is redacted only as far as the gap. Terminated keys never reach here — the DOTALL
    # BEGIN..END pattern above owns them. The same stop rule means an UNRECOGNIZED header line (e.g.
    # `Comment:`) between BEGIN and the body halts consumption and leaves the whole body: accepted on
    # purpose, since open-ended header matching would eat ordinary prose. Both costs are pinned in
    # tests/test_scrub.py. Keep this tuple identical to scrub.py's (that file's parity test enforces
    # it); comments may differ.
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"
                r"(?:(?:\\[rn]|\s)*(?:Proc-Type|DEK-Info):[^\n]{0,120})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{16,})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{1,15}(?=(?:\\[rn]|\s)*$))?"),
     "[REDACTED:private-key]"),
    # Distinctive-shape tokens: NO \b anchors — the prefixes (AKIA / gh[pousr]_ / eyJ...) are specific
    # enough to stand alone, and a leading \b would let a secret GLUED to a preceding word char
    # (e.g. "id=AKIA...", "x-ghp_...") slip through unredacted. Shape alone must catch it.
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"), "[REDACTED:gh-token]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    # Auth material by keyword — runs BEFORE the key:value rule so a "Basic <base64>" value can't be
    # orphaned by the key rule redacting only the keyword. base64 padding (+/=) is in the value class.
    # NOTE: bare "token" is deliberately NOT here — it's the single most common word in the research this
    # feature captures ("token management", "token economics"), so keywording it here over-redacts prose.
    # A real assignment `token: <value>` / `token=<value>` is caught by the key:value rule below instead.
    (re.compile(r"(?i)\b(?:bearer|basic|digest)\s+[A-Za-z0-9+/=._\-]{8,}"), "[REDACTED:auth]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|"
                r"access[_-]?token|authorization|token|secret|password|passwd|pwd)\b[\"']?\s*[:=]\s*"
                r"[\"']?[^\s\"'<>&]{4,}"),
     r"\1: [REDACTED]"),
)


def _slug(text, n=48):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "untitled").strip("-")


def _scrub(text):
    """Redact secret-shaped substrings, never emitting the matched value (location-only capture rule)."""
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _kg_enabled(project_dir):
    # strict: only an explicit boolean true opts in — a stringy "false"/"no" must NOT enable.
    try:
        cfg = json.loads((Path(project_dir) / ".sdlc" / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("knowledge_graph") or {}).get("enabled") is True
    except Exception:
        return False


def build_breadcrumb(tool_name, tool_input, tool_response):
    """Pure: (relative_path, markdown) for a web tool with a subject, else None.

    Security: we persist a provenance breadcrumb — source, subject, and a SHORT, scrubbed excerpt —
    never the raw response body. Raw web bodies can carry tokens/PII; dumping any of it verbatim into
    a git-tracked dir is the leak this closes. The excerpt is scrubbed of secret-shaped substrings and
    THEN capped at `_EXCERPT_CHARS`; `.sdlc/knowledge/` is also gitignored by /sdlc-setup, so even a
    scrubbed breadcrumb stays local unless the adopter deliberately commits it (defense in depth)."""
    if tool_name not in _WEB_TOOLS:
        return None
    raw = tool_input.get("query", "") if tool_name == "WebSearch" else tool_input.get("url", "")
    subject = (raw or "").strip()
    if not subject:
        return None                                     # skip failed/empty web calls — no junk breadcrumbs
    subject = _scrub(subject)                            # a credential in a URL query param / pasted query
                                                         # must not land verbatim in frontmatter/heading/slug
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="microseconds")
    stamp = now.strftime("%Y-%m-%dT%H%M%S-%f")          # collision-safe to the microsecond
    heading = subject.replace("\n", " ").replace("\r", " ")[:200]  # one-line, can't break the markdown
    body = tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False)
    # Scrub the WHOLE body, then cap — the order is load-bearing and matches mirror.py:46's own
    # `scrub(body)[:_EXCERPT_CHARS].strip()`. Truncating first can cut a well-formed key in half and
    # hand the scrubber a BEGIN with no END, i.e. manufacture the degraded input out of a sound one.
    # scrub() is a fixed set of linear-scan regexes over a body this hook already holds in memory.
    excerpt = _scrub(body)[:_EXCERPT_CHARS].strip()
    md = ("---\n"
          f"source: {tool_name.lower()}\n"
          f"subject: {json.dumps(subject, ensure_ascii=False)}\n"
          f"captured_at: {ts}\n"
          "contributor: loopsmith\n"
          "---\n\n"
          f"# {tool_name}: {heading}\n\n"
          f"{excerpt}\n")
    return f".sdlc/knowledge/research/web/{stamp}-{_slug(subject)}.md", md


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        if _kg_enabled(project_dir):
            out = build_breadcrumb(data.get("tool_name", ""),
                                   data.get("tool_input") or {},
                                   data.get("tool_response", ""))
            if out is not None:
                rel, md = out
                dest = Path(project_dir) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(md, encoding="utf-8")
    except Exception:
        pass  # fail-open: never disrupt the session
    sys.exit(0)


if __name__ == "__main__":
    main()
