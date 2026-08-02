#!/usr/bin/env python3
"""Slice-level parallelism: run the independent parts of one goal at the same time, safely.

THE PROBLEM. A goal is planned as slices and the loop runs them strictly one after another, even when
three of them touch nothing in common. Sequential is not merely slow: a long goal spends ONE session's
context on work that had no reason to share a window, so the last slice starts on a flushed one.

WHY A DECLARED MANIFEST, NEVER INFERENCE. Concurrency is safe only where the blast radius is stated.
Two slices that each declare which files they touch can be PROVEN disjoint; a slice that declares
nothing cannot be, so this module treats it as conflicting with everything and it runs alone. Guessing
the radius would trade a slow run for two workers silently overwriting each other — a much worse bug
than waiting.

WHERE THE BOUNDARY IS. A python script cannot spawn a subagent — only the agent can. So this module
COMPUTES and ADVISES: it reads the manifest, works out which slices may run together, and renders a
dispatch plan. `/sdlc-loop`'s prose does the dispatching, one **subagent** per slice, with
`isolation: worktree` whenever the slice declares files so two of them cannot fight over one checkout.

AND WHERE IT STOPS BEING HONEST TO AUTOMATE. A slice too large for one subagent's context needs a real
session, and the truthful answer is to PRINT the `claude --worktree <name>` line for a human to start.
The kit never shells out to an unattended `claude -p`: that is uncapped spend, and it would put a
second worker on one `.sdlc`, which every state file in this kit assumes never happens.

Default OFF (`parallel.enabled`): with no `parallel` block, and with no manifest beside the plan, the
loop behaves exactly as it did before this module existed. Zero deps.
"""
import fnmatch
import importlib.util
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ledger = _load("ledger")            # team record (config-gated, default OFF; every call is fail-open)

#: Manifest suffix, written beside the goal's plan so the two are groomed together.
SUFFIX = ".slices.json"

DEFAULT_SIZE = "small"
DEFAULT_STATUS = "pending"
DONE = "done"
LARGE = "large"

#: The two dispatch modes. `subagent` is the automatable one (fresh isolated context, optional
#: worktree); `session` is the honest escape hatch a human starts — see the module docstring.
SUBAGENT = "subagent"
SESSION = "session"

#: Three concurrent slices is the default ceiling: enough to pay for the coordination, small enough
#: that a wave's diffs are still reviewable as one landing.
DEFAULT_MAX_CONCURRENT = 3


# --------------------------------------------------------------------------- config


def settings(config):
    return (config or {}).get("parallel") or {}


def parallel(config):
    """-> (enabled, max_concurrent). `is True` on purpose: a truthy string or a stray 1 must not
    silently start several workers against one checkout. A non-numeric cap falls back to the default
    rather than raising — a typo in config must not break a run that would otherwise be sequential."""
    block = settings(config)
    try:
        cap = int(block.get("max_concurrent", DEFAULT_MAX_CONCURRENT))
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_CONCURRENT
    return (block.get("enabled") is True, max(1, cap))


def _config(sdlc_dir):
    """Tolerant read: `plan` is advisory output and has to work in a directory that has no config yet
    (or a half-written one), so an unreadable file reads as "no opinion", not as a failure."""
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- load


def goal_stem(goal):
    """The goal's identity as a filename part. Same rule as loop.py's evidence path: a `.md` goal is a
    PATH (take its stem), a github goal is an issue NUMBER (take it as-is)."""
    text = str(goal)
    return pathlib.Path(text).stem if text.endswith(".md") else text


def manifest_path(sdlc_dir, goal):
    return pathlib.Path(sdlc_dir) / "plans" / f"{goal_stem(goal)}{SUFFIX}"


def _as_list(value):
    """A single string where a list belongs is the commonest hand-edit slip; accept it rather than
    reporting a type error the author cannot act on. Blanks are dropped — an empty files entry would
    otherwise read as a declared radius that matches nothing."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def _normalise(item, index, path):
    if not isinstance(item, dict):
        raise ValueError(f"{path}: slice #{index} is a {type(item).__name__}, expected an object")
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "needs": _as_list(item.get("needs")),
        "files": _as_list(item.get("files")),
        "size": str(item.get("size") or DEFAULT_SIZE).strip() or DEFAULT_SIZE,
        "status": str(item.get("status") or DEFAULT_STATUS).strip() or DEFAULT_STATUS,
    }


def load(sdlc_dir, goal):
    """The manifest as a normalised list, `[]` when there is none (the goal simply runs whole).

    A broken manifest RAISES and names the file: a slice plan that silently reads as empty would run
    the whole goal in one session while its author believed it was parallelised."""
    path = manifest_path(sdlc_dir, goal)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of slices, got {type(raw).__name__}")
    return [_normalise(item, i, path) for i, item in enumerate(raw)]


# --------------------------------------------------------------------------- validate


def _cycles(slices):
    """Every dependency cycle, each as its ordered members.

    Reported by MEMBERS rather than as "a cycle exists" because the fix is to break one edge, and you
    cannot choose which edge without the names. A cycle is recorded once however many entry points
    reach it."""
    known = {s["id"] for s in slices}
    graph = {}
    for s in slices:
        graph.setdefault(s["id"], [])
        graph[s["id"]] += [n for n in s["needs"] if n in known]
    colour, found, seen = {}, [], set()

    def walk(node, path):
        colour[node] = 1                                    # 1 = on the current stack
        path.append(node)
        for nxt in graph.get(node, []):
            if colour.get(nxt) == 1:                        # back-edge: the cycle is the tail of path
                cycle = path[path.index(nxt):]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    found.append(cycle)
            elif colour.get(nxt, 0) == 0:
                walk(nxt, path)
        path.pop()
        colour[node] = 2                                    # 2 = fully explored

    for s in slices:
        if colour.get(s["id"], 0) == 0:
            walk(s["id"], [])
    return found


def validate(slices):
    """-> human-readable problems, in manifest order; empty means the manifest is safe to schedule.

    Everything here makes the schedule WRONG rather than merely ugly: a missing or duplicated id makes
    a dependency ambiguous, an unknown `needs` can never become done (that slice would never run), and
    a cycle means no frontier ever opens."""
    problems = []
    ids = [s["id"] for s in slices]
    for index, s in enumerate(slices):
        if not s["id"]:
            problems.append(f'slice #{index} has no "id"')
    reported = set()
    for sid in ids:
        if sid and ids.count(sid) > 1 and sid not in reported:
            reported.add(sid)
            problems.append(f'duplicate slice id "{sid}"')
    known = {sid for sid in ids if sid}
    for s in slices:
        for need in s["needs"]:
            if need not in known:
                problems.append(f'slice "{s["id"]}" needs unknown slice "{need}"')
    for cycle in _cycles(slices):
        problems.append("dependency cycle: " + " -> ".join(cycle + [cycle[0]]))
    return problems


# --------------------------------------------------------------------------- graph


def frontier(slices):
    """The runnable set right now: not done, and every declared dependency already done. A `needs`
    that names nothing real is never satisfied, which keeps that slice out of the frontier instead of
    letting it run early — `validate` is where the author is told why."""
    done = {s["id"] for s in slices if s["status"] == DONE}
    return [s for s in slices if s["status"] != DONE and all(n in done for n in s["needs"])]


def fan_out(slices):
    """-> {id: how many slices transitively wait on it}.

    TRANSITIVE, not direct: the point of the ordering is to keep the critical path moving, and a slice
    with one dependent that itself unblocks five matters far more than a leaf with one dependent.
    Cycle-safe (a visited set), so a malformed manifest still produces a number instead of hanging."""
    dependents = {s["id"]: set() for s in slices}
    for s in slices:
        for need in s["needs"]:
            if need in dependents:
                dependents[need].add(s["id"])
    out = {}
    for sid in dependents:
        seen, stack = set(), list(dependents[sid])
        while stack:
            node = stack.pop()
            if node in seen or node == sid:
                continue
            seen.add(node)
            stack.extend(dependents.get(node, ()))
        out[sid] = len(seen)
    return out


# --------------------------------------------------------------------------- conflicts


def _literal_prefix(pattern):
    """The leading path segments before the first wildcard — `engine/**/loader.py` -> `engine/`.

    fnmatch alone cannot see that `engine/**` and `engine/graph.py` reach the same file from opposite
    directions, and a false "disjoint" here is two workers editing one file."""
    head = []
    for part in str(pattern).strip().rstrip("/").split("/"):
        if any(ch in part for ch in "*?["):
            break
        head.append(part)
    return "/".join(head) + "/"


def _overlap(a, b):
    return (fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)
            or _literal_prefix(a).startswith(_literal_prefix(b))
            or _literal_prefix(b).startswith(_literal_prefix(a)))


def conflicts(a, b):
    """True when two slices could touch the same file, so they must not run in one wave.

    A slice with NO declared files conflicts with EVERYTHING. That is the whole safety property: an
    unknown blast radius is not something you may parallelise, and the cost of being wrong (a lost
    edit) is far higher than the cost of one extra wave."""
    files_a, files_b = a.get("files") or [], b.get("files") or []
    if not files_a or not files_b:
        return True
    return any(_overlap(pa, pb) for pa in files_a for pb in files_b)


# --------------------------------------------------------------------------- schedule


def schedule(slices, max_concurrent=DEFAULT_MAX_CONCURRENT):
    """-> ordered waves; each wave is a mutually non-conflicting slice of the frontier, capped.

    Within a wave, widest fan-out first (ties broken by manifest order) so a wave is never spent on
    leaves while the critical path waits. Fully deterministic — the same manifest always yields the
    same waves, which is what makes the plan reviewable before anything is dispatched.

    A frontier that never opens (a cycle, or a dependency on something absent) STOPS rather than
    looping: the remaining slices are simply not in the plan, and `validate` names the reason."""
    cap = max(1, int(max_concurrent or 1))
    order = {s["id"]: i for i, s in enumerate(slices)}
    fan = fan_out(slices)
    done = {s["id"] for s in slices if s["status"] == DONE}
    remaining = [s for s in slices if s["status"] != DONE]
    waves = []
    while remaining:
        ready = [s for s in remaining if all(n in done for n in s["needs"])]
        if not ready:
            break
        ready.sort(key=lambda s: (-fan.get(s["id"], 0), order.get(s["id"], 0)))
        wave = []
        for candidate in ready:
            if len(wave) >= cap:
                break
            if any(conflicts(candidate, picked) for picked in wave):
                continue                                    # not now — it leads the next wave instead
            wave.append(candidate)
        waves.append(wave)
        taken = {s["id"] for s in wave}
        done |= taken
        remaining = [s for s in remaining if s["id"] not in taken]
    return waves


# --------------------------------------------------------------------------- dispatch


def dispatch(slice_obj):
    """How this slice should be run. `large` means "will not fit one subagent's context", and the only
    honest answer to that is a real session a human starts — see the module docstring."""
    return SESSION if (slice_obj.get("size") or DEFAULT_SIZE) == LARGE else SUBAGENT


def needs_worktree(slice_obj):
    """A slice that declares files gets its own checkout, so a concurrent sibling cannot see (or
    stomp) its half-finished edits. A slice that declares none runs alone anyway, so a worktree would
    buy nothing and only cost a checkout."""
    return bool(slice_obj.get("files"))


def worktree_name(goal, slice_obj):
    return f"{goal_stem(goal)}-{slice_obj.get('id', '')}"


def session_command(goal, slice_obj):
    """The exact line a human runs. Printed, never executed: an unattended `claude -p` is uncapped
    spend and would put a second worker on one `.sdlc`."""
    return f"claude --worktree {worktree_name(goal, slice_obj)}"


# --------------------------------------------------------------------------- render


def render(plan, goal):
    """The dispatch plan a human reads before approving it, and the agent reads before acting on it.
    Every line names the one decision it carries: what runs, how, isolated or not."""
    total = sum(len(wave) for wave in plan)
    widest = max((len(wave) for wave in plan), default=0)
    lines = [f"# Slice plan — {goal_stem(goal)}", ""]
    if not plan:
        lines += ["No runnable slices: every slice is done, or nothing is unblocked "
                  "(run `slices.py check` for why).", ""]
        return "\n".join(lines)
    lines += [f"{total} slice(s) in {len(plan)} wave(s); the widest wave runs {widest} concurrently.",
              "Dispatch one wave at a time, land it, then compute the next.", ""]
    for number, wave in enumerate(plan, start=1):
        lines.append(f"## Wave {number} — {len(wave)} slice(s)")
        for s in wave:
            mode = dispatch(s)
            isolation = ("isolation: worktree" if needs_worktree(s)
                         else "no worktree (declares no files — runs alone)")
            lines.append(f"  - {s['id']} · {s['title'] or '(untitled)'}")
            lines.append(f"      dispatch: {mode}   ·   {isolation}")
            if mode == SESSION:
                lines.append("      too large for one subagent's context — start it yourself:")
                lines.append(f"      {session_command(goal, s)}")
        lines.append("")
    lines += ["Run a wave's `subagent` slices concurrently; never dispatch one with an unattended",
              "`claude -p` — uncapped spend, and a second worker on one .sdlc breaks its state.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def _flags(argv):
    """Deliberately a copy of ledger.py's parser rather than an import: this module stays usable and
    testable without pulling the ledger (and its git/gh surface) in behind it."""
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            name = token[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]
                i += 2
                continue
            out[name] = "true"
        i += 1
    return out


def _load_or_report(sdlc_dir, goal):
    """-> (slices, error). A malformed manifest is reported at the CLI edge, never as a traceback."""
    try:
        return load(sdlc_dir, goal), None
    except ValueError as exc:
        return None, str(exc)


def main(argv):
    if len(argv) >= 4 and argv[1] in ("plan", "frontier", "check"):
        verb, sdlc_dir, goal = argv[1], argv[2], argv[3]
        slices, error = _load_or_report(sdlc_dir, goal)
        if error:
            print(f"slices: {error}", file=sys.stderr)
            return 1
        problems = validate(slices)

        if verb == "check":
            for problem in problems:
                print(f"slices: {problem}", file=sys.stderr)
            if problems:
                return 1
            print(f"ok — {len(slices)} slice(s), no problems")
            return 0

        if verb == "frontier":
            for s in frontier(slices):
                print(s["id"])
            return 0

        if problems:
            print(f"slices: {manifest_path(sdlc_dir, goal)} cannot be scheduled:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        if not slices:
            print(f"no slice manifest at {manifest_path(sdlc_dir, goal)} — "
                  "the goal runs as one unit, exactly as before")
            return 0
        config = _config(sdlc_dir)
        enabled, cap = parallel(config)
        flags = _flags(argv[4:])
        if flags.get("max", "").isdigit():
            cap = max(1, int(flags["max"]))
        if not enabled:
            print('slices: parallelism is off (config: "parallel": {"enabled": true}) — '
                  "showing the plan anyway; run the waves in order until you turn it on",
                  file=sys.stderr)
        plan = schedule(slices, cap)
        # Site g (#139): one `slice` event per planned slice, across every wave. Each field
        # computation (`s["id"]`, `dispatch(s)`, `len(s["files"])`) runs in THIS frame, not inside
        # `safe_append`'s own try/except — Python evaluates call arguments before the call happens
        # — so each slice gets its own guard: one malformed manifest entry must never break the
        # rest of the plan, matching `ledger.read_all`'s own "one bad line is skipped" convention.
        for wave_index, wave in enumerate(plan, start=1):
            for s in wave:
                try:
                    ledger.safe_append(sdlc_dir, "slice", goal, config=config, stream=ledger.EVENTS,
                                       slice=s["id"], wave=wave_index, mode=dispatch(s),
                                       files_declared=len(s["files"]))
                except Exception:    # noqa: BLE001 - fail-open; telemetry must never break planning
                    continue
        print(render(plan, goal), end="")
        return 0

    print("usage: slices.py plan <sdlc-dir> <goal> [--max N] | frontier <sdlc-dir> <goal> | "
          "check <sdlc-dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
