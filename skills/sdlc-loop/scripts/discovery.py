"""Local-goals backlog discovery (the file source). Source selection lives in sources.py, which also
implements the GitHub-issues source; this module is the zero-dep local-files adapter.

Also resolves a goal's LANE — the ceremony tier the Research phase measured it into. Kept here with
the other goal-metadata reads (status) rather than in frontmatter.py, which is a parser with no goal
semantics."""
import sys, pathlib, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("frontmatter", _HERE / "frontmatter.py")
frontmatter = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(frontmatter)

_TERMINAL = {"done", "parked", "failed"}
# proposed = detector-suggested, awaiting HUMAN promotion (edit status -> pending).
# Not terminal, but never auto-picked: proposing work is safe, running it is gated.
_SKIP = _TERMINAL | {"proposed"}


def next_pending(goals_dir, skip=()):
    """First *.md goal (filename order) whose status is not done/parked/failed/proposed. None if none.
    Files without frontmatter (e.g. README.md) are not goals. `skip` holds goals a claim lease has
    assigned to another loop this pass — they are passed over so two loops don't start the same one."""
    skip = {str(s) for s in skip}
    for path in sorted(pathlib.Path(goals_dir).glob("*.md")):
        if str(path) in skip:
            continue
        status = frontmatter.get(path.read_text(), "status")
        if status and status not in _SKIP:
            return str(path)
    return None


LANES = ("small", "medium", "large")
#: Unsized goals get the FULL pass. `lane: auto` means Research hasn't measured it yet (or was
#: skipped), and guessing "small" on an unknown goal would skip ceremony the goal might need —
#: the one direction where being wrong is expensive. Unknown fails toward more rigour, not less.
DEFAULT_LANE = "medium"


def lane_of(goal):
    """The ceremony tier for a goal: 'small' | 'medium' | 'large'.

    `goal` is a path to a local goal file, or raw goal text. Anything unrecognised — absent, `auto`,
    a typo, an unreadable file — resolves to DEFAULT_LANE.

    LOCAL MODE ONLY, by design: this module is the local-files adapter, and a GitHub issue number
    carries no lane (Research records it in the issue timeline, which sources.py owns). Passing an
    issue number here returns DEFAULT_LANE — safe, but it is not a lane lookup, so github mode reads
    the lane from the phase note instead. Both modes apply the same unknown-goal default.
    """
    text = goal
    try:
        p = pathlib.Path(goal)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_LANE
    value = (frontmatter.get(text, "lane") or "").strip().lower()
    return value if value in LANES else DEFAULT_LANE


def main(argv):
    if len(argv) >= 3 and argv[1] == "lane":
        print(lane_of(argv[2]))
        return 0
    print("usage: discovery.py lane <goal-path-or-text>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
