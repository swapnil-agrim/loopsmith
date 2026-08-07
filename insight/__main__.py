# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`python -m insight` / the `insight` console script (issue #95, E0.S2).

`ingest` (issue #99, E1.S1), `gaps` (issue #122, E3.S7), and `dash` (issue #124, E4.S1)
are all real now: `ingest` opens/creates a DuckDB store and ensures its schema exists;
`gaps` evaluates every loaded gap rule and reports findings, with `--compare`
classifying each against a prior run; `dash` builds a self-contained `index.html` from
the real metric catalog and gap findings report, optionally serving it over loopback
HTTP with `--serve`. There is no longer a stub subcommand in this parser.

`insight.ingest.store`, `insight.gaps.{report,compare}`, and `insight.dash.{render,serve}`
(and therefore `duckdb`) are imported LAZILY, inside main()'s `ingest`/`gaps`/`dash`
branches only — never at module level here. Hoisting them to the top would make
`--help` require duckdb to be importable, breaking the "argparse skeleton stays pure"
contract on a box without duckdb installed. See
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
        "--claude-analytics", dest="claude_analytics", action="store_true", default=False,
        help="EXPERIMENTAL, opt-in, off by default: also query the Claude Code Analytics API "
             "for org-level usage. Requires an Admin API key in the ANTHROPIC_ADMIN_API_KEY "
             "env var (never a CLI flag -- a secret typed as a flag leaks into shell history "
             "and `ps`); unavailable on Bedrock/Foundry/Vertex/Claude Platform on AWS, so it "
             "degrades silently rather than failing ingest. NOT VERIFIED end-to-end against the "
             "real API in this repo -- see insight/ingest/analytics_reader.py's own module "
             "docstring for exactly what is and is not confirmed. When this flag is omitted, no "
             "claude-analytics/v1 row is written at all -- a collector that was never asked to "
             "run has no coverage story to tell.",
    )
    ingest_parser.add_argument(
        "--claude-analytics-window-days", dest="claude_analytics_window_days", type=int,
        default=14,
        help="trailing window (days) for --claude-analytics (default: 14, matching "
             "--gh-window-days's own default); ignored unless --claude-analytics is passed",
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
    dash_parser = subparsers.add_parser(
        "dash",
        help="build a static, self-contained HTML dashboard from the DuckDB store; --serve "
             "optionally serves it over loopback HTTP (issue #124, E4.S1)",
    )
    dash_parser.add_argument(
        "--db", dest="db", default=None,
        help="path to the DuckDB store file (default: .sdlc/insight.duckdb under CWD)",
    )
    dash_parser.add_argument(
        "--out", dest="out", default=None,
        help="directory to write the dashboard into (default: .sdlc/insight-dash under CWD); "
             "writes <out>/index.html",
    )
    dash_parser.add_argument(
        "--serve", dest="serve", action="store_true",
        help="after building, serve --out over 127.0.0.1 (loopback only) until interrupted -- "
             "OPTIONAL: the built index.html also opens directly via file://, no server required",
    )
    dash_parser.add_argument(
        # 8787 is also insight.dash.serve.DEFAULT_PORT -- build_parser() cannot import
        # insight.dash.serve to share it without breaking the lazy-import/--help contract (it
        # would pull duckdb-adjacent modules in transitively), so the literal is duplicated
        # deliberately. If you change this default, also change DEFAULT_PORT in
        # insight/dash/serve.py.
        "--port", dest="port", type=int, default=8787,
        help="port for --serve (default: 8787); ignored without --serve",
    )
    dash_parser.add_argument(
        "--actor", dest="actor", default=None,
        help="build the IC persona view (<out>/ic.html) scoped to this actor; falls back to "
             ".sdlc/config.json's ledger.actor when omitted (issue #126, E4.S3). If neither "
             "resolves, ic.html is skipped with a WARNING on stdout -- insight dash itself still "
             "exits 0.",
    )
    # `users` (issue #306, E18.S1): the first NESTED subcommand in this file (verb+noun, `users
    # add`, rather than a bare top-level verb) -- the only reading consistent with the issue's own
    # literal wording ("insight users add"), and leaves room for `list`/`remove` in later stories
    # without redesigning the top level. `--username`/`--role` are required FLAGS, not
    # positionals -- ingest/gaps/dash have no positional-argument precedent anywhere in this file.
    # The password is NEVER a CLI argument, in any form -- prompted interactively via
    # getpass.getpass(), twice, in main()'s dispatch branch below. Same reasoning already written
    # into this file for a different secret (see --claude-analytics's own help text above): a
    # secret typed as a flag leaks into shell history and `ps`. See .sdlc/plans/306.md Decision 5.
    users_parser = subparsers.add_parser(
        "users",
        help="manage insight web app accounts (issue #306, E18.S1)",
    )
    users_subparsers = users_parser.add_subparsers(dest="action", required=True)
    users_add_parser = users_subparsers.add_parser(
        "add",
        help="create a new account with a role; password is prompted interactively, never a "
             "CLI flag or positional",
    )
    users_add_parser.add_argument(
        "--username", dest="username", required=True,
        help="the new account's username",
    )
    users_add_parser.add_argument(
        "--role", dest="role", required=True,
        help="the new account's role -- a free-form, non-empty string, stored verbatim; no "
             "fixed vocabulary exists yet (E19's job)",
    )

    # `verify` (issue #307 [E18.S2], .sdlc/plans/307.md Decision 1): the Node web tier's
    # credential-check bridge shells out to this. Username/password NEVER arrive as flags or
    # positionals here either (same reasoning as `add`'s own comment above) -- they arrive as a
    # single JSON object on stdin. `--accounts-path` exists ONLY on this action, not on `add`: the
    # Node bridge always passes it explicitly rather than relying on this process's CWD matching
    # resolve_accounts_path's CWD-relative default, which would be wrong in the standalone Docker
    # runtime (see .sdlc/plans/307.md Decision 1's CWD discussion).
    users_verify_parser = users_subparsers.add_parser(
        "verify",
        help="verify a username/password pair read as JSON from stdin, for the Node web tier's "
             "credential bridge (issue #307 [E18.S2]); never accepts the password as a flag",
    )
    users_verify_parser.add_argument(
        "--accounts-path", dest="accounts_path", default=None,
        help="override the accounts store path (default: resolve_accounts_path's own CWD-"
             "relative default, kept for manual/local use; the Node bridge always passes this "
             "explicitly)",
    )

    # the stub loop now has nothing left in it -- kept as an empty tuple rather than deleted, so
    # a FUTURE new stub subcommand has an obvious place to land, mirroring how this loop already
    # shrank from {"gaps", "dash"} to {"dash"} in #122 without changing shape
    for name in ():
        subparsers.add_parser(name, help="not implemented yet - see issue #%d" % _TRACKING_ISSUE[name])
    return parser


def _ingest_one_repo(conn, project_root, args, label=None):
    """The single-repo ingest body (issues #99-#104, unchanged in substance) -- extracted so
    #106's per-repo loop can call it once per adopted repo, all sharing the ONE open
    connection/store opened before the loop. `label`, when given, prefixes every summary line
    with the repo's own path so multi-repo output stays attributable; the DEFAULT (no --repos)
    mode passes label=None and prints EXACTLY what it printed before this story.

    `ingest_analytics_reader` is called ONLY when `args.claude_analytics` is set (FIXED post-#146:
    an earlier revision called it unconditionally, which made it write an `analytics_disabled`-
    degraded claude-analytics/v1 row on EVERY run, permanently dragging every non-adopter's
    `insight gaps` verdict from PASS to WARN via insight/gaps/coverage_degraded_collector.sql --
    a deliberately all-schema rule with no exclusion for this collector. A collector that was
    never asked to run has no coverage story to tell, so a non-adopter's run now writes no
    claude-analytics/v1 row at all, and the summary print below stays silent about it too --
    no noise for the common case of never having opted in)."""
    from insight.ingest.packs import ingest_collectors
    from insight.ingest.artifact_reader import ingest_artifacts
    from insight.ingest.git_reader import ingest_git_facts, ingest_merge_lead_time
    from insight.ingest.gh_reader import ingest_gh_reader
    from insight.ingest.ledger_writer import ingest_ledger
    from insight.ingest.goal_lifecycle import ingest_goal_lifecycle

    prefix = "%s: " % label if label else ""
    results = ingest_collectors(conn, project_root, collectors_root=args.collectors_root)
    artifacts = ingest_artifacts(conn, project_root)
    git_pack = ingest_git_facts(conn, project_root, days=args.git_window_days)
    lead_time = ingest_merge_lead_time(conn, project_root, days=args.git_window_days)
    gh_pack = ingest_gh_reader(conn, project_root, days=args.gh_window_days)
    if args.claude_analytics:
        from insight.ingest.analytics_reader import ingest_analytics_reader
        analytics_pack = ingest_analytics_reader(
            conn, project_root, days=args.claude_analytics_window_days,
        )
    else:
        analytics_pack = None
    ledger_result = ingest_ledger(conn, project_root)
    # issue #217: must run AFTER ingest_ledger -- it derives fact_goal's lifecycle columns by
    # replaying the fact_event rows ingest_ledger just wrote; order matters.
    lifecycle_result = ingest_goal_lifecycle(conn, project_root)
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
    if analytics_pack is not None:
        analytics_suffix = (" (degraded: %s)" % ", ".join(analytics_pack["degraded"])
                             if analytics_pack["degraded"] else "")
        print("insight ingest: %s%s%s" % (prefix, analytics_pack["schema"], analytics_suffix))
    print("insight ingest: %s%d ledger event(s), %d hand-off(s) (%d skipped)"
          % (prefix, ledger_result["events"], ledger_result["handoffs"], ledger_result["skipped"]))
    print("insight ingest: %s%d goal(s) derived from event replay (%d stale row(s) purged)"
          % (prefix, lifecycle_result["goals"], lifecycle_result["purged"]))


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
            # A prior file is an ARTIFACT OF AN EARLIER `--json` RUN, so it can be truncated or
            # half-written (disk full, SIGKILL mid-write) in a way the missing-file case above
            # would not catch. pipeline.py's own `card --compare` loads its prior unguarded and
            # would traceback here; the mirroring mandate is over the CLASSIFICATION semantics,
            # not over that, so this degrades to the same SKIP/exit-3 path as a missing file.
            try:
                prior = json.loads(prior_path.read_text())
            except ValueError as exc:
                print("SKIP: %s is not valid JSON (%s)" % (prior_path, exc), file=sys.stderr)
                return 3
            if not isinstance(prior, dict):
                print("SKIP: %s is not a findings report (top level is %s, not an object)"
                      % (prior_path, type(prior).__name__), file=sys.stderr)
                return 3
            delta = compare_reports(prior, report)
            report["delta"] = delta

        print(render_report(report, delta))
        if args.json_out:
            out = pathlib.Path(args.json_out)
            out.write_text(json.dumps(report, indent=2, default=json_default))
            print("wrote %s" % out)
        return 1 if (report["verdict"]["failing"] or report["verdict"]["errored"]) else 0
    if args.command == "dash":
        # Lazy, mirroring ingest/gaps: keeps duckdb out of the import graph for --help. Neither
        # insight.dash.render nor insight.dash.serve actually needs duckdb directly, but the
        # convention is kept for symmetry and because the AST test below pins it either way.
        from insight.ingest.store import open_store, resolve_db_path
        from insight.metrics.loader import MetricLoadError
        from insight.dash.render import (
            render_dashboard, assert_self_contained, DEFAULT_OUT_DIR, CoverageDenominatorMissing,
        )
        from insight.dash.serve import serve_forever_until_interrupted

        db_path = resolve_db_path(args.db)
        conn = open_store(args.db)
        out_dir = pathlib.Path(args.out) if args.out else pathlib.Path(".sdlc") / DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            html_text, summary = render_dashboard(conn, str(db_path))
        except (MetricLoadError, CoverageDenominatorMissing) as e:
            # A first-party bug in the shipped .sql catalog -- fatal, never silently swallowed
            # (see load_metrics' own fail-hard Design decision D; this never happens against the
            # real catalog today, but the CLI must not pretend otherwise if it ever does).
            # CoverageDenominatorMissing (issue #129 D8) is the same shape of contract violation
            # as a bad header: a class-2 metric's own view lacking its coverage-denominator
            # columns, caught here rather than surfacing as a raw traceback.
            print("insight dash: metric catalog failed to load: %s" % e, file=sys.stderr)
            return 1
        finally:
            conn.close()

        assert_self_contained(html_text)  # belt-and-suspenders -- Decision 4b; should be unreachable

        index_path = out_dir / "index.html"
        # errors="replace", not the encoding= default -- a --db path containing invalid UTF-8
        # bytes (os.fsdecode's own surrogateescape) survives html.escape() unharmed but crashes a
        # plain write_text() with UnicodeEncodeError; live-reproduced and fixed, see
        # .sdlc/plans/124.md section N.
        index_path.write_text(html_text, encoding="utf-8", errors="replace")

        if not summary["ever_ingested"]:
            print("insight dash: WARNING never ingested -- wrote onboarding shell to %s" % index_path)
        elif not summary["has_data"]:
            print("insight dash: WARNING ingested, nothing measurable yet -- wrote %s" % index_path)
        else:
            print(
                "insight dash: wrote %s (%d metrics, %d with data; gaps verdict %s)"
                % (index_path, summary["metric_count"], summary["metrics_with_data"],
                   summary["gaps_verdict"])
            )

        # IC persona view (issue #126, E4.S3): a SECOND, actor-scoped file, ic.html, built from a
        # fresh connection (the one above is already closed). Lazy import, same convention as
        # everything else in this branch. Unresolved actor is NOT a `dash` failure -- see
        # insight.dash.actor's own module docstring (Decision 1/2 of .sdlc/plans/126.md) and the
        # six pre-existing `dash` CLI tests (test_cli.py:742-819) that run with no config/flag and
        # assert code == 0, stderr == "" -- this branch must not regress either.
        from insight.dash.actor import ActorResolutionError, resolve_actor
        from insight.dash.ic import render_ic_view

        try:
            actor = resolve_actor(pathlib.Path(".sdlc"), explicit=args.actor)
        except ActorResolutionError as e:
            print("insight dash: WARNING no actor resolved -- skipping IC view (%s)" % e)
        else:
            ic_conn = open_store(args.db)
            try:
                ic_html, _ic_summary = render_ic_view(ic_conn, actor)
            finally:
                ic_conn.close()
            assert_self_contained(ic_html)  # belt-and-suspenders, mirrors index.html above
            ic_path = out_dir / "ic.html"
            ic_path.write_text(ic_html, encoding="utf-8", errors="replace")
            print("insight dash: wrote %s (IC view for %s)" % (ic_path, actor))

        # Manager persona view (issue #127, E4.S4): a THIRD, team-wide file, manager.html, built
        # from yet another fresh connection (ic_conn above, if it was opened at all, is already
        # closed) -- mirrors the render->close->reopen pattern already used twice in this branch.
        # UNCONDITIONAL, unlike ic.html: the manager view has no per-viewer identity to resolve
        # (Decision 5 of .sdlc/plans/127.md), so it never touches resolve_actor and is written on
        # every successful `dash` run, regardless of whether an actor resolves.
        from insight.dash.manager import render_manager_view

        manager_conn = open_store(args.db)
        try:
            manager_html, _manager_summary = render_manager_view(manager_conn)
        except (MetricLoadError, CoverageDenominatorMissing) as e:
            # Same treatment as the render_dashboard call above -- issue #129 review: this call
            # was previously bare, relying on the implicit (and undocumented) invariant that
            # render_dashboard's own registry-wide sweep over the same catalog would always raise
            # first. That ordering is not guaranteed (a reordering or a metrics_dir divergence
            # breaks it silently), so CoverageDenominatorMissing must be caught here too, not
            # inferred from a call site three blocks up.
            print("insight dash: metric catalog failed to load: %s" % e, file=sys.stderr)
            return 1
        finally:
            manager_conn.close()
        assert_self_contained(manager_html)  # belt-and-suspenders, mirrors index.html/ic.html above
        manager_path = out_dir / "manager.html"
        manager_path.write_text(manager_html, encoding="utf-8", errors="replace")
        print("insight dash: wrote %s (manager view)" % manager_path)

        # Delivery panel (panel.html): the designed, information-dense entry point, and the file
        # `--serve` opens by default. Built alongside the persona views rather than replacing
        # them -- those carry per-persona metric selections this page does not reproduce.
        #
        # Deliberately NOT wrapped in the MetricLoadError/CoverageDenominatorMissing guard the
        # four views above share. Those views load the metric catalog and refuse to render without
        # it; the panel reads the store defensively and renders a metric's ABSENCE as its normal
        # output, so there is no catalog-load failure for it to catch. A panel that could not
        # render when metrics are missing would be unable to report the one thing it exists to
        # report -- how much of the instrumentation is actually live.
        from insight.dash.panel import render_panel

        panel_conn = open_store(args.db)
        try:
            panel_html = render_panel(panel_conn, db_label=str(db_path))
        finally:
            panel_conn.close()
        assert_self_contained(panel_html)
        panel_path = out_dir / "panel.html"
        panel_path.write_text(panel_html, encoding="utf-8", errors="replace")
        print("insight dash: wrote %s (delivery panel)" % panel_path)

        # Leadership persona view (issue #131, E5.S1): a FOURTH, team-wide file,
        # leadership.html, built from yet another fresh connection (manager_conn above is
        # already closed). UNCONDITIONAL, like manager.html: no per-viewer identity to resolve,
        # never touches resolve_actor (Decision 5 of .sdlc/plans/131.md).
        from insight.dash.leadership import render_leadership_view

        leadership_conn = open_store(args.db)
        try:
            leadership_html, _leadership_summary = render_leadership_view(leadership_conn)
        except (MetricLoadError, CoverageDenominatorMissing) as e:
            print("insight dash: metric catalog failed to load: %s" % e, file=sys.stderr)
            return 1
        finally:
            leadership_conn.close()
        assert_self_contained(leadership_html)
        leadership_path = out_dir / "leadership.html"
        leadership_path.write_text(leadership_html, encoding="utf-8", errors="replace")
        print("insight dash: wrote %s (leadership view)" % leadership_path)

        # Cross-functional persona view (issue #133, E5.S3): a FIFTH, team-wide file,
        # cross-functional.html, built from yet another fresh connection (leadership_conn above
        # is already closed). UNCONDITIONAL, like manager.html/leadership.html: no per-viewer
        # identity to resolve, never touches resolve_actor (Decision 2 of .sdlc/plans/133.md,
        # zero-exception privacy posture, matching leadership.py).
        from insight.dash.cross_functional import render_cross_functional_view

        cross_functional_conn = open_store(args.db)
        try:
            cross_functional_html, _cross_functional_summary = render_cross_functional_view(
                cross_functional_conn
            )
        except (MetricLoadError, CoverageDenominatorMissing) as e:
            print("insight dash: metric catalog failed to load: %s" % e, file=sys.stderr)
            return 1
        finally:
            cross_functional_conn.close()
        assert_self_contained(cross_functional_html)
        cross_functional_path = out_dir / "cross-functional.html"
        cross_functional_path.write_text(cross_functional_html, encoding="utf-8", errors="replace")
        print("insight dash: wrote %s (cross-functional view)" % cross_functional_path)

        if args.serve:
            try:
                serve_forever_until_interrupted(out_dir, port=args.port)
            except KeyboardInterrupt:
                pass
        return 0
    if args.command == "users" and args.action == "add":
        # Lazy, mirroring ingest/gaps/dash: keeps argon2-cffi (and insight.accounts.store /
        # insight.accounts.hashing) out of the import graph for --help and every other
        # subcommand. See the module docstring and the AST guard in test_cli.py.
        import getpass

        from insight.accounts import hashing, store

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("insight users add: passwords do not match", file=sys.stderr)
            return 1

        try:
            store.add_user(args.username, password, args.role)
        except store.UsernameExistsError as e:
            print("insight users add: %s" % e, file=sys.stderr)
            return 1
        except ValueError as e:
            print("insight users add: %s" % e, file=sys.stderr)
            return 1
        except store.AccountsStoreCorruptError as e:
            print("insight users add: %s" % e, file=sys.stderr)
            return 1
        except hashing.KDFUnavailableError as e:
            print("insight users add: %s" % e, file=sys.stderr)
            return 1
        except store.AccountsLockUnavailableError as e:
            # issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 (code review finding 2). Never
            # caught before this fix, so it escaped as a raw traceback instead of this module's
            # established clean-CLI-error convention -- every OTHER account-store error above gets
            # a one-line stderr message and a non-zero exit, not a stack trace. `add_user` fires
            # this on every platform lacking `fcntl` (Windows); this repo's own CI/dev are both
            # POSIX, so it never fires in practice today.
            print("insight users add: %s" % e, file=sys.stderr)
            return 1

        print("insight users add: created account %r (role: %s)" % (args.username, args.role))
        return 0
    if args.command == "users" and args.action == "verify":
        # Lazy import, same reasoning as the `add` branch just above: keeps argon2-cffi (and
        # insight.accounts.store/hashing) out of the import graph for --help and every other
        # subcommand. issue #307 [E18.S2], .sdlc/plans/307.md Decision 1.
        import json as _json
        from insight.accounts import hashing, store

        try:
            payload = _json.load(sys.stdin)
            username = payload["username"]
            password = payload["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("username and password must both be strings")
        except Exception as e:
            print("insight users verify: malformed request on stdin: %s" % e, file=sys.stderr)
            return 4

        try:
            role = store.verify_user(username, password, accounts_path=args.accounts_path)
        except store.InvalidCredentials:
            # The stdout marker is load-bearing, not decoration (independent security review of
            # #307). The Node bridge must not read a BARE exit 1 as "wrong password": CPython
            # exits 1 for any uncaught exception -- e.g. the ModuleNotFoundError from launching
            # this with the wrong CWD -- and the "not implemented yet" fallthrough below returns 1
            # too. Without a positive verdict on stdout, every one of those would answer "invalid
            # username or password" to users typing the CORRECT password, hiding an outage behind
            # a credentials message. Still ONE message for wrong-password/unknown-user/corrupt-
            # record, so this leaks no oracle (store.InvalidCredentials' own design).
            print(_json.dumps({"error": "invalid_credentials"}))
            print("insight users verify: invalid username or password", file=sys.stderr)
            return 1
        except hashing.KDFUnavailableError as e:
            print("insight users verify: %s" % e, file=sys.stderr)
            return 2
        except store.AccountsStoreCorruptError as e:
            print("insight users verify: %s" % e, file=sys.stderr)
            return 3
        except store.AccountsLockUnavailableError as e:
            # issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 (both independent code/security
            # reviews of #308 found this). Never caught before this fix, so it escaped as a raw
            # traceback instead of this branch's established per-cause exit-code convention
            # (1/2/3/4 above). Deliberately REUSES exit 2 -- the SAME code `hashing.
            # KDFUnavailableError` returns just above -- rather than inventing a fresh one. A lock
            # that cannot be acquired is exactly "the credential check could not run," the same
            # class of operator/infra failure as "the KDF is unavailable," never "invalid
            # credentials": on the Node side, pythonBridge.ts's `switch` already maps exit 2 to
            # `CredentialCheckUnavailableError` (checked on both sides before choosing this, per
            # the review), so reusing it needs zero Node-side changes and stays inside the
            # existing Python/Node exit-code contract `insight/web/scripts/
            # prove-python-bridge-exit-codes.mjs` pins -- an earlier version of this fix invented
            # a new exit code (5) instead, which that same contract argues against: every code
            # this CLI returns is meant to be one of a small, closed, Node-mapped set, not grown
            # ad hoc per new Python-side exception type.
            print("insight users verify: %s" % e, file=sys.stderr)
            return 2

        print(_json.dumps({"role": role}))
        return 0
    issue = _TRACKING_ISSUE[args.command]
    print(
        "insight %s: not implemented yet - see issue #%d" % (args.command, issue),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
