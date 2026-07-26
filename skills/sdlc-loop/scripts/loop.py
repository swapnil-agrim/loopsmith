"""Park-&-continue loop driver. run_loop ties the backlog source + run_goal + state; start/next/record are
the agent's CLI hooks into the same primitives. Budgets (all per-run, reset each invocation):
max_iterations always enforces; max_minutes enforces by wall-clock from the run's start; max_tokens
enforces against the host-REPORTED spend counter (`loop.py spend <dir> <n>` — the loop never measures
spend itself; no reports == no enforcement). An absent/zero key enforces nothing, so a config without
it behaves exactly as before. The irreversible-action gate is enforced by /sdlc-loop SKILL.md prose.
Claim and outcome are mirrored to the team ledger (ledger.py) when `ledger.enabled` is on — every
such call is fail-open, so a ledger problem can never stop a run."""
import sys, pathlib, importlib.util, time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


state = _load("state")
sources = _load("sources")          # backlog source: local files or GitHub issues (config-selected)
ledger = _load("ledger")            # team record (config-gated, default OFF; every call is fail-open)
work = _load("work")                # per-goal worktree/branch/PR (config-gated, default OFF)


def _budget_spent(cursor, budget):
    """True when ANY configured ceiling is reached. Absent/zero keys never enforce —
    a config without them behaves exactly as before this check existed."""
    if cursor["run_iteration"] >= budget.get("max_iterations", 20):
        return True
    minutes = budget.get("max_minutes")
    if minutes and cursor["run_started_at"]:
        if (time.time() - cursor["run_started_at"]) / 60.0 >= minutes:
            return True
    tokens = budget.get("max_tokens")
    if tokens and cursor["run_tokens"] >= tokens:
        return True
    return False


def _next(sdlc_dir, source, config):
    """(kind, goal): 'goal' (+marks in_progress, the commit point), 'DONE' (drained), 'BUDGET'.
    Drained backlog reports DONE even if budget is also spent (empty wins the tie)."""
    goal = source.next_pending()
    if goal is None:
        return ("DONE", None)
    if _budget_spent(state.load_cursor(sdlc_dir), config.get("budget", {})):
        return ("BUDGET", None)
    source.mark_in_progress(goal)
    ledger.safe_append(sdlc_dir, "claimed", goal, config=config)
    return ("goal", goal)


def _surface_inbox(sdlc_dir):
    """Print anything a teammate needs from you BEFORE handing over the next goal.

    Between goals is the only boundary that works: nothing can inject a message into a running
    session, and interrupting a goal mid-flight is how half-finished work gets lost. Worst-case
    latency is therefore one goal, which is the right trade. stderr ONLY — stdout is the goal the
    caller parses. Fail-open: no watcher, no inbox, no problem."""
    try:
        watch = _load("watch")
        text = watch.read_inbox(sdlc_dir)
        if text:
            print("\n=== LEDGER INBOX — a teammate needs you ===\n" + text
                  + "\n=== end inbox ===\n", file=sys.stderr)
            watch.clear_inbox(sdlc_dir)
    except Exception:
        pass


def _record(sdlc_dir, source, goal, result, detail=""):
    if result == "done":
        source.complete(goal)
    elif result == "failed" and hasattr(source, "fail"):
        source.fail(goal, detail or result)      # hasattr: a source without fail() parks instead
    else:                                        # parked (or failed on a fail-less source)
        source.park(goal, detail or result)
    cur = state.load_cursor(sdlc_dir)
    state.save_cursor(sdlc_dir, cur["iteration"] + 1, cur["run_iteration"] + 1,
                      f"last: {pathlib.Path(goal).name} -> {result}")
    # The outcome, once, on the single chokepoint both the CLI and run_loop paths pass through.
    ledger.safe_append(sdlc_dir, result if result in ("done", "failed") else "parked", goal,
                       why=detail or None)


_evidence_path = state.evidence_path       # both live in state.py so work.py can require the same
_done_refusal = state.done_refusal         # evidence without loop.py and work.py importing each other


def verify_goal(sdlc_dir, goal):
    """Run the goal's proving command and persist MACHINE evidence (sdlc-verify's
    prose gate, made checkable). Command source: goal frontmatter `verify_command`
    (local mode), else config `verify.command`.
    Exit: 0 verified · 1 the command failed · 3 no command declared (honest absence)."""
    import json as _json, subprocess
    config = state.load_config(sdlc_dir)
    cmd = None
    goal_path = pathlib.Path(str(goal))
    if goal_path.suffix == ".md" and goal_path.exists():
        cmd = state.frontmatter.get(goal_path.read_text(), "verify_command")
    cmd = cmd or (config.get("verify") or {}).get("command") or None
    if not cmd:
        print("NO-COMMAND (set goal frontmatter `verify_command` or config `verify.command`)",
              file=sys.stderr)
        return 3
    # Proving commands run where THIS GOAL'S CODE IS — its worktree when `work` is on, else the
    # project root, exactly as before (same injectable-root rule as pipeline.py's `repo_root`).
    # Deriving it from sdlc_dir alone would test the main checkout while the change sits in the
    # worktree: a green that proves nothing, and one that `record done` would happily accept.
    root = work.root(sdlc_dir, goal)
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=root)
    ev = _evidence_path(sdlc_dir, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
    ev.write_text(_json.dumps({"command": cmd, "exit": proc.returncode,
                               "at": int(time.time()), "tail": tail}, indent=2))
    print(f"{'VERIFIED' if proc.returncode == 0 else 'FAILED'} exit={proc.returncode} evidence={ev}")
    return 0 if proc.returncode == 0 else 1


def _done_refusal(sdlc_dir, goal):
    """None when fresh passing evidence exists for this goal, else the reason to refuse.
    Fresh = produced at/after this run's start (a stale green from yesterday proves nothing)."""
    import json as _json
    ev = _evidence_path(sdlc_dir, goal)
    if not ev.exists():
        return "no verify evidence for this goal"
    try:
        data = _json.loads(ev.read_text())
    except Exception:
        return "verify evidence is unreadable"
    if data.get("exit") != 0:
        return f"last verify FAILED (exit {data.get('exit')})"
    if data.get("at", 0) < state.load_cursor(sdlc_dir)["run_started_at"]:
        return "verify evidence predates this run"
    return None


def run_loop(sdlc_dir, run_goal):
    state.start_run(sdlc_dir)                       # reset per-run budget (resume-safe)
    config = state.load_config(sdlc_dir)
    source = sources.get_source(sdlc_dir, config)   # one source per run (e.g. github labels ensured once)
    done = parked = failed = 0
    while True:
        kind, goal = _next(sdlc_dir, source, config)
        if kind == "DONE":
            stopped = "backlog-empty"; break
        if kind == "BUDGET":
            stopped = "budget"; break
        result, detail = run_goal(goal)
        _record(sdlc_dir, source, goal, result, detail)
        done += (result == "done")
        failed += (result == "failed")
        parked += (result not in ("done", "failed"))
    return {"done": done, "parked": parked, "failed": failed,
            "iterations": state.load_cursor(sdlc_dir)["iteration"], "stopped": stopped}


def main(argv):
    if len(argv) >= 3 and argv[1] == "start":
        state.start_run(argv[2]); return 0
    if len(argv) >= 3 and argv[1] == "next":
        config = state.load_config(argv[2])
        _surface_inbox(argv[2])                     # stderr; stdout stays exactly the goal/DONE/BUDGET
        kind, goal = _next(argv[2], sources.get_source(argv[2], config), config)
        print(goal if kind == "goal" else kind); return 0
    if len(argv) >= 4 and argv[1] == "qc":          # board-only: move a goal to QC at the Review phase
        config = state.load_config(argv[2])
        sources.get_source(argv[2], config).mark_qc(argv[3]); return 0
    if len(argv) >= 5 and argv[1] == "note":        # record a journey-log / critical-insight note (fail-open)
        config = state.load_config(argv[2])
        try:
            sources.get_source(argv[2], config).note(argv[3], argv[4])
        except Exception as e:
            print(f"loop.py note: recording failed (non-fatal): {e}", file=sys.stderr)
        return 0
    if len(argv) >= 5 and argv[1] == "record":
        config = state.load_config(argv[2])
        # Machine done_when (opt-in): with verify.enforce on, a `done` needs fresh
        # passing evidence from `loop.py verify` — the sdlc-verify prose gate, enforced.
        if argv[4] == "done" and (config.get("verify") or {}).get("enforce") is True:
            refusal = _done_refusal(argv[2], argv[3])
            if refusal:
                print(f"REFUSED: {refusal} — run `loop.py verify {argv[2]} <goal>` first "
                      "(config verify.enforce is on)", file=sys.stderr)
                return 4
        _record(argv[2], sources.get_source(argv[2], config), argv[3], argv[4],
                argv[5] if len(argv) > 5 else ""); return 0
    if len(argv) >= 4 and argv[1] == "spend":       # host-reported token spend → budget.max_tokens
        state.add_tokens(argv[2], argv[3]); return 0
    if len(argv) >= 4 and argv[1] == "verify":      # machine done_when: run + persist evidence
        return verify_goal(argv[2], argv[3])
    print("usage: loop.py start <dir> | next <dir> | qc <dir> <goal> | "
          "note <dir> <goal> <text> | record <dir> <goal> done|parked|failed [reason] | "
          "spend <dir> <tokens> | verify <dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
