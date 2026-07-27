"""Read-only backlog status: counts by goal status + run cursor + queue state. Zero-dep.
(A 3-line frontmatter read is duplicated from sdlc-loop's frontmatter.py on purpose — the two
skills are independently installable units; sharing a lib across them would over-couple them.)"""
import sys, pathlib, re

_FENCE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _status_of(text):
    m = _FENCE.match(text)
    if not m:
        return None
    s = re.search(r"^status:\s*(\S+)", m.group(1), re.MULTILINE)
    return s.group(1).strip('"') if s else None        # parity with frontmatter.parse (strips quotes)


def summary(sdlc_dir):
    base = pathlib.Path(sdlc_dir)
    counts = {"pending": 0, "in_progress": 0, "done": 0, "parked": 0, "failed": 0, "proposed": 0}
    for p in sorted((base / "goals").glob("*.md")):
        s = _status_of(p.read_text())
        if s in counts:
            counts[s] += 1
    cur = base / "state" / "STATE.md"
    it = 0
    if cur.exists():
        m = re.search(r"^iteration:\s*(\d+)", cur.read_text(), re.MULTILINE)
        it = int(m.group(1)) if m else 0
    q = base / "state" / "review-queue.md"
    queue_nonempty = bool(q.exists() and re.search(r"^## ", q.read_text(), re.MULTILINE))
    return {**counts, "iteration": it, "queue_nonempty": queue_nonempty,
            "ledger_entries": _ledger_entries(base),
            "goals_since_align": _goals_since_align(base, counts["done"], it)}


#: Goals shipped before a cumulative-drift audit is worth running. Drift needs a trajectory to be
#: visible at all, and re-reading the same short window just produces noise.
#: ponytail: a flat count, not a cadence — bump it if /sdlc-align keeps coming back clean.
ALIGN_EVERY = 5


def _goals_since_align(base, done, iteration):
    """Work not yet covered by an alignment audit — None when there's no north-star to drift from.
    The last report IS the state (no extra bookkeeping file): it records the count it reviewed.

    This exists because the trigger would otherwise be circular — "run the drift audit once drift
    becomes a problem" can't fire, since undetected drift is exactly what you can't observe without
    the audit. A count is something the loop can actually see.

    Counts the LARGER of two signals, because neither covers both backlog modes on its own: the local
    `done` tally is blind in github mode (goals are issues there, and .sdlc/goals/ stays empty), while
    the loop's `iteration` cursor is blind to interactive `/sdlc-goal` runs (only /sdlc-loop advances
    it). Whichever is higher is the honest floor for "how much has happened".
    ponytail: a github-mode backlog driven ONLY interactively advances neither, so it under-counts and
    the offer stays silent — `/sdlc-align` still runs fine on demand. Counting closed `sdlc:goal`
    issues would close that hole, at the cost of making this dashboard shell out to `gh`."""
    if not (base / "context" / "north-star.md").exists():
        return None
    reports = sorted((base / "knowledge" / "align").glob("*.md")) if (base / "knowledge" / "align").is_dir() else []
    reviewed = 0
    if reports:
        try:                                   # ISO-dated names sort newest-last; unreadable = never ran
            m = re.search(r"^goals_reviewed:\s*(\d+)", reports[-1].read_text(), re.MULTILINE)
            reviewed = int(m.group(1)) if m else 0
        except OSError:
            pass
    return max(0, max(done, iteration) - reviewed)


def _ledger_entries(base):
    """Ledger lines across every author's file. Zero when the ledger is off or absent, which
    keeps the status line byte-identical for a repo that has not opted in."""
    total = 0
    entries = base / "ledger" / "entries"
    if not entries.exists():
        return 0
    for path in sorted(entries.glob("*.jsonl")):
        try:
            total += sum(1 for line in path.read_text().splitlines() if line.strip())
        except OSError:
            continue
    return total


def main(argv):
    s = summary(argv[1] if len(argv) > 1 else ".sdlc")
    line = (f"backlog: {s['proposed']} proposed, {s['pending']} pending, {s['in_progress']} in-progress, "
            f"{s['done']} done, {s['parked']} parked, {s['failed']} failed | "
            f"iteration {s['iteration']} | "
            f"review-queue: {'NEEDS ATTENTION' if s['queue_nonempty'] else 'empty'}")
    if s["ledger_entries"]:                    # silent when the ledger is off: same line as before
        line += f" | ledger: {s['ledger_entries']} entries"
    if (s["goals_since_align"] or 0) >= ALIGN_EVERY:   # silent without a north-star, or when not due
        line += f" | alignment check due ({s['goals_since_align']} goals since the last): /sdlc-align"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
