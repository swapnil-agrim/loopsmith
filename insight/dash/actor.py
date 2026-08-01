# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Resolve "who is asking" for a privacy-scoped dashboard view (issue #126, E4.S3, Task 1).

Two sources, checked in order: an explicit `--actor` flag, else `.sdlc/config.json`'s
`ledger.actor`. Neither `gh api user` nor `$USER`/`$LOGNAME` is consulted (see the Rejected
paragraph below) -- unlike `skills/sdlc-loop/scripts/ledger.py`'s own `actor()`, which falls all
the way through to those and finally to the literal `"unknown"`. This module CANNOT import that
function: `tests/test_import_boundary.py` forbids `insight/` from importing `skills`/`hooks` in
either direction (AST-enforced, both ways), so `_safe_name` below is a deliberate reimplementation
of `ledger.py`'s own sanitizer, not a shortcut around the boundary. `read_config_snapshot`
(`insight/ingest/artifact_reader.py:169`) is the existing precedent for the *allowed* coupling:
read `config.json` off disk directly, tolerate absence/malformity, never import the code that also
reads it.

**Rejected: falling through to `gh api user` / `$USER` when config is silent.** `insight dash` is
store-only and offline-safe today -- no module under `insight/dash/` shells out or touches the
network. Adding that here would make a PRIVACY-SENSITIVE view's correctness depend on network/auth
state, and would answer a different question than the one that matters: `gh`/`$USER` say who the
*machine* is authenticated as, not who *this project's own ledger* calls the actor. Guessing wrong
on a page whose entire job is "own data only" is the worst available failure mode, so this module
narrows to the two sources that are genuinely this project's own record of who's asking.

**Rejected: falling through to a guessed/default identity (e.g. `"unknown"`) instead of raising.**
`ledger.py`'s own fallback chain ends in `"unknown"` because a ledger *write* must never be lost.
A dashboard *read* has the opposite risk profile -- rendering under a fabricated identity either
shows nobody's data (useless) or, worse, silently shows a leftover actor's data if `"unknown"`
happens to collide with a real `actor_id` some other process wrote. Unresolvable must be loud
(`ActorResolutionError`), never defaulted.

**Named limitation this module cannot detect, and does not try to (issue #126 plan, Risks
section):** resolution only proves a *name was configured or passed* -- it has no way to prove
that name is the actual identity of whoever is now looking at the rendered page. A stale,
templated, or copy-pasted `config.json` can silently resolve to a *different real teammate's*
`ledger.actor`, and the IC view then renders cleanly, self-contained, structurally correct -- under
a wrong-but-real identity. This is sharper than "many viewers share one URL": a SINGLE viewer,
alone, with nothing else wrong, can be shown someone else's data because the config on disk names
someone else. Nothing in `resolve_actor` (or anywhere downstream) authenticates the *viewer*; it
only resolves whichever identity the *builder* (the machine/process running `insight dash`)
supplied. See `insight/dash/ic.py`'s own module docstring for how this composes with the SQL-level
per-row filter, which is correct FOR the resolved actor regardless of whether that actor is really
the person reading the page.
"""
import json
import pathlib


class ActorResolutionError(Exception):
    """Raised by `resolve_actor` when no actor can be resolved from either source. Caught by
    `insight/__main__.py`'s `dash` branch: prints a `WARNING`-prefixed line to stdout (never
    stderr -- matches `render.py`'s existing cold-start banner convention) and skips writing
    `ic.html`, but does NOT fail the `insight dash` command as a whole. Never caught silently to
    render under a guessed identity -- see this module's docstring."""


def _safe_name(who):
    """Reimplementation (not an import -- see module docstring) of
    `skills/sdlc-loop/scripts/ledger.py`'s own `_safe_name`: keep only `[A-Za-z0-9._-]`, strip
    leading/trailing `.`/`-`/`_`, cap at 64 chars, default to `"unknown"` if nothing survives. A
    GitHub login never contains a path separator, but this is defensive anyway so a bad config
    value (or a hostile `--actor` flag) can never be used to construct an unsafe path anywhere
    downstream -- e.g. path traversal characters (`../../etc`) are stripped to `etc`."""
    keep = [c for c in str(who) if c.isalnum() or c in "-_."]
    return ("".join(keep).strip(".-_") or "unknown")[:64]


def resolve_actor(sdlc_dir, explicit=None):
    """Resolve the acting identity: `explicit` (the `--actor` flag) wins if given, sanitized via
    `_safe_name`. Otherwise, read `<sdlc_dir>/config.json`'s `ledger.actor` field (mirrors only the
    CONFIG branch of `ledger.py`'s own `actor()` -- see module docstring for why the `gh`/`$USER`
    fallback is deliberately not reimplemented here). Raises `ActorResolutionError` if neither
    resolves to a non-blank value.

    Never raises for a missing or malformed `config.json` -- that degrades to "not configured",
    exactly like `read_config_snapshot`'s own `OSError`/`ValueError` handling
    (`insight/ingest/artifact_reader.py:169`), not a crash.
    """
    if explicit:
        return _safe_name(explicit)
    config_path = pathlib.Path(sdlc_dir) / "config.json"
    try:
        raw = config_path.read_text(encoding="utf-8-sig", errors="replace")
        config = json.loads(raw)
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    ledger = config.get("ledger")
    configured = ((ledger.get("actor") if isinstance(ledger, dict) else None) or "").strip()
    if configured:
        return _safe_name(configured)
    raise ActorResolutionError(
        "no actor resolved: pass --actor, or set ledger.actor in .sdlc/config.json"
    )
