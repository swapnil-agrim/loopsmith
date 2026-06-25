"""Run state: config, STATE.md counters, goal status transitions, review-queue append. Zero-dep."""
import json, pathlib, importlib.util, re

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("frontmatter", _HERE / "frontmatter.py")
frontmatter = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(frontmatter)


def load_config(sdlc_dir):
    return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())


def _state_file(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "STATE.md"


def _read_int(text, key):
    m = re.search(rf"^{key}:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else 0


def load_cursor(sdlc_dir):
    text = _state_file(sdlc_dir).read_text()
    return {"iteration": _read_int(text, "iteration"), "run_iteration": _read_int(text, "run_iteration")}


def _set_line(text, key, value):
    line_re = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    new = f"{key}: {value}"
    return line_re.sub(new, text) if line_re.search(text) else text.rstrip() + f"\n{new}\n"


def save_cursor(sdlc_dir, iteration, run_iteration, summary):
    f = _state_file(sdlc_dir)
    text = f.read_text()                                  # structure-preserving: patch lines, keep the rest
    text = _set_line(text, "iteration", iteration)
    text = _set_line(text, "run_iteration", run_iteration)
    text = _set_line(text, "last_run", summary)
    f.write_text(text)


def start_run(sdlc_dir):
    """Reset the per-run budget counter at the start of a /sdlc-loop invocation."""
    f = _state_file(sdlc_dir)
    f.write_text(_set_line(f.read_text(), "run_iteration", 0))


def _set_status(goal_path, status):
    p = pathlib.Path(goal_path)
    p.write_text(frontmatter.set_field(p.read_text(), "status", status))


def set_in_progress(sdlc_dir, goal_path):
    _set_status(goal_path, "in_progress")


def complete(sdlc_dir, goal_path):
    _set_status(goal_path, "done")


def park(sdlc_dir, goal_path, reason):
    _set_status(goal_path, "parked")
    q = pathlib.Path(sdlc_dir) / "state" / "review-queue.md"
    name = pathlib.Path(goal_path).name
    with q.open("a") as f:        # append-mode: race-safe (single-worker), header written once by scaffolder
        f.write(f"\n## {name}\n- reason: {reason}\n- needs: human review\n")
