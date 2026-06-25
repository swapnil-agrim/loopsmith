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


def _slug(text, n=48):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "untitled").strip("-")


def _kg_enabled(project_dir):
    try:
        cfg = json.loads((Path(project_dir) / ".sdlc" / "config.json").read_text(encoding="utf-8"))
        return bool((cfg.get("knowledge_graph") or {}).get("enabled"))
    except Exception:
        return False


def build_breadcrumb(tool_name, tool_input, tool_response):
    """Pure: (relative_path, markdown) for a web tool, else None."""
    if tool_name not in _WEB_TOOLS:
        return None
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="microseconds")
    stamp = now.strftime("%Y-%m-%dT%H%M%S-%f")          # collision-safe to the microsecond
    subject = tool_input.get("query", "") if tool_name == "WebSearch" else tool_input.get("url", "")
    body = tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False)
    md = ("---\n"
          f"source: {tool_name.lower()}\n"
          f"subject: {json.dumps(subject, ensure_ascii=False)}\n"
          f"captured_at: {ts}\n"
          "contributor: loopsmith\n"
          "---\n\n"
          f"# {tool_name}: {subject}\n\n"
          f"{body[:4000]}\n")
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
