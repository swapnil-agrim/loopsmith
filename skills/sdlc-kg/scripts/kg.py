#!/usr/bin/env python3
"""Knowledge-graph integration (optional, opt-in). LoopSmith captures external research + internal
analysis (+ optionally the code) into a corpus and hands it to an external graph builder (default:
the `graphify` CLI). This helper is the deterministic side — read config, locate the corpus, compute
the build plan per scope. The /sdlc-kg skill drives the builder itself. Zero-dep; the builder is a
soft dependency. Disabled by default."""
import sys, json, pathlib, hashlib, re

_DEFAULTS = {"enabled": False, "scope": "full", "builder": "graphify", "auto_refresh": False}


def load_config(sdlc_dir):
    """The knowledge_graph config block, merged over safe defaults. Missing/garbage config -> defaults
    (disabled), so a project that never opted in is never touched."""
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    kg = {**_DEFAULTS, **(cfg.get("knowledge_graph") or {})}
    kg["enabled"] = kg.get("enabled") is True       # strict: only explicit boolean true opts in
    return kg


def corpus_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "knowledge"


def _gaps_file(sdlc_dir):
    return corpus_dir(sdlc_dir) / "gaps.md"


def gap_list(sdlc_dir):
    """Open knowledge gaps - logged query-misses, in log order. [] if none."""
    f = _gaps_file(sdlc_dir)
    if not f.exists():
        return []
    return [ln[2:].strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def gap_log(sdlc_dir, question):
    """Record a query-miss so the graph tracks what it does NOT know - the backlog the loop can later
    fill. Exact (whitespace-normalized) duplicates are skipped so the gap log can't bloat into the
    very noise it exists to surface. Returns True if newly logged, False if empty or already known.
    ponytail: exact-match dedup + no close/resolve; the loop closes gaps in Phase C, add semantic
    dedup only if the list gets noisy."""
    question = " ".join(question.split())
    if not question or question in gap_list(sdlc_dir):
        return False
    f = _gaps_file(sdlc_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    head = "" if f.exists() else "# Knowledge gaps - logged query-misses (what the graph doesn't know yet)\n\n"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(head + f"- {question}\n")
    return True


def _count_md(d):
    return sum(1 for _ in d.rglob("*.md")) if d.exists() else 0


_MAINTAIN_THRESHOLD = 200       # research+analysis files before maintenance is flagged due
                                # ponytail: arbitrary; tune if it fires too early / too late


def _cited_paths(text):
    """Repo paths cited in backticks (must contain '/' + a file extension). ponytail: backtick
    heuristic only - URLs (with '://') are excluded by the charclass; bare filenames are skipped."""
    return set(re.findall(r"`([\w.\-/]+/[\w.\-]+\.\w+)`", text))


def maintain_report(sdlc_dir, repo_root="."):
    """Report-only maintenance audit of the knowledge corpus - proposes, never archives/deletes:
    - stale: analysis notes citing a repo path that no longer exists
    - dups:  groups of analysis notes with byte-identical content
    - counts / over_threshold: corpus-size signal
    ponytail: exact-content dedup + backtick-path citations; richer signals only if this proves noisy."""
    corpus = corpus_dir(sdlc_dir)
    analysis = corpus / "analysis"
    root = pathlib.Path(repo_root)
    notes = sorted(analysis.rglob("*.md")) if analysis.exists() else []
    stale, by_hash = [], {}
    for n in notes:
        text = n.read_text(encoding="utf-8")
        dead = sorted(p for p in _cited_paths(text) if not (root / p).exists())
        if dead:
            stale.append({"note": str(n.relative_to(corpus)), "missing": dead})
        by_hash.setdefault(hashlib.sha256(text.encode("utf-8")).hexdigest(), []).append(
            str(n.relative_to(corpus)))
    dups = sorted(sorted(v) for v in by_hash.values() if len(v) > 1)
    counts = {"research": _count_md(corpus / "research"), "analysis": len(notes),
              "gaps": len(gap_list(sdlc_dir))}
    return {"stale": stale, "dups": dups, "counts": counts,
            "over_threshold": counts["research"] + counts["analysis"] > _MAINTAIN_THRESHOLD}


def build_plan(sdlc_dir, repo_root="."):
    """The build plan for the configured scope, or None if KG is disabled.
    scope 'full' includes the code tree; 'research' (anything else) is corpus-only."""
    kg = load_config(sdlc_dir)
    if not kg.get("enabled"):
        return None
    include_code = kg.get("scope", "full") == "full"
    return {
        "scope": kg.get("scope", "full"),
        "builder": kg.get("builder", "graphify"),
        "corpus": str(corpus_dir(sdlc_dir)),
        "include_code": include_code,
        "code_root": str(pathlib.Path(repo_root).resolve()) if include_code else None,
        "auto_refresh": bool(kg.get("auto_refresh")),
    }


def status(sdlc_dir):
    kg = load_config(sdlc_dir)
    corpus = corpus_dir(sdlc_dir)
    return {
        **kg,
        "corpus": str(corpus),
        "research_files": _count_md(corpus / "research"),
        "analysis_files": _count_md(corpus / "analysis"),
        # the builder writes <builder>-out/ at the repo root (parent of .sdlc); graphify -> graphify-out
        "graph_built": (pathlib.Path(sdlc_dir).resolve().parent
                        / f"{kg.get('builder', 'graphify')}-out" / "graph.json").exists(),
    }


def main(argv):
    sdlc = argv[2] if len(argv) > 2 else ".sdlc"
    if len(argv) >= 2 and argv[1] == "status":
        s = status(sdlc)
        if not s["enabled"]:
            print("knowledge-graph: disabled (set knowledge_graph.enabled=true in .sdlc/config.json)")
            return 0
        print(f"knowledge-graph: ENABLED | scope={s['scope']} builder={s['builder']} "
              f"auto_refresh={s['auto_refresh']} | corpus={s['corpus']} "
              f"(research {s['research_files']}, analysis {s['analysis_files']}) | "
              f"graph: {'built' if s['graph_built'] else 'not built'}")
        return 0
    if len(argv) >= 2 and argv[1] == "plan":
        plan = build_plan(sdlc, argv[3] if len(argv) > 3 else ".")
        print("knowledge-graph: disabled — nothing to build." if plan is None
              else json.dumps(plan, indent=2))
        return 0
    if len(argv) >= 2 and argv[1] == "maintain":
        rep = maintain_report(argv[2] if len(argv) > 2 else ".sdlc",
                              argv[3] if len(argv) > 3 else ".")
        c = rep["counts"]
        print(f"knowledge-graph maintain (report-only): research {c['research']}, analysis "
              f"{c['analysis']}, gaps {c['gaps']}" + (" | OVER THRESHOLD" if rep["over_threshold"] else ""))
        for s in rep["stale"]:
            print(f"  stale: {s['note']} -> missing {', '.join(s['missing'])}")
        for grp in rep["dups"]:
            print("  duplicate: " + " == ".join(grp))
        if not rep["stale"] and not rep["dups"]:
            print("  clean: no stale or duplicate notes.")
        print("  (report-only: nothing archived/deleted; review + apply yourself - archive, don't delete.)")
        return 0
    if len(argv) >= 3 and argv[1] == "gap":
        sub = argv[2]
        if sub == "log" and len(argv) >= 4:
            gdir = argv[4] if len(argv) > 4 else ".sdlc"
            print(f"kg gap: logged - {argv[3]}" if gap_log(gdir, argv[3])
                  else f"kg gap: already known (skipped) - {argv[3]}")
            return 0
        if sub == "list":
            gaps = gap_list(argv[3] if len(argv) > 3 else ".sdlc")
            print("\n".join(f"- {g}" for g in gaps) if gaps else "kg gap: no gaps logged.")
            return 0
        print('usage: kg.py gap log "<question>" [sdlc_dir] | gap list [sdlc_dir]', file=sys.stderr)
        return 2
    print("usage: kg.py status <sdlc_dir> | plan <sdlc_dir> [repo_root] | "
          "maintain <sdlc_dir> [repo_root] | "
          'gap log "<question>" [sdlc_dir] | gap list [sdlc_dir]', file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
