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

GENERALIZED (issue #462): a formal cross-area hand-off was never the only issue LoopSmith itself
opens — a same-area follow-up finding (a review comment, a mid-goal discovery) is just as real, and
until now had no disciplined path at all: the agent filed it by hand via a bare `gh issue create`,
with no label and no assignee, easy to lose in a long session. `create_tracked_issue()` below is the
one real place the kit ever opens an issue on its own behalf; `hand_off()` is now a thin, behavior-
preserving wrapper around it — a formal hand-off is exactly its
`same_area=False, immediately_actionable=True, blocks_goal=True` special case. The `track` CLI verb
is the same-area/non-blocking sibling of `open`, for a finding that doesn't need a human decision.
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


def _tracked_issue_body(goal, area, why, blocks_goal):
    """Default body for a tracked issue when the caller doesn't supply one — the `track` CLI verb has
    no `--body` flag, so this is what a same-area follow-up or a queued finding gets. `hand_off()`
    always supplies its own body via `issue_body()` instead (unchanged): that template's wording
    ("blocking another area's work") is specific to a blocking cross-area dependency and would be
    misleading here."""
    lines = [
        f"Raised automatically by the SDLC loop from goal `{goal}`.",
        "",
        f"**Area:** `{area}`",
        f"**What is needed:** {why}",
    ]
    if blocks_goal:
        lines += [
            "",
            "**Done when:** the work above exists and the blocked goal can proceed.",
            "",
            "Reply on this issue if this should be re-scoped, re-assigned, or declined — the "
            "blocked goal is parked until then.",
        ]
    return "\n".join(lines)


def create_tracked_issue(sdlc_dir, config, goal, area, why, *,
                          same_area, immediately_actionable, blocks_goal,
                          priority=DEFAULT_PRIORITY, title=None, body=None,
                          extra_labels=(), source=None, run=None):
    """Open a tracked issue, address it, record it. The one real place the kit ever opens an issue on
    its own behalf — `hand_off()` below is a thin wrapper around this. Generalizes what used to be
    hand-off-only discipline (owner resolution, labels, the `gh` call, the ledger write, the dual
    comment+body-marker channel) to EVERY issue LoopSmith itself creates, closing the "orphan issue"
    bug: a same-area follow-up finding had no disciplined path at all before this and got filed by
    hand, unlabeled and unassigned (#462).

    `goal` keeps `hand_off()`'s existing meaning: the currently-in-progress goal this issue is being
    filed FROM, never the new issue itself.

    Three REQUIRED, keyword-only booleans — Python raises `TypeError` if a caller omits one. That is
    the actual mechanism behind "explicit, unavoidable, never a silent default": a default here is
    exactly how the orphan-issue bug, and a false-blocking bug, would each quietly reappear.

      same_area — who gets assigned.
        True  → `ledger.actor(config, run)` — I'm filing a follow-up in the area I'm already
                working; assign it to me.
        False → `owners.owner_of(project_root(sdlc_dir), area, config)` — a cross-area hand-off,
                CODEOWNERS-resolved, `None` with no matching entry (a caller must handle that
                gracefully — this function already does).

      immediately_actionable — the `sdlc:goal` gate.
        True  → the new issue carries the goal label, so `next_pending()`'s own `--label` filter
                auto-picks it for whoever it's assigned to.
        False → queued: filed, but not auto-picked until a human promotes it.

      blocks_goal — gates exactly two things, nothing else.
        True  → write the `**Blocked by:** #N` marker onto the CURRENT goal's own issue body (via
                `source.append_to_body`), so `backlog_check._explicit_blockers()` auto-skips that
                goal until the new issue closes — a genuine blocking dependency. `hand_off()` always
                pins this True: a hand-off is BY DEFINITION a blocking dependency.
        False → no marker, and the current goal is never treated as blocked. A non-blocking,
                merely-related finding must never auto-park unrelated work — that is the real
                correctness bug this axis exists to prevent; a design that conflated "blocking
                dependency" with "follow-up finding" would ship it.

    Labels always applied: `priority:<priority>`, `area:<area>`, any `extra_labels`, plus the goal
    label iff `immediately_actionable` (`GitHubSource.create_dependency`'s own `goal_label=`).

    Ledger shape reuses two existing kinds, adds no new one — gated on `blocks_goal` too, not just
    `same_area` (PR #466 review finding): `backlog_check._ledger_signals()` is a SECOND, independent
    blocking mechanism from the body-marker/`_explicit_blockers()` channel above — it treats any
    `ledger.outstanding()` entry (`kind == "handoff"`, not yet acked) as a confident block against
    whatever `ledger.handoff_key()` resolves to, and `handoff_key()` FALLS BACK TO THE FILING GOAL'S
    OWN REF whenever no real issue number was recorded (the default outcome for any source without
    `create_dependency`, e.g. `LocalSource` — not a rare failure). Writing `kind="handoff"`
    unconditionally for every `same_area=False` call — including `blocks_goal=False`, a fully
    sanctioned "cross-area FYI, not a blocker" combination — let a degraded/local source's unresolved
    entry confident-block the FILING goal against itself, exactly the false-blocking bug this whole
    axis exists to prevent, one layer deeper than the body marker:
      `(not same_area) and blocks_goal` (a genuine cross-area BLOCKING dependency) → `kind="handoff"`,
        `to=<resolved owner>`, `state="open"` — byte-identical to `hand_off()`'s pre-existing write
        (`hand_off()` always pins `blocks_goal=True`, so its behavior is completely unchanged), so it
        still participates in `ledger.outstanding()`/`unanswered()` and is answerable via
        `handoff.py ack`.
      Anything else (`same_area=True`, OR `blocks_goal=False` regardless of `same_area`) →
        `kind="note"`, `to=<ledger.actor(config, run) if same_area else the resolved owner>`, no
        `state` — deliberately NOT a "handoff": `outstanding()` only ever looks at `kind ==
        "handoff"`, so this can never get stuck as a permanently-unanswered hand-off nobody was ever
        meant to `ack`, AND never triggers `_ledger_signals()`'s ledger-based block either. A
        `same_area=True` note is self-addressed so a LATER session by the same actor (after a
        compact, a crash, or just picking the loop back up) is reminded the tracked issue exists; a
        `same_area=False, blocks_goal=False` note is addressed to the CODEOWNERS-resolved owner
        instead, so they still see it for visibility — just without the (incorrect, for a
        non-blocker) outstanding-hand-off treatment.

    Returns a report dict (`goal`, `area`, `owner`, `issue`, `entry`, `warnings`); never raises."""
    report = {"goal": str(goal), "area": area, "owner": None, "issue": None,
              "entry": None, "warnings": []}

    if same_area:
        report["owner"] = ledger.actor(config, run)          # never raises, never empty (see actor())
    else:
        try:
            report["owner"] = owners.owner_of(project_root(sdlc_dir), area, config)
        except Exception as exc:                               # noqa: BLE001 - roster is advisory
            report["warnings"].append(f"could not read CODEOWNERS: {exc}")
        if not report["owner"]:
            report["warnings"].append(
                f"no owner for area {area!r} — recording unaddressed; "
                "add the area to CODEOWNERS or to config ledger.owners")

    if source is None:
        try:
            source = sources.get_source(sdlc_dir, config)
        except Exception as exc:                               # noqa: BLE001
            report["warnings"].append(f"no backlog source: {exc}")

    if source is not None and hasattr(source, "create_dependency"):
        heading = title or (f"[{area}] {'dependency' if blocks_goal else 'finding'} from "
                            f"{pathlib.Path(str(goal)).name}")
        text = body if body is not None else _tracked_issue_body(goal, area, why, blocks_goal)
        labels = [f"priority:{priority}", f"area:{area}", *extra_labels]
        # goal_label is passed only when it DIFFERS from create_dependency's own default (True) --
        # not "goal_label=immediately_actionable" unconditionally -- so a source implementation that
        # predates this parameter (a test double, or a future non-GitHub source) keeps working
        # exactly as before for the (overwhelmingly common) immediately_actionable=True case; only
        # `immediately_actionable=False`, a genuinely new capability, requires a source that
        # understands the new parameter at all.
        create_kwargs = {"labels": labels}
        if not immediately_actionable:
            create_kwargs["goal_label"] = False
        try:
            report["issue"] = source.create_dependency(heading, text, report["owner"], **create_kwargs)
        except Exception as exc:                               # noqa: BLE001 - never block the park
            report["warnings"].append(f"could not open the tracked issue: {exc}")
    else:
        report["warnings"].append("backlog source cannot open issues — ledger entry only")

    # #466 review: gate on blocks_goal too, not solely same_area -- see the docstring above. Only a
    # genuine cross-area BLOCKING dependency (same_area=False AND blocks_goal=True, hand_off()'s own
    # always-pinned case) may write kind="handoff" -- that is the one kind ledger.outstanding() /
    # backlog_check._ledger_signals() treat as a real, confident block. Everything else is a "note",
    # regardless of same_area, so it can never be mistaken for one.
    if (not same_area) and blocks_goal:
        report["entry"] = ledger.safe_append(
            sdlc_dir, "handoff", goal, config=config, to=report["owner"], area=area,
            issue=int(report["issue"]) if str(report["issue"] or "").isdigit() else None,
            priority=priority, why=why, state="open")
    else:
        report["entry"] = ledger.safe_append(
            sdlc_dir, "note", goal, config=config, to=report["owner"], area=area,
            issue=int(report["issue"]) if str(report["issue"] or "").isdigit() else None,
            priority=priority, why=why)

    if report["issue"] and source is not None:
        # two channels, two audiences (#376): a human-visible narrative comment, AND (blocks_goal
        # only) a machine-readable body marker so a future precheck() run can auto-skip the CURRENT
        # goal without a human re-stating what already happened. "Blocked by #N" is the exact phrase
        # backlog_check.py's _BLOCK_RE requires -- an earlier version of the narrative said
        # "Blocked on", which never matched at all; fixed here too so the human-visible text is
        # consistent with the machine-readable one, not just superficially similar.
        #
        # F14/#338: a resolved owner does not mean the assignment took -- create_dependency falls
        # back to opening the issue unassigned when gh rejects it (a team, most often) and records
        # which happened via last_assignee_applied. A source that predates this (or doesn't expose
        # it) defaults to True: its assignment always either took or raised, so there was never a
        # silent gap to report.
        assignee_applied = getattr(source, "last_assignee_applied", True)
        if blocks_goal:
            narrative = (f"Blocked by a `{area}` dependency — opened #{report['issue']}"
                        + (f" and assigned to @{report['owner']}" if report["owner"] and assignee_applied else "")
                        + f" ({priority}). Parking this goal until it lands.")
        else:
            # deliberately does not match backlog_check._BLOCK_RE -- a non-blocking finding must
            # never read as a park-worthy dependency, in the human-visible channel either.
            narrative = (f"Related `{area}` issue filed — opened #{report['issue']}"
                        + (f" and assigned to @{report['owner']}" if report["owner"] and assignee_applied else "")
                        + f" ({priority}).")
        if hasattr(source, "note"):
            try:
                source.note(str(goal), narrative)
            except Exception as exc:                            # noqa: BLE001
                report["warnings"].append(f"could not comment on the issue: {exc}")
        if blocks_goal and hasattr(source, "append_to_body"):
            try:
                source.append_to_body(str(goal), f"**Blocked by:** #{report['issue']}")
            except Exception as exc:                            # noqa: BLE001
                report["warnings"].append(
                    f"could not record the machine-readable blocker on the blocked issue's body: {exc}")
    return report


def hand_off(sdlc_dir, config, goal, area, why, priority=DEFAULT_PRIORITY,
             title=None, source=None, run=None):
    """Open the dependency, address it, record it. A thin, behavior-preserving wrapper: a hand-off is
    BY DEFINITION a blocking, cross-area, immediately-actionable dependency, so this pins
    `create_tracked_issue()`'s three axes accordingly and supplies the hand-off-specific title/body
    template (`issue_body()`, unchanged) — see `create_tracked_issue()` for the machinery this now
    shares with every other issue the loop creates. Returns a report dict; never raises.

    `issue` is None when the host could not create one (local backlog, no `gh`, or a failed call) —
    the ledger entry is written regardless, because a hand-off nobody can see is the bug this
    function exists to fix."""
    if source is None:
        try:
            source = sources.get_source(sdlc_dir, config)
        except Exception:                                       # noqa: BLE001 - create_tracked_issue
            source = None                                       # retries resolution and reports below

    heading, body = title, None
    if source is not None and hasattr(source, "create_dependency"):
        blocked_url = source.issue_url(goal) if hasattr(source, "issue_url") else ""
        heading = title or f"[{area}] dependency for {pathlib.Path(str(goal)).name}"
        body = issue_body(goal, area, why, blocked_url)

    return create_tracked_issue(sdlc_dir, config, goal, area, why,
                                 same_area=False, immediately_actionable=True, blocks_goal=True,
                                 priority=priority, title=heading, body=body,
                                 extra_labels=[dependency_label(config)],
                                 source=source, run=run)


def acknowledge(sdlc_dir, config, issue, state, why="", goal=None):
    """The other half. Reading a hand-off obliges an answer: taking it, needing time, declining it,
    or closing it out. `deferred` deliberately does NOT settle the hand-off — a promise to look later
    is not a resolution, and the team view keeps showing it.

    F22/#347: `issue` cannot key a LOCAL hand-off — `hand_off()` writes `issue=None` for one (no
    `gh`, or a local backlog), so `ledger.handoff_key()` falls back to `goal`. Settling that hand-off
    means writing an `ack` whose own key falls back the same way, which is why `goal` is accepted
    here: leaving `issue` falsy makes the line below store `issue=None` on the entry, so
    `handoff_key()` reads `goal` on BOTH sides and the two halves meet. Passing an `issue` still wins
    when present, matching `handoff_key`'s own precedence exactly."""
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
    if len(argv) >= 3 and argv[1] == "track":
        sdlc_dir, goal = argv[2], argv[3] if len(argv) > 3 else ""
        flags = ledger._flags(argv[4:])
        area, why = flags.get("area"), flags.get("why")
        # Every safety axis is a REQUIRED value flag, never a bare boolean -- _flags() would accept a
        # bare `--immediately-actionable` as "true", but a caller who forgets it would then silently
        # get "false", exactly the silent-default failure mode this whole design exists to close. A
        # missing OR misspelled value on any of the three is the same hard usage error, exit 2,
        # nothing written -- never a default.
        queue_map = {"actionable": True, "queued": False}
        assignee_map = {"same-area": True, "cross-area": False}
        blocks_map = {"yes": True, "no": False}
        queue, assignee, blocks = flags.get("queue"), flags.get("assignee"), flags.get("blocks")
        if (not goal or not area or not why or queue not in queue_map
                or assignee not in assignee_map or blocks not in blocks_map):
            print("handoff.py track needs <goal> --area <area> --why <text> "
                  "--queue actionable|queued --assignee same-area|cross-area --blocks yes|no "
                  "[--priority P --title T --label L --body-file F]",
                  file=sys.stderr)
            return 2
        # #522: --body-file reads a file verbatim as the new issue's body -- the way the
        # `goal_decompose` file-mode meta-goal (and any other multi-paragraph tracked issue) hands
        # `track` a body too long for a CLI arg. Read BEFORE any create so a missing file can never
        # half-file an issue (ledger._flags parses it to the key "body-file", hyphen kept as-is).
        body = None
        body_file = flags.get("body-file")
        if body_file:
            try:
                body = pathlib.Path(body_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                # #522 review fix 7: UnicodeDecodeError is NOT an OSError subclass -- a binary or
                # wrongly-encoded file used to crash with a raw traceback instead of the same usable
                # refusal a missing file already gets.
                print(f"handoff.py track: could not read --body-file {body_file!r}: {exc}",
                      file=sys.stderr)
                return 2
        config = ledger._config(sdlc_dir)
        report = create_tracked_issue(
            sdlc_dir, config, goal, area, why,
            same_area=assignee_map[assignee], immediately_actionable=queue_map[queue],
            blocks_goal=blocks_map[blocks], priority=flags.get("priority", DEFAULT_PRIORITY),
            title=flags.get("title"), body=body,
            extra_labels=[flags["label"]] if flags.get("label") else ())
        for warning in report["warnings"]:
            print(f"handoff: {warning}", file=sys.stderr)
        print(f"tracked to {report['owner'] or '(unowned)'}"
              + (f" as #{report['issue']}" if report["issue"] else " (no issue opened)")
              + (f"; ledger {report['entry']['id']}" if report["entry"] else "; ledger off"))
        return 0
    if len(argv) >= 3 and argv[1] == "ack":
        sdlc_dir = argv[2]
        flags = ledger._flags(argv[3:])
        issue, goal, state = flags.get("issue"), flags.get("goal"), flags.get("state")
        # F22/#347: a local/issue-less hand-off has no `<n>` to give — requiring --issue
        # unconditionally made it unanswerable forever. --goal is the symmetric alternative
        # (see acknowledge()); at least one of the two must identify which hand-off this answers.
        if not (issue or goal) or state not in ledger.STATES:
            print("handoff.py ack needs --issue <n> (or --goal <goal> for a local/issue-less "
                  f"hand-off) --state {'|'.join(ledger.STATES)}", file=sys.stderr)
            return 2
        entry = acknowledge(sdlc_dir, ledger._config(sdlc_dir), issue, state, flags.get("why", ""),
                            goal=goal)
        print(entry["id"] if entry else "OFF (config: \"ledger\": {\"enabled\": true})")
        return 0
    print("usage: handoff.py open <dir> <goal> --area A --why TEXT [--priority P --title T] | "
          "track <dir> <goal> --area A --why TEXT --queue actionable|queued "
          "--assignee same-area|cross-area --blocks yes|no "
          "[--priority P --title T --label L --body-file F] | "
          f"ack <dir> --issue N | --goal G --state {'|'.join(ledger.STATES)} [--why TEXT]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
