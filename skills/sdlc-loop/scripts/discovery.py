"""Backlog discovery. Pluggable by source; only 'local-goals' implemented (GitHub adapter = v1.1)."""
import pathlib, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("frontmatter", _HERE / "frontmatter.py")
frontmatter = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(frontmatter)

_TERMINAL = {"done", "parked"}


def next_pending(goals_dir):
    """First *.md goal (filename order) whose status is not done/parked. None if none.
    Files without frontmatter (e.g. README.md) are not goals."""
    for path in sorted(pathlib.Path(goals_dir).glob("*.md")):
        status = frontmatter.get(path.read_text(), "status")
        if status is not None and status not in _TERMINAL:
            return str(path)
    return None
