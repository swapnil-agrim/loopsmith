"""Park-&-continue loop driver. run_loop ties discovery + run_goal + state; start/next/record are
the agent's CLI hooks into the same primitives. Budget v1 = per-run max_iterations (run_iteration,
reset each invocation). max_tokens/max_minutes deferred: no host spend signal / wall-clock yet.
The irreversible-action gate is enforced by the /sdlc-loop SKILL.md prose, not here."""
import sys, pathlib, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


discovery = _load("discovery")
state = _load("state")


def _goals_dir(sdlc_dir):
    return str(pathlib.Path(sdlc_dir) / "goals")


def _next(sdlc_dir):
    """(kind, goal): 'goal' (+marks in_progress, the commit point), 'DONE' (drained), 'BUDGET'.
    Drained backlog reports DONE even if budget is also spent (empty wins the tie)."""
    goal = discovery.next_pending(_goals_dir(sdlc_dir))
    if goal is None:
        return ("DONE", None)
    config = state.load_config(sdlc_dir)
    run_iteration = state.load_cursor(sdlc_dir)["run_iteration"]
    if run_iteration >= config.get("budget", {}).get("max_iterations", 20):
        return ("BUDGET", None)
    state.set_in_progress(sdlc_dir, goal)
    return ("goal", goal)


def _record(sdlc_dir, goal, result, detail=""):
    if result == "done":
        state.complete(sdlc_dir, goal)
    else:                                        # parked or failed
        state.park(sdlc_dir, goal, detail or result)
    cur = state.load_cursor(sdlc_dir)
    state.save_cursor(sdlc_dir, cur["iteration"] + 1, cur["run_iteration"] + 1,
                      f"last: {pathlib.Path(goal).name} -> {result}")


def run_loop(sdlc_dir, run_goal):
    state.start_run(sdlc_dir)                     # reset per-run budget (resume-safe)
    done = parked = 0
    while True:
        kind, goal = _next(sdlc_dir)
        if kind == "DONE":
            stopped = "backlog-empty"; break
        if kind == "BUDGET":
            stopped = "budget"; break
        result, detail = run_goal(goal)
        _record(sdlc_dir, goal, result, detail)
        done += (result == "done")
        parked += (result != "done")
    return {"done": done, "parked": parked,
            "iterations": state.load_cursor(sdlc_dir)["iteration"], "stopped": stopped}


def main(argv):
    if len(argv) >= 3 and argv[1] == "start":
        state.start_run(argv[2]); return 0
    if len(argv) >= 3 and argv[1] == "next":
        kind, goal = _next(argv[2]); print(goal if kind == "goal" else kind); return 0
    if len(argv) >= 5 and argv[1] == "record":
        _record(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else ""); return 0
    print("usage: loop.py start <dir> | next <dir> | record <dir> <goal> done|parked [reason]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
