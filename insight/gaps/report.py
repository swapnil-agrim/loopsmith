# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap findings report: run every loaded rule once, and hand back plain data
insight/gaps/compare.py can diff run-over-run (issue #122, [E3.S7] -- this story also bundles the
BASE gaps runner; no prior E3 story ever wired a live `gaps` CLI subcommand -- see
.sdlc/plans/122.md's own SCOPE DECISION).

No `import duckdb` -- `conn` is passed in already open, the same convention insight.gaps.evaluate/
insight.metrics.testing/insight.metrics.loader already establish.

ONE RULE CRASHING NEVER ABORTS THE RUN (.sdlc/plans/122.md Design decision 3). Confirmed live,
this story's own research: insight/gaps/consistency_files_outside_plan.sql's own guardrail
discloses that syntactically invalid raw_payload JSON still raises DuckDB's own
InvalidInputException through every rule's population/evidence query, unguarded by TRY_CAST (the
exception is raised by json_extract/json_type themselves, before any CAST ever runs) -- a known,
disclosed, catalog-wide limitation this story does not fix. build_report's own per-rule
try/except is what keeps that limitation from also taking down every OTHER rule's visibility in
the same run -- the same "one bad input must not deny visibility into everything else" posture
insight.__main__'s own `ingest` branch already established (its own per-repo try/except,
insight/__main__.py:187-193)."""
import datetime

from insight.gaps.evaluate import evaluate_rule
from insight.gaps.loader import load_gap_rules
from insight.gaps.severity import SEVERITY_ORDER

SCHEMA = "insight-gaps-report/v1"

#: Same four characters as skills/sdlc-loop/scripts/pipeline.py's own render() icon map.
_ICON = {"PASS": "+", "WARN": "!", "FAIL": "x", "ABSENT": "·"}


def json_default(value):
    """json.dumps(..., default=json_default): the only non-JSON-native type an evidence row
    carries is a DuckDB TIMESTAMP surfacing as a native datetime.datetime/date through the
    DB-API (proven live, insight/tests/test_gap_rule_consistency_files_outside_plan.py's own
    `collected_ts` assertion; re-verified this session, .sdlc/plans/122.md Design decision 5).
    Anything else re-raises TypeError rather than being silently coerced."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable: {value!r}")


def build_report(conn, rules_dir=None):
    """Evaluate every loaded gap rule once against `conn`, in load_gap_rules' own (sorted-by-
    filename) order. `findings` carries EVERY rule's finding -- PASS and ABSENT included, never
    only the failing ones (Design decision 1): --compare needs the full prior state to detect a
    PASS -> WARN regression, not only a WARN -> FAIL one. A rule that raises is recorded in
    `errors` instead and never aborts the loop (Design decision 3)."""
    registry = load_gap_rules(rules_dir)
    findings = []
    errors = []
    for rule_id in registry:                       # load_gap_rules' own dict is already sorted
        rule = registry[rule_id]
        try:
            finding = evaluate_rule(conn, rule)
        except Exception as e:                       # never fatal -- see module docstring
            errors.append({"rule_id": rule_id, "error": f"{type(e).__name__}: {e}"})
            continue
        findings.append(dict(finding, rule_id=rule_id))
    return {"schema": SCHEMA, "findings": findings, "errors": errors,
            "verdict": _verdict(findings, errors)}


def _verdict(findings, errors):
    """Mirrors pipeline.py's own _verdict() two-tier split (Design decision 4): `overall` is the
    worst severity among SUCCESSFUL findings only; `clean` additionally requires NO finding to be
    ABSENT anywhere (checked independently of `worst`, matching pipeline.py's own `blocked`
    list -- an ABSENT lane blocks `clean` even when something WORSE elsewhere already makes
    `worst` a WARN/FAIL, since `worst` alone can't tell you an ABSENT exists underneath it) and no
    rule errors; `failing`/`errored` are the two booleans the CLI's own exit code is computed
    from."""
    worst = "PASS"
    has_absent = False
    for f in findings:
        if f["severity"] == "ABSENT":
            has_absent = True
        if SEVERITY_ORDER[f["severity"]] > SEVERITY_ORDER[worst]:
            worst = f["severity"]
    return {"overall": worst, "clean": worst != "FAIL" and not has_absent and not errors,
            "failing": worst == "FAIL", "errored": bool(errors)}


def render_report(report, delta=None):
    """Human-facing text. 'Never a bare red number... what, evidence rows, metric, action'
    (design spec, Presentation section). PASS/ABSENT rules render one compact line each (their
    own evidence is guaranteed empty by evaluate.py's own invariant); WARN/FAIL rules render every
    evidence row plus the rule's own action. Errors print unconditionally, even under --compare,
    so a crashed rule is never invisible in the rendered output. Under --compare, the three
    error-related buckets (`errored_in_current`, `still_erroring`, `recovered_from_error`) EACH
    get their own distinguishable delta-scoped line -- distinct from the unconditional error
    section above them, which only says "this rule crashed"; these lines additionally say what
    the rule was doing before the crash, whether it was ALREADY crashing last run too, or that it
    just came back, none of which exist without a prior report to compare against (Design
    decision 8, fixed across two plan-review rounds: the machine-readable delta carries all three
    signals too, not only this text)."""
    lines = [f"# Gap findings report -- {report['schema']}",
             "gating: none -- diagnostic report; never gates the run it examines", ""]
    for f in report["findings"]:
        icon = _ICON[f["severity"]]
        lines.append(f"{icon} [{f['class']}] {f['rule_id']}: {f['severity']} (metric {f['metric']})")
        if f["severity"] in ("WARN", "FAIL"):
            for row in f["evidence"]:
                lines.append(f"    evidence: {row}")
            lines.append(f"    action: {f['action']}")
    if report["errors"]:
        lines.append("")
        for e in report["errors"]:
            lines.append(f"! {e['rule_id']} ERRORED: {e['error']}")
    v = report["verdict"]
    lines.append("")
    lines.append(f"verdict: {v['overall']}" + (" (rule error(s) present)" if v["errored"] else ""))
    if delta is not None:
        lines.append(f"delta: regressed={len(delta['regressed'])} "
                     f"improved={len(delta['improved'])} "
                     f"still-failing (recurrence)={delta['recurrence_count']}")
        for row in delta["regressed"]:
            lines.append(f"  REGRESSED {row['rule_id']}: {row['before']} -> {row['now']}")
        for row in delta["still_failing"]:
            lines.append(f"  STILL FAILING {row['rule_id']} -- recurrence signal: route to the "
                         "backlog (pipeline.py's own `propose` contract; this command never "
                         "writes a goal file itself)")
        for row in delta["errored_in_current"]:
            lines.append(f"  ERRORED IN CURRENT RUN {row['rule_id']} (was {row['before']} in the "
                         "prior run) -- visibility lost, not a confirmed still-failing")
        for row in delta["still_erroring"]:
            lines.append(f"  STILL ERRORING {row['rule_id']} (errored in the prior run too) -- "
                         "visibility lost multiple runs running, own severity unknown")
        for row in delta["recovered_from_error"]:
            lines.append(f"  RECOVERED FROM ERROR {row['rule_id']} -> now {row['now']} "
                         "(was invisible in the prior run)")
    return "\n".join(lines)
