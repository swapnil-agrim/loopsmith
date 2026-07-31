# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Layer 2 of the collector adapter (issue #100, E1.S2): schema-keyed registry + persistence
into fact_collector_pack. See .sdlc/plans/100.md §A/§E for why this is split from collectors.py
and how an unrecognised schema is handled. No `import duckdb` here — conn is passed in already
open (insight/ingest/store.py owns the schema and the duckdb import)."""
import hashlib
import json

from insight.ingest import collectors

#: Lands in fact_collector_pack.degraded_adapter when payload carries a schema string with no
#: registered normaliser below. Never raised — see ingest_collectors.
ADAPTER_UNKNOWN_SCHEMA = "adapter_unknown_schema"
#: The catch-all: something in this adapter itself broke on one source. Distinct from every
#: other code, which describes the COLLECTOR's behaviour — this one accuses our own code, so a
#: reader can tell "the collector misbehaved" from "we have a bug".
ADAPTER_INTERNAL_ERROR = "adapter_internal_error"

_EMPTY_WINDOW = {
    "window_since_days": None, "window_oldest_sha": None, "window_oldest_date": None,
    "window_newest_sha": None, "window_newest_date": None, "window_commit_count": None,
}


def _normalize_alignment_collect(payload):
    window = payload.get("window") or {}
    oldest = window.get("oldest") or {}
    newest = window.get("newest") or {}
    return {
        "window_since_days": window.get("since_days"),
        "window_oldest_sha": oldest.get("sha") or None,
        "window_oldest_date": oldest.get("date") or None,
        "window_newest_sha": newest.get("sha") or None,
        "window_newest_date": newest.get("date") or None,
        "window_commit_count": window.get("commit_count"),
        "degraded_collector": list(payload.get("degraded") or []),
    }


def _normalize_windowless(payload):
    """discovery-scan/v1 and pipeline-card/v1: neither has a window or degraded[] concept —
    verified by reading both sources (discovery-scan.sh's only two emit points, and
    build_card()'s return keys). Always NULL window, always empty degraded_collector."""
    out = dict(_EMPTY_WINDOW)
    out["degraded_collector"] = []
    return out


_NORMALIZERS = {
    "alignment-collect/v1": _normalize_alignment_collect,
    "discovery-scan/v1": _normalize_windowless,
    "pipeline-card/v1": _normalize_windowless,
}


def normalize(schema, payload):
    """(fields_dict, extra_adapter_codes) for one already-run result. payload may be None (the
    collector never produced JSON — see collectors.run_source); an unrecognised schema is
    recorded, never raised. fields_dict always has all six window_* keys plus
    'degraded_collector'."""
    if payload is None:
        return dict(_EMPTY_WINDOW, degraded_collector=[]), []
    fn = _NORMALIZERS.get(schema)
    if fn is None:
        return dict(_EMPTY_WINDOW, degraded_collector=[]), [ADAPTER_UNKNOWN_SCHEMA]
    return fn(payload), []


_INSERT_SQL = """
    INSERT INTO fact_collector_pack
      (project_id, schema, collected_ts, window_since_days, window_oldest_sha,
       window_oldest_date, window_newest_sha, window_newest_date, window_commit_count,
       degraded_collector, degraded_adapter, raw_payload)
    VALUES (?, ?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def write_pack(conn, project_id, schema, fields, degraded_adapter, raw_payload):
    """One INSERT, one row. degraded_collector/degraded_adapter are always explicit lists —
    never None — so a later query never sees NULL vs [] ambiguity."""
    conn.execute(_INSERT_SQL, [
        project_id, schema,
        fields["window_since_days"], fields["window_oldest_sha"], fields["window_oldest_date"],
        fields["window_newest_sha"], fields["window_newest_date"], fields["window_commit_count"],
        list(fields["degraded_collector"]), list(degraded_adapter), raw_payload,
    ])


def _project_id_for(project_root):
    """Placeholder identity: sha256 of the resolved absolute path, truncated to 16 hex chars.
    No story populates dim_project yet (.sdlc/plans/99.md: "this table is never populated by
    this story anyway") so there is no established project_id scheme to match. A later
    dim_project-populating story must reconcile this with whatever scheme it picks — tracked,
    not solved, here (see .sdlc/plans/100.md §C, "what a later story needs to add")."""
    return hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]


def ingest_collectors(conn, project_root, collectors_root=None):
    """Run every collectors.SOURCES entry against project_root, normalise, and persist ONE
    fact_collector_pack row per source — always, even on total failure. Never raises. Returns a
    list of {'name', 'schema', 'degraded_collector', 'degraded_adapter'} for CLI printing.
    Repeated calls APPEND rows (this is a fact/log table, not upserted) — see
    test_ingest_collectors_appends_not_upserts_on_repeated_runs."""
    project_id = _project_id_for(project_root)
    resolved_root = collectors.resolve_collectors_root(collectors_root)
    results = []
    for source in collectors.SOURCES:
        # "Never fatal" is a promise about EVERY malformed thing a collector can emit, not
        # only the ones enumerated below — and a collector is a subprocess whose output this
        # code does not control. Without this guard one bad source aborts the loop and NO
        # source gets a row, which is strictly worse than the unknown-schema case the story
        # asks us to survive. Caught once, here, rather than re-guarded at every call site.
        try:
            run = collectors.run_source(source, project_root, resolved_root)
            fields, extra_adapter_codes = normalize(run["schema"], run["payload"])
            schema = run["schema"]
            degraded_adapter = list(run["degraded_adapter"]) + extra_adapter_codes
            raw_payload = json.dumps(run["payload"]) if run["payload"] is not None else None
        except Exception:
            schema = source.expected_schema
            fields = dict(_EMPTY_WINDOW, degraded_collector=[])
            degraded_adapter = [ADAPTER_INTERNAL_ERROR]
            raw_payload = None
        write_pack(conn, project_id, schema, fields, degraded_adapter, raw_payload)
        results.append({
            "name": source.name, "schema": schema,
            "degraded_collector": fields["degraded_collector"],
            "degraded_adapter": degraded_adapter,
        })
    return results
