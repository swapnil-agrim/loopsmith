# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`python -m insight` / the `insight` console script (issue #95, E0.S2).

`ingest` (issue #99, E1.S1) and `gaps` (issue #122, E3.S7) are real: `ingest`
opens/creates a DuckDB store and ensures its schema exists; `gaps` evaluates every
loaded gap rule and reports findings, with `--compare` classifying each against a
prior run. `dash` is the only subcommand still a stub, tracked by its own epic —
running it prints where the real work lives rather than pretending to do something it
can't; a stub that pretends to work is worse than one that says it doesn't.

`insight.ingest.store` and `insight.gaps.{report,compare}` (and therefore `duckdb`)
are imported LAZILY, inside main()'s `ingest`/`gaps` branches only — never at module
level here. Hoisting them to the top would make `--help` and `dash` require duckdb to
be importable, breaking the "stubs stay pure" contract on a box without duckdb
installed. See
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
    ingest_parser.add_argument(
        "--gh-window-days", dest="gh_window_days", type=int, default=14,
        help="trailing window (days) for gh-backed PR review timings and check outcomes "
             "(default: 14; requires the gh CLI -- absent/unauthenticated/unreachable gh "
             "degrades, never fails ingest; see insight/ingest/gh_reader.py)",
    )
    ingest_parser.add_argument(
        "--repos", dest="repos", default=None,
        help="a glob of repo directories to ingest into the SAME store, resolved against CWD "
             "(QUOTE IT, e.g. --repos '../*/' -- otherwise your shell expands it before this "
             "program ever sees it). Default: ingest CWD only (unchanged from before issue "
             "#106). A repo whose match is not a directory is silently excluded; a matched "
             "directory with no .sdlc/ is skipped with an explicit reason, never processed; "
             "zero matches is a usage error (exit 1). See insight/ingest/repo_scan.py.",
    )
    gaps_parser = subparsers.add_parser(
        "gaps",
        help="evaluate every loaded gap rule and report findings (WARN/FAIL/PASS/ABSENT); "
             "--compare classifies each against a prior run (issue #122, E3.S7)",
    )
    gaps_parser.add_argument(
        "--db", dest="db", default=None,
        help="path to the DuckDB store file (default: .sdlc/insight.duckdb under CWD)",
    )
    gaps_parser.add_argument(
        "--json", dest="json_out", default=None,
        help="write the full findings report (JSON) to this path",
    )
    gaps_parser.add_argument(
        "--compare", dest="compare", default=None,
        help="path to a prior report written by --json; classifies each finding regressed / "
             "improved / still-failing against it",
    )
    for name in ("dash",):
        subparsers.add_parser(name, help="not implemented yet - see issue #%d" % _TRACKING_ISSUE[name])
    return parser


def _ingest_one_repo(conn, project_root, args, label=None):
    """The single-repo ingest body (issues #99-#104, unchanged in substance) -- extracted so
    #106's per-repo loop can call it once per adopted repo, all sharing the ONE open
    connection/store opened before the loop. `label`, when given, prefixes every summary line
    with the repo's own path so multi-repo output stays attributable; the DEFAULT (no --repos)
    mode passes label=None and prints EXACTLY what it printed before this story."""
    from insight.ingest.packs import ingest_collectors
    from insight.ingest.artifact_reader import ingest_artifacts
    from insight.ingest.git_reader import ingest_git_facts, ingest_merge_lead_time
    from insight.ingest.gh_reader import ingest_gh_reader
    from insight.ingest.ledger_writer import ingest_ledger

    prefix = "%s: " % label if label else ""
    results = ingest_collectors(conn, project_root, collectors_root=args.collectors_root)
    artifacts = ingest_artifacts(conn, project_root)
    git_pack = ingest_git_facts(conn, project_root, days=args.git_window_days)
    lead_time = ingest_merge_lead_time(conn, project_root, days=args.git_window_days)
    gh_pack = ingest_gh_reader(conn, project_root, days=args.gh_window_days)
    ledger_result = ingest_ledger(conn, project_root)
    for r in results:
        codes = r["degraded_collector"] + r["degraded_adapter"]
        suffix = " (degraded: %s)" % ", ".join(codes) if codes else ""
        print("insight ingest: %s%s%s" % (prefix, r["schema"], suffix))
    print("insight ingest: %s%d goal(s), %d slice(s), config %s"
          % (prefix, artifacts["goals"], artifacts["slices"],
             "present" if artifacts["config_present"] else "absent"))
    git_suffix = " (degraded: %s)" % ", ".join(git_pack["degraded"]) if git_pack["degraded"] else ""
    print("insight ingest: %s%s%s" % (prefix, git_pack["schema"], git_suffix))
    print("insight ingest: %s%d merge lead-time event(s) (%d skipped)"
          % (prefix, lead_time["events"], lead_time["skipped"]))
    gh_suffix = " (degraded: %s)" % ", ".join(gh_pack["degraded"]) if gh_pack["degraded"] else ""
    print("insight ingest: %s%s%s" % (prefix, gh_pack["schema"], gh_suffix))
    print("insight ingest: %s%d PR review event(s), %d PR check row(s)"
          % (prefix, gh_pack["review_events"], gh_pack["check_rows"]))
    print("insight ingest: %s%d ledger event(s), %d hand-off(s) (%d skipped)"
          % (prefix, ledger_result["events"], ledger_result["handoffs"], ledger_result["skipped"]))


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        # Lazy: keeps duckdb out of the import graph for --help/dash/gaps. See the
        # module docstring.
        from insight.ingest.store import open_store, resolve_db_path
        from insight.ingest.repo_scan import (
            SKIP_NO_SDLC, SKIP_UNREADABLE, expand_repos, is_adopted,
        )
        from insight.ingest.packs import project_id_for, remote_identity_for
        from insight.ingest.artifact_reader import write_project_snapshot

        if args.repos:
            roots = expand_repos(args.repos, pathlib.Path.cwd())
            if not roots:
                print(
                    "insight ingest: --repos %r matched 0 directories" % args.repos,
                    file=sys.stderr,
                )
                return 1
        else:
            roots = [pathlib.Path.cwd()]

        # Adoption is checked BEFORE open_store() is ever called -- open_store's own
        # path.parent.mkdir(parents=True, exist_ok=True) creates <cwd>/.sdlc/ as a side effect
        # when the default db path is used, and the default single-repo mode's own root IS
        # cwd. Checking adoption after that mkdir would always see "adopted" for a genuinely
        # non-adopted directory -- a real bug, reproduced and fixed at plan review; see
        # .sdlc/plans/106.md Design decision E.
        #
        # is_adopted() itself never raises (it guards Path.is_dir()'s own OSError -- see its
        # docstring, fixed at code review after a chmod-000 sibling directory crashed this exact
        # list comprehension, taking down the whole --repos run before open_store() ever ran).
        # It returns a TRI-STATE: True, False, or None ("cannot confirm" -- a directory this
        # process could not stat at all). This comprehension is therefore itself still safe to
        # leave unguarded -- but the loop below must NOT collapse `adopted is None` into the
        # `not adopted` branch (Python's `not None` is True, which would silently mislabel an
        # unreadable directory as the confirmed-non-adopted SKIP_NO_SDLC case).
        adoption = [(root, is_adopted(root)) for root in roots]

        path = resolve_db_path(args.db)
        conn = open_store(args.db)
        print("insight ingest: store ready at %s" % path)

        for root, adopted in adoption:
            label = str(root) if args.repos else None
            prefix = "%s: " % label if label else ""
            try:
                if adopted is None:
                    # Cannot confirm adoption (is_adopted's own OSError guard) -- a DIFFERENT
                    # fact from "confirmed no .sdlc/", recorded under its own skip_reason so a
                    # later query never conflates the two. See repo_scan.SKIP_UNREADABLE.
                    project_id = project_id_for(root)
                    repo, remote_url_sha256 = remote_identity_for(root)
                    write_project_snapshot(conn, project_id, None, repo=repo,
                                            remote_url_sha256=remote_url_sha256,
                                            adopted=False, skip_reason=SKIP_UNREADABLE)
                    print("insight ingest: %sskipped (%s)" % (prefix, SKIP_UNREADABLE))
                    continue
                if not adopted:
                    project_id = project_id_for(root)
                    repo, remote_url_sha256 = remote_identity_for(root)
                    write_project_snapshot(conn, project_id, None, repo=repo,
                                            remote_url_sha256=remote_url_sha256,
                                            adopted=False, skip_reason=SKIP_NO_SDLC)
                    print("insight ingest: %sskipped (%s)" % (prefix, SKIP_NO_SDLC))
                    continue
                _ingest_one_repo(conn, root, args, label=label)
            except Exception:
                # Never-raises, one level up: one repo's unforeseen failure must not abort the
                # rest of a --repos run (or the single-repo default). See .sdlc/plans/106.md
                # Design decision F.
                print("insight ingest: %sunexpected error, skipped" % prefix, file=sys.stderr)
                continue
        conn.close()
        return 0
    if args.command == "gaps":
        # Lazy, mirroring the `ingest` branch: keeps duckdb (and everything transitively under
        # insight.gaps/insight.ingest.store) out of the import graph for --help/dash. See the
        # module docstring and insight/gaps/report.py's own docstring.
        import json
        from insight.ingest.store import open_store
        from insight.gaps.report import build_report, render_report, json_default

        conn = open_store(args.db)
        report = build_report(conn)
        conn.close()

        delta = None
        if args.compare:
            prior_path = pathlib.Path(args.compare)
            if not prior_path.exists():
                print("SKIP: %s not found" % prior_path, file=sys.stderr)
                return 3
            from insight.gaps.compare import compare_reports
            prior = json.loads(prior_path.read_text())
            delta = compare_reports(prior, report)
            report["delta"] = delta

        print(render_report(report, delta))
        if args.json_out:
            out = pathlib.Path(args.json_out)
            out.write_text(json.dumps(report, indent=2, default=json_default))
            print("wrote %s" % out)
        return 1 if (report["verdict"]["failing"] or report["verdict"]["errored"]) else 0
    issue = _TRACKING_ISSUE[args.command]
    print(
        "insight %s: not implemented yet - see issue #%d" % (args.command, issue),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
