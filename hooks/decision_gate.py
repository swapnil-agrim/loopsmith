#!/usr/bin/env python3
"""PreToolUse decision gate — the one guardrail in this kit that is NOT a prompt.

Every other phase gate here is discipline a model is asked to follow, and a model can talk itself
past discipline. This one refuses: an edit that assigns a registered invariant a violating value is
DENIED before it happens, by a script that does not negotiate.

Reads a tool call on stdin. For Edit/Write/MultiEdit/NotebookEdit it matches the target file against
`.sdlc/decisions.json` and:

  * DENY   when the changed text assigns a protected param of an `invariant` decision a value that
           violates its typed constraint;
  * ASK    on the same violation of a `recipe` decision, on any edit inside a decision declaring
           `caution_on_touch`, and on any edit to the registry itself (changing an invariant is a
           supersession, never a silent rewrite);
  * ALLOW  (silent) otherwise.

Matching is SCOPED, which is what keeps it from crying wolf: a param name is only checked inside
files matching its OWN decision's protected_paths, only in assignment shape (`name = <literal>` /
`name: <literal>`), with comments stripped. Prose mentions and same-named tokens elsewhere never
trip it, and a new value that still SATISFIES the constraint is allowed.

OFF until a repo authors `.sdlc/decisions.json` — no registry, no behavior. Set
`gates.decision_gate.enabled: false` in `.sdlc/config.json` to disable without deleting the registry
(useful mid-refactor).

Fail-OPEN everywhere: any internal error allows the call. A gate that wedges you on its own bug is
worse than a missed check — `decision_gate.py check` is the backstop that catches what the hook let
through. Zero-dep: the registry is JSON, not YAML, because this kit takes no dependencies.
"""
import ast
import fnmatch
import json
import math
import os
import re
import sys
from pathlib import Path

REGISTRY_REL = ".sdlc/decisions.json"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
OPS = ("eq", "ge", "le", "in")

#: A literal on the right-hand side of an assignment. Anything else (an expression, a name, a call)
#: is deliberately NOT matched — the gate only judges values it can actually read.
_LITERAL = re.compile(r"(-?\d+\.\d+|-?\d+|True|False|true|false|\"[^\"]*\"|'[^']*')")


def project_dir():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / REGISTRY_REL).exists():
        return Path(env)
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / REGISTRY_REL).exists():
            return parent
    return None


def load_registry(root):
    try:
        return json.loads((Path(root) / REGISTRY_REL).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def enabled(root):
    """Authoring a registry IS the opt-in — a config flag you had to discover would make the
    feature silently do nothing. `enabled: false` turns it off without deleting the file."""
    try:
        cfg = json.loads((Path(root) / ".sdlc" / "config.json").read_text(encoding="utf-8"))
        gate = (cfg.get("gates") or {}).get("decision_gate") or {}
        return gate.get("enabled") is not False
    except Exception:
        return True


def matches(rel, patterns):
    """Glob match with the `**` semantics people actually expect.

    `fnmatch` has no concept of path segments, so `src/**/*.py` fails to match `src/a.py` — the
    pattern demands a second slash. Every glob dialect a user has met (gitignore, pathlib, shell
    globstar) treats `**` as *zero* or more directories, so a rule written that way would silently
    protect the nested files and not the top-level ones. Trying the collapsed form covers the
    zero-directory case."""
    for pat in patterns or []:
        if not isinstance(pat, str):
            continue
        collapsed = pat.replace("/**/", "/")
        if collapsed.startswith("**/"):
            collapsed = collapsed[3:]
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, collapsed):
            return True
    return False


def _rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except Exception:
        return Path(path).as_posix()


def _changed_text(tool_name, ti):
    if tool_name in ("Write", "NotebookEdit"):
        return ti.get("content") or ti.get("new_source") or ""
    if tool_name == "Edit":
        return ti.get("new_string") or ""
    if tool_name == "MultiEdit":
        return "\n".join((e.get("new_string") or "") for e in ti.get("edits", []))
    return ""


def _norm(tok):
    if tok in ("true", "false"):
        tok = tok.capitalize()
    return ast.literal_eval(tok)


def satisfies(op, actual, expected):
    """True when `actual` still meets the constraint. Fail-open on any comparison error — a gate
    that denies because it could not compare is worse than one that misses."""
    try:
        if op == "eq":
            if isinstance(expected, bool) or isinstance(actual, bool):
                return actual == expected
            if isinstance(expected, float) or isinstance(actual, float):
                return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12)
            return actual == expected
        if op == "ge":
            return actual >= expected
        if op == "le":
            return actual <= expected
        if op == "in":
            return actual in expected
    except Exception:
        return True
    return True


def _in_string(code, idx):
    """True when position `idx` sits inside a quoted span.

    Without this, `doc = "set timeout: 120 here"` reads as an assignment of 120 and DENIES the edit.
    A false deny costs far more than a miss: it is the thing that teaches people to click through
    the gate, and a gate that gets clicked through protects nothing. Naive (no escape handling),
    which keeps it erring toward allow."""
    quote = None
    for ch in code[:idx]:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
    return quote is not None


def violations(changed, protected_params):
    """Assignments in `changed` that break a constraint: [(name, actual_value, param_spec)]."""
    found = []
    for line in changed.splitlines():
        code = line.split("#", 1)[0]          # naive comment strip; errs toward NOT blocking
        for p in protected_params or []:
            name = p.get("name")
            if not name or p.get("op") not in OPS:
                continue
            m = re.search(rf"(?<![\w.]){re.escape(name)}\s*[=:]\s*(.+)", code)
            if not m or _in_string(code, m.start()):
                continue
            lit = _LITERAL.search(m.group(1))
            if not lit:
                continue                       # not a literal — nothing this gate can judge
            try:
                actual = _norm(lit.group(1))
            except Exception:
                continue
            if not satisfies(p["op"], actual, p.get("value")):
                found.append((name, actual, p))
    return found


def evaluate(tool_name, ti, registry, root):
    """Pure decision logic — returns (permissionDecision, reason|None). No I/O, so it is testable
    without a hook harness or a filesystem."""
    if tool_name not in EDIT_TOOLS:
        return "allow", None
    fp = ti.get("file_path") or ti.get("filePath") or ti.get("notebook_path")
    if not fp:
        return "allow", None
    rel = _rel(fp, root)

    # The registry protects itself. Changing a recorded invariant is a supersession — a decision the
    # user makes deliberately — never a silent edit by the agent that is about to be bound by it.
    if rel == REGISTRY_REL:
        return "ask", ("This edits the decision registry itself. Changing a recorded decision is a "
                       "supersession: add a NEW entry with `supersedes` set and flip the old one to "
                       "`status: superseded` — never rewrite or delete in place. Confirm this is an "
                       "approved change.")

    # Defensive on shape as well as on errors: `evaluate` is the pure entry point and is called
    # directly (by `check`, by tests, by anything embedding this), so it cannot rely on main()'s
    # catch-all to keep a hand-edited registry from raising.
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        return "allow", None
    active = [d for d in decisions
              if isinstance(d, dict) and d.get("status", "active") == "active"
              and matches(rel, d.get("protected_paths"))]
    if not active:
        return "allow", None

    changed = _changed_text(tool_name, ti)
    deny, ask = [], []
    for d in active:
        ident = f"{d.get('id', '?')} {d.get('title', '')}".strip()
        if d.get("caution_on_touch"):
            # Guards a PATH with no params to check. Without this a path-only decision falls through
            # to allow SILENTLY, which makes it impossible to guard code that is dangerous to touch
            # at all (anything that spends money, deploys, or migrates).
            ask.append(f"{ident}: {d.get('statement', '')}".strip())
        for name, actual, p in violations(changed, d.get("protected_params")):
            msg = (f"{ident}: {name}={actual!r} violates {p['op']} {p.get('value')!r}. "
                   f"{d.get('statement', '')}").strip()
            why = d.get("rationale")
            if why:
                msg += f" (Why: {why})"
            (deny if d.get("class") == "invariant" else ask).append(msg)

    if deny:
        return "deny", (" | ".join(deny) + " — This is a recorded INVARIANT. If it genuinely needs "
                        f"to change, supersede it in {REGISTRY_REL} first; that is a decision, not "
                        "an edit.")
    if ask:
        return "ask", " | ".join(ask)
    return "allow", None


# --- registry audit: the backstop for whatever the hook let through -----------------------------

def check(root):
    """Scan files already on disk for violations of the active registry. Answers the first question
    anyone asks after authoring one — 'does my code actually satisfy this?' — which the hook cannot,
    since it only ever sees edits going forward. Returns [(rel_path, decision_id, name, value)]."""
    root = Path(root)
    registry = load_registry(root)
    out = []
    for d in registry.get("decisions", []) or []:
        if not isinstance(d, dict) or d.get("status", "active") != "active" \
                or not d.get("protected_params"):
            continue
        # pathlib.glob DOES understand `**` as zero-or-more, so patterns behave here as written.
        for pat in d.get("protected_paths") or []:
            for path in sorted(root.glob(pat)):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for name, actual, p in violations(text, d["protected_params"]):
                    out.append((_rel(str(path), root), d.get("id", "?"), name, actual))
    return out


def validate(registry):
    """Structural problems that would make a decision silently unenforceable — the failure mode of a
    registry is being quietly wrong, not loudly broken."""
    problems = []
    seen = set()
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        return ["`decisions` must be a list"]
    for i, d in enumerate(decisions):
        ident = d.get("id") or f"#{i}"
        if not d.get("id"):
            problems.append(f"{ident}: missing `id`")
        elif d["id"] in seen:
            problems.append(f"{ident}: duplicate `id`")
        seen.add(d.get("id"))
        if d.get("class") not in ("invariant", "recipe"):
            problems.append(f"{ident}: `class` must be 'invariant' or 'recipe'")
        if not d.get("protected_paths"):
            problems.append(f"{ident}: no `protected_paths` — it can never match an edit")
        if not d.get("protected_params") and not d.get("caution_on_touch"):
            problems.append(f"{ident}: no `protected_params` and no `caution_on_touch` — "
                            f"it guards a path but checks nothing, so it will never fire")
        for p in d.get("protected_params") or []:
            if p.get("op") not in OPS:
                problems.append(f"{ident}: param {p.get('name')!r} has op {p.get('op')!r}, "
                                f"expected one of {', '.join(OPS)}")
    return problems


def _emit(decision, reason):
    if decision == "allow":
        sys.exit(0)
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}}
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out))
    sys.exit(0)


def main(argv):
    if len(argv) >= 2 and argv[1] in ("check", "validate"):
        root = argv[2] if len(argv) > 2 else "."
        if not (Path(root) / REGISTRY_REL).exists():
            print(f"no registry at {REGISTRY_REL} — the gate is off (see /sdlc-decide)")
            return 0
        if argv[1] == "validate":
            problems = validate(load_registry(root))
            for p in problems:
                print(f"  [PROBLEM] {p}")
            print(f"decision registry: {'valid' if not problems else str(len(problems)) + ' problem(s)'}")
            return 1 if problems else 0
        found = check(root)
        for rel, ident, name, actual in found:
            print(f"  [VIOLATION] {rel}: {name}={actual!r} breaks {ident}")
        print(f"decision registry: {len(found)} violation(s) in code already on disk"
              + ("." if not found else " — the hook only guards NEW edits; these predate it."))
        return 1 if found else 0

    # hook mode: a tool call on stdin
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        root = project_dir()
        if root is None or not enabled(root):
            _emit("allow", None)
            return 0
        decision, reason = evaluate(data.get("tool_name", ""), data.get("tool_input") or {},
                                    load_registry(root), root)
        _emit(decision, reason)
    except Exception:
        sys.exit(0)      # fail open: never block real work because the gate itself errored
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
