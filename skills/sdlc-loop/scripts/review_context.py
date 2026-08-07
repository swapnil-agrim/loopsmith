#!/usr/bin/env python3
"""Assemble the context an INDEPENDENT reviewer needs — the project, never the author.

THE PROBLEM. A maker that reviews its own work rationalizes it; on a lower-tier model that confirmation
bias amplifies hallucination exactly where a review is supposed to catch it. So every review gate
(plan-review, code review, the post-PR review) must run as a FRESH reviewer that never saw the maker's
reasoning. But a fresh reviewer handed only the diff has the opposite failure: it cannot see BLAST
RADIUS — the callers a small change breaks two files away, the invariant it quietly violates — because
the change is on screen and the impact surface is not. The reviewer needs the PROJECT (what it is for,
its rules, the whole codebase to read), not the AUTHOR (their justifications).

WHERE THE BOUNDARY IS. A python script cannot spawn a subagent — only the agent can (the same boundary
`slices.py` states). So this module COMPUTES the reviewer's context PACK and the SKILL prose DISPATCHES
one fresh subagent per review, fed only this pack. The value here is that the payload is assembled by
code: the maker's transcript is excluded BY CONSTRUCTION (there is no field for it), and what the
reviewer is told — "you did not write this; trace blast radius across the whole repo" — is consistent
across every gate and testable, instead of prose each phase is trusted to reproduce.

WHAT GOES IN. The project's own words, north-star first (same ordering `sdlc-context` uses), but here
UN-GATED by the knowledge graph and reviewer-framed: the north-star (vision / strategy / non-goals /
numbered architecture rules the plan is judged against), project.md + the governing CLAUDE.md, the
contracts dir, the goal, and a pointer to the artifact under review. Never the maker's context.

Fail-open: a missing file drops its line, never raises — a reviewer with a partial brief still reviews;
one that crashed assembling the brief reviews nothing. ASCII-only output (a non-utf8 locale must not
break it). Zero deps.
"""
import pathlib
import sys

#: The gates this brief serves and what each reviews. Kept explicit so an unknown `--for` is a loud
#: error, not a silently-empty brief that reads as "nothing to review".
PHASES = {
    "plan-review": "the implementation PLAN (before any code)",
    "code-review": "the branch DIFF (before the PR)",
    "pr-review": "the opened PR's real, mergeable diff (after commit + CI)",
    "retro": "the shipped change vs its stated intent",
}

#: Project docs pulled into the pack, in relevance order — north-star first (the highest grounding: a
#: plan/diff is judged against its strategy, non-goals, and architecture rules). Each is optional; a
#: repo without one just drops that line.
_PROJECT_DOCS = [
    ("context/north-star.md", "north-star (vision / strategy / non-goals / architecture rules)"),
    ("project.md", "project (stack, conventions, verify command)"),
]


def _read(path):
    """Fail-open read: an unreadable or absent file contributes nothing, never an exception — a partial
    brief is a working reviewer, a crash is a skipped review."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def _project_context(sdlc_dir, repo_root):
    """The project's own words the reviewer is grounded in. north-star + project.md from .sdlc, the
    governing CLAUDE.md and any contracts dir from the repo root. Pointers where a file is large
    (CLAUDE.md, contracts) so the reviewer pulls detail on demand rather than drowning in it."""
    base = pathlib.Path(sdlc_dir)
    lines = []
    for rel, label in _PROJECT_DOCS:
        body = _read(base / rel)
        if body:
            lines.append("## %s\n%s" % (label, body))

    root = pathlib.Path(repo_root)
    if (root / "CLAUDE.md").is_file():
        lines.append("## conventions\nRead `CLAUDE.md` at the repo root - its rules bind this change.")
    contracts = root / "docs" / "CONTRACTS"
    if contracts.is_dir():
        frozen = sorted(p.name for p in contracts.glob("*") if p.is_file())
        if frozen:
            lines.append("## contracts\nHonor `docs/CONTRACTS/` (a FROZEN contract is not yours to "
                         "change): %s" % ", ".join(frozen))
    return "\n\n".join(lines)


def _dossier(sdlc_dir, goal):
    """The Research phase's dossier for this goal, if it wrote one.

    This is the richest grounding available to an author-blind reviewer and the one thing that closes
    the gap the module docstring names: a fresh reviewer "cannot see BLAST RADIUS". Research already
    measured it — every affected site with `file:line`, the debt sitting in the radius, and the exact
    sweep commands, stored verbatim so they can be RE-RUN. Handing those over turns "trace the callers
    yourself" from an instruction into a checkable starting point, and lets the reviewer catch what
    landed *after* research by re-running the query rather than trusting the list.

    It is a project artifact, not the maker's reasoning — it records what the code IS, never why the
    author chose what they chose, so it does not reintroduce the bias this module exists to remove."""
    research = pathlib.Path(sdlc_dir) / "research"
    if not (goal and research.is_dir()):
        return ""
    # Path(goal).stem extracts only the filename component (no directories or extension), preventing traversal (#486).
    stem = pathlib.Path(goal).stem
    # Goals are `NNNN-slug.md`; a dossier may be filed under either the full stem or the bare slug.
    slug = stem.split("-", 1)[1] if "-" in stem and stem.split("-", 1)[0].isdigit() else stem
    for name in dict.fromkeys([stem, slug]):          # ordered, de-duplicated
        if name and (research / (name + ".md")).is_file():
            return _read(research / (name + ".md"))
    return ""


def _artifact_pointer(phase, artifact):
    """Where the reviewer finds the thing under review — a path, a PR number, or the phase default.
    NOT the artifact's content and NEVER the maker's reasoning about it: the reviewer opens it fresh."""
    if artifact:
        if phase == "pr-review":
            return "The PR under review: #%s. Read its real diff (`gh pr diff %s`)." % (artifact, artifact)
        return "The artifact under review: `%s`. Read it, then verify it against the code." % artifact
    return {
        "plan-review": "The active plan under `.sdlc/plans/`. Read it, then verify every claim against the code.",
        "code-review": "The branch's diff vs its base. Review the change, then read around it.",
        "pr-review": "The opened PR's diff. Read it fresh - this is the review AFTER the PR, not the pre-PR self-review.",
        "retro": "The shipped change and the goal it claimed to serve.",
    }[phase]


def brief(sdlc_dir, goal, phase, artifact="", repo_root="."):
    """The independent reviewer's context pack: who they are, what the project is for, what to review,
    and the one rule that separates a real review from a rubber-stamp. Returns a string ready to hand a
    fresh subagent. Raises ValueError only on an unknown phase — everything else fails open."""
    if phase not in PHASES:
        raise ValueError("unknown --for %r; one of: %s" % (phase, ", ".join(sorted(PHASES))))

    goal_text = _read(goal) if goal and pathlib.Path(goal).exists() else (goal or "").strip()
    # In github mode `$goal` is a bare issue NUMBER, not the intent - a reviewer handed only "42" has
    # lost the acceptance criteria it is meant to judge against. Point it at the issue instead.
    if goal_text.isdigit():
        goal_text = ("GitHub issue #%s - read it (`gh issue view %s`) for the full intent and "
                     "acceptance criteria, and judge the change against those." % (goal_text, goal_text))
    project = _project_context(sdlc_dir, repo_root)

    parts = [
        "# Independent review brief - %s" % phase,
        # The separation rule, stated to the reviewer itself. This is the whole point: author-blind by
        # construction (no maker field exists), and told to earn the review by tracing impact from code.
        "You are an INDEPENDENT reviewer. You did NOT write this and you have not seen the author's "
        "reasoning - judge only what is here plus the code. You have full READ access to the whole "
        "repository: trace every caller and assess the blast radius from the code itself, not just the "
        "changed files. The diff is the change; the codebase is the impact surface. Try to break it; a "
        "review that only agreed did not review.",
        "## What you are reviewing\n%s" % _artifact_pointer(phase, artifact),
    ]
    if project:
        parts.append("## What the project is for (judge the change against this)\n%s" % project)
    if goal_text:
        parts.append("## The goal this change serves\n%s" % goal_text)

    dossier = _dossier(sdlc_dir, goal)
    if dossier:
        parts.append("## Blast radius already measured (Research phase)\nThis is the project's own "
                     "survey, not the author's argument. Re-run its stored queries: a site that landed "
                     "AFTER research is exactly what a diff-only review misses.\n%s" % dossier)

    gaps = _missing(project, goal_text, dossier, phase, artifact)
    if gaps:
        # Fail-open keeps the review RUNNING on a partial brief; this keeps it HONEST about it. An
        # under-briefed reviewer returning a confident "no issues" is worse than a biased one - the
        # verdict is indistinguishable from a real pass. Same rule pipeline.py states for stages:
        # no instrument reads ABSENT, never PASS.
        parts.append("## Inputs NOT available to you\n%s\n\nDo not imply coverage you did not have. "
                     "Read them yourself from the repo where they exist; where they do not, say so in "
                     "your verdict. A review missing a required input reads ABSENT, never PASS."
                     % "\n".join("- %s" % g for g in gaps))
    return "\n\n".join(parts)


def _missing(project, goal_text, dossier, phase, artifact):
    """What the reviewer was NOT given, stated as fact. Only genuine gaps - a drop-in repo with no
    north-star must not be nagged every run, or the notice stops being read."""
    gaps = []
    if not goal_text:
        gaps.append("The goal / acceptance criteria - you cannot judge fitness without it.")
    if not project:
        gaps.append("No north-star or project.md: strategy, non-goals and architecture rules are "
                    "unavailable, so alignment cannot be judged - only correctness.")
    if not dossier and phase in ("plan-review", "code-review", "pr-review"):
        gaps.append("No Research dossier: blast radius was never measured for this goal. Trace "
                    "callers from the code yourself before concluding the change is contained.")
    if artifact and phase != "pr-review" and not pathlib.Path(artifact).exists():
        gaps.append("The artifact `%s` does not exist - you have nothing to review. Stop and say so; "
                    "do not review from the goal text alone." % artifact)
    return gaps


def main(argv):
    if len(argv) >= 4 and argv[1] == "brief":
        sdlc_dir, goal = argv[2], argv[3]
        phase, artifact, repo_root = "", "", "."
        rest = argv[4:]
        i = 0
        while i < len(rest):
            if rest[i] == "--for" and i + 1 < len(rest):
                phase = rest[i + 1]; i += 2
            elif rest[i] == "--artifact" and i + 1 < len(rest):
                artifact = rest[i + 1]; i += 2
            elif rest[i] == "--repo-root" and i + 1 < len(rest):
                repo_root = rest[i + 1]; i += 2
            else:
                i += 1
        if not phase:
            print("usage: review_context.py brief <sdlc_dir> <goal> --for %s [--artifact <path|PR#>] "
                  "[--repo-root <dir>]" % "|".join(sorted(PHASES)), file=sys.stderr)
            return 2
        try:
            print(brief(sdlc_dir, goal, phase, artifact, repo_root))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    print("usage: review_context.py brief <sdlc_dir> <goal> --for %s [--artifact <path|PR#>] "
          "[--repo-root <dir>]" % "|".join(sorted(PHASES)), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
