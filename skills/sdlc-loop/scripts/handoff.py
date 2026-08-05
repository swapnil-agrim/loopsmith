#!/usr/bin/env python3
"""Cross-area hand-off: park WITH a successor instead of parking into silence.

The loop already parks correctly when it hits something it must not decide alone. But a very common
blocker is not a decision at all — it is a dependency in code someone else owns. Parking that one
tells nobody: the queue entry is local and gitignored, the issue comment is unaddressed, and no code
path in the kit has ever set an assignee. The work stalls until a human happens to notice.

A hand-off closes that. It resolves the owner from the repo's own CODEOWNERS, opens an issue in their
area carrying the dependency, assigns it to them, records the fact in the team ledger addressed to
them, and links it from the blocked issue. Then the goal parks as before and the loop moves on.

The routing then happens by itself: the new issue carries the GOAL label and an assignee, so the
owner's own loop picks it up through the `discovery.github.assignee` filter. No new transport, no
daemon — the backlog everyone already shares does the delivery.

Every step degrades honestly. No owner in CODEOWNERS, no `gh`, or a local backlog: the ledger entry
is still written, so the team can still see what is blocked on whom. Zero deps.
"""
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
owners = _load("owners")
sources = _load("sources")

DEFAULT_PRIORITY = "P1"
DEPENDENCY_LABEL = "sdlc:dependency"


def _settings(config):
    return (ledger.settings(config).get("handoff") or {})


def dependency_label(config):
    return _settings(config).get("label") or DEPENDENCY_LABEL


def project_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).resolve().parent


def issue_body(goal, area, why, blocked_by_url=""):
    """The body a human (or their loop) reads cold. It has to say what is blocked, on what, and what
    'done' means — an unactionable hand-off is worse than none, because it looks handled."""
    lines = [
        f"Blocking another area's work. Raised automatically by the SDLC loop from goal `{goal}`.",
        "",
        f"**Area:** `{area}`",
        f"**What is needed:** {why}",
        "",
        "**Done when:** the dependency above exists and the blocked goal can proceed.",
        "",
        "Reply on this issue if this should be re-scoped, re-assigned, or declined — the blocked "
        "goal is parked until then.",
    ]
    if blocked_by_url:
        lines.insert(4, f"**Blocked goal:** {blocked_by_url}")
    return "\n".join(lines)


def hand_off(sdlc_dir, config, goal, area, why, priority=DEFAULT_PRIORITY,
             title=None, source=None, run=None):
    """Open the dependency, address it, record it. Returns a report dict; never raises.

    `issue` is None when the host could not create one (local backlog, no `gh`, or a failed call) —
    the ledger entry is written regardless, because a hand-off nobody can see is the bug this
    function exists to fix."""
    report = {"goal": str(goal), "area": area, "owner": None, "issue": None,
              "entry": None, "warnings": []}

    try:
        report["owner"] = owners.owner_of(project_root(sdlc_dir), area, config)
    except Exception as exc:                                   # noqa: BLE001 - roster is advisory
        report["warnings"].append(f"could not read CODEOWNERS: {exc}")
    if not report["owner"]:
        report["warnings"].append(
            f"no owner for area {area!r} — recording the hand-off unaddressed; "
            "add the area to CODEOWNERS or to config ledger.owners")

    if source is None:
        try:
            source = sources.get_source(sdlc_dir, config)
        except Exception as exc:                               # noqa: BLE001
            report["warnings"].append(f"no backlog source: {exc}")

    if source is not None and hasattr(source, "create_dependency"):
        heading = title or f"[{area}] dependency for {pathlib.Path(str(goal)).name}"
        try:
            blocked_url = source.issue_url(goal) if hasattr(source, "issue_url") else ""
            report["issue"] = source.create_dependency(
                heading, issue_body(goal, area, why, blocked_url), report["owner"],
                labels=[dependency_label(config), f"priority:{priority}"])
        except Exception as exc:                               # noqa: BLE001 - never block the park
            report["warnings"].append(f"could not open the dependency issue: {exc}")
    else:
        report["warnings"].append("backlog source cannot open issues — ledger entry only")

    report["entry"] = ledger.safe_append(
        sdlc_dir, "handoff", goal, config=config, to=report["owner"], area=area,
        issue=int(report["issue"]) if str(report["issue"] or "").isdigit() else None,
        priority=priority, why=why, state="open")

    if report["issue"] and source is not None:
        # two channels, two audiences (#376): a human-visible narrative comment, AND a
        # machine-readable body marker so a future precheck() run can auto-skip this goal without
        # a human re-stating what already happened. "Blocked by #N" is the exact phrase
        # backlog_check.py's _BLOCK_RE requires -- an earlier version of the narrative said
        # "Blocked on", which never matched at all; fixed here too so the human-visible text is
        # consistent with the machine-readable one, not just superficially similar.
        narrative = (f"Blocked by a `{area}` dependency — opened #{report['issue']}"
                    + (f" and assigned to @{report['owner']}" if report["owner"] else "")
                    + f" ({priority}). Parking this goal until it lands.")
        if hasattr(source, "note"):
            try:
                source.note(str(goal), narrative)
            except Exception as exc:                            # noqa: BLE001
                report["warnings"].append(f"could not comment on the blocked issue: {exc}")
        if hasattr(source, "append_to_body"):
            try:
                source.append_to_body(str(goal), f"**Blocked by:** #{report['issue']}")
            except Exception as exc:                            # noqa: BLE001
                report["warnings"].append(
                    f"could not record the machine-readable blocker on the blocked issue's body: {exc}")
    return report


def acknowledge(sdlc_dir, config, issue, state, why="", goal=None):
    """The other half. Reading a hand-off obliges an answer: taking it, needing time, declining it,
    or closing it out. `deferred` deliberately does NOT settle the hand-off — a promise to look later
    is not a resolution, and the team view keeps showing it."""
    return ledger.safe_append(sdlc_dir, "ack", goal or f"issue-{issue}", config=config,
                              issue=int(issue) if str(issue).isdigit() else None,
                              state=state, why=why)


def main(argv):
    if len(argv) >= 3 and argv[1] == "open":
        sdlc_dir, goal = argv[2], argv[3] if len(argv) > 3 else ""
        flags = ledger._flags(argv[4:])
        area, why = flags.get("area"), flags.get("why")
        if not goal or not area or not why:
            print("handoff.py open needs <goal> --area <area> --why <text>", file=sys.stderr)
            return 2
        config = ledger._config(sdlc_dir)
        report = hand_off(sdlc_dir, config, goal, area, why,
                          priority=flags.get("priority", DEFAULT_PRIORITY),
                          title=flags.get("title"))
        for warning in report["warnings"]:
            print(f"handoff: {warning}", file=sys.stderr)
        print(f"handed off to {report['owner'] or '(unowned)'}"
              + (f" as #{report['issue']}" if report["issue"] else " (no issue opened)")
              + (f"; ledger {report['entry']['id']}" if report["entry"] else "; ledger off"))
        return 0
    if len(argv) >= 3 and argv[1] == "ack":
        sdlc_dir = argv[2]
        flags = ledger._flags(argv[3:])
        issue, state = flags.get("issue"), flags.get("state")
        if not issue or state not in ledger.STATES:
            print(f"handoff.py ack needs --issue <n> --state {'|'.join(ledger.STATES)}",
                  file=sys.stderr)
            return 2
        entry = acknowledge(sdlc_dir, ledger._config(sdlc_dir), issue, state, flags.get("why", ""))
        print(entry["id"] if entry else "OFF (config: \"ledger\": {\"enabled\": true})")
        return 0
    print("usage: handoff.py open <dir> <goal> --area A --why TEXT [--priority P --title T] | "
          f"ack <dir> --issue N --state {'|'.join(ledger.STATES)} [--why TEXT]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
