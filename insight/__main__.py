# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`python -m insight` / the `insight` console script (issue #95, E0.S2).

This is a skeleton: it wires up the three subcommands' argument-parsing shape only.
Each is a stub tracked by its own epic. Running one prints where the real work lives
rather than pretending to do something it can't — a stub that pretends to work is
worse than one that says it doesn't.
"""
import argparse
import sys

#: subcommand name -> epic issue tracking its real implementation.
_TRACKING_ISSUE = {
    "ingest": 98,   # E1 - Ingest: collectors, readers, store bootstrap
    "gaps": 115,    # E3 - Gap engine: five typed classes, no LLM in the finding path
    "dash": 123,    # E4 - Dashboard: shell, chart primitives, IC and manager views
}


def build_parser():
    """Build the top-level argparse parser.

    Kept separate from main() so tests can exercise --help and the subcommand shape
    without also going through argv defaulting and process-exit handling.
    """
    parser = argparse.ArgumentParser(
        prog="insight",
        description="LoopSmith Insight: the analytics platform for LoopSmith.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "dash", "gaps"):
        subparsers.add_parser(
            name,
            help="not implemented yet - see issue #%d" % _TRACKING_ISSUE[name],
        )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    issue = _TRACKING_ISSUE[args.command]
    print(
        "insight %s: not implemented yet - see issue #%d" % (args.command, issue),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
