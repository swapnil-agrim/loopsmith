# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`python -m insight` / the `insight` console script (issue #95, E0.S2).

`ingest` (issue #99, E1.S1) is real: it opens/creates a DuckDB store and ensures its
schema exists. `dash` and `gaps` are still stubs tracked by their own epics — running
one prints where the real work lives rather than pretending to do something it can't;
a stub that pretends to work is worse than one that says it doesn't.

`insight.ingest.store` (and therefore `duckdb`) is imported LAZILY, inside main()'s
`ingest` branch only — never at module level here. Hoisting it to the top would make
`--help`, `dash`, and `gaps` all require duckdb to be importable, breaking the "stubs
stay pure" contract on a box without duckdb installed. See
insight/tests/test_cli.py::test_main_module_does_not_import_duckdb_at_top_level, which
pins this structurally via AST (no duckdb needed to run it).
"""
import argparse
import pathlib
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
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="open/create the DuckDB store and ensure its schema exists",
    )
    ingest_parser.add_argument(
        "--db",
        dest="db",
        default=None,
        help="path to the DuckDB store file (default: .sdlc/insight.duckdb under CWD)",
    )
    ingest_parser.add_argument(
        "--collectors-root", dest="collectors_root", default=None,
        help="directory containing the plugin's collector scripts (default: $CLAUDE_PLUGIN_ROOT/"
             "skills, else ./skills relative to CWD; see insight/ingest/collectors.py)",
    )
    ingest_parser.add_argument(
        "--git-window-days", dest="git_window_days", type=int, default=14,
        help="trailing window (days) for git commit/merge counts and merge lead time "
             "(default: 14, matching velocity.py's own default; see insight/ingest/git_reader.py)",
    )
    for name in ("dash", "gaps"):
        subparsers.add_parser(
            name,
            help="not implemented yet - see issue #%d" % _TRACKING_ISSUE[name],
        )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        # Lazy: keeps duckdb out of the import graph for --help/dash/gaps. See the
        # module docstring.
        from insight.ingest.store import open_store, resolve_db_path
        from insight.ingest.packs import ingest_collectors
        from insight.ingest.artifact_reader import ingest_artifacts
        from insight.ingest.git_reader import ingest_git_facts, ingest_merge_lead_time

        path = resolve_db_path(args.db)
        conn = open_store(args.db)
        project_root = pathlib.Path.cwd()
        results = ingest_collectors(conn, project_root, collectors_root=args.collectors_root)
        artifacts = ingest_artifacts(conn, project_root)
        git_pack = ingest_git_facts(conn, project_root, days=args.git_window_days)
        lead_time = ingest_merge_lead_time(conn, project_root, days=args.git_window_days)
        conn.close()
        print("insight ingest: store ready at %s" % path)
        for r in results:
            codes = r["degraded_collector"] + r["degraded_adapter"]
            suffix = " (degraded: %s)" % ", ".join(codes) if codes else ""
            print("insight ingest: %s%s" % (r["schema"], suffix))
        print("insight ingest: %d goal(s), %d slice(s), config %s"
              % (artifacts["goals"], artifacts["slices"],
                 "present" if artifacts["config_present"] else "absent"))
        git_suffix = " (degraded: %s)" % ", ".join(git_pack["degraded"]) if git_pack["degraded"] else ""
        print("insight ingest: %s%s" % (git_pack["schema"], git_suffix))
        print("insight ingest: %d merge lead-time event(s) (%d skipped)"
              % (lead_time["events"], lead_time["skipped"]))
        return 0
    issue = _TRACKING_ISSUE[args.command]
    print(
        "insight %s: not implemented yet - see issue #%d" % (args.command, issue),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
