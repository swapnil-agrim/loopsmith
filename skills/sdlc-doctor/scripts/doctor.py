#!/usr/bin/env python3
"""sdlc-doctor: a setup check-up. Audit only what THIS project's config makes relevant — github board
-> gh auth + project scope; KG enabled -> the builder; vision-first -> the north-star; always -> the
.sdlc layer — and report each check with the exact one-line fix. The command runner is injectable so
the logic is hermetically testable. Zero-dep."""
import sys, json, pathlib


def _real_run(args):
    import subprocess
    try:
        p = subprocess.run(args, capture_output=True, text=True)
        return (p.stdout + p.stderr) if p.returncode == 0 else ""
    except Exception:
        return ""


def _cfg(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        return {}


def _chk(name, ok, fix):
    return {"name": name, "ok": bool(ok), "fix": "" if ok else fix}


def check(sdlc_dir=".sdlc", run=None):
    """Return the setup checks relevant to this project's config; each is {name, ok, fix}."""
    run = run or _real_run
    base = pathlib.Path(sdlc_dir)
    cfg = _cfg(sdlc_dir)
    disc = cfg.get("discovery") or {}
    kg = cfg.get("knowledge_graph") or {}
    out = [_chk("project layer", (base / "config.json").exists(), "run /sdlc-init to scaffold .sdlc/")]

    if disc.get("source") == "github":
        auth = run(["gh", "auth", "status"])
        out.append(_chk("gh auth", bool(auth), "run: gh auth login"))
        if ((disc.get("github") or {}).get("project") or {}).get("enabled"):
            out.append(_chk("gh project scope", bool(auth) and "project" in auth,
                            "run: gh auth refresh -s project"))

    if kg.get("enabled") is True:
        builder = kg.get("builder", "graphify")
        ok = bool(run([builder, "--version"]))
        fix = "run: pip install graphifyy" if builder == "graphify" else f"install the '{builder}' graph builder"
        out.append(_chk(f"{builder} installed", ok, fix))

    ns = base / "context" / "north-star.md"
    if ns.exists():
        filled = "<the change you want" not in ns.read_text(encoding="utf-8")
        out.append(_chk("north-star filled", filled, "run /sdlc-vision to fill the tiers"))
    return out


def main(argv):
    if len(argv) >= 2 and argv[1] == "check":
        checks = check(argv[2] if len(argv) > 2 else ".sdlc")
        gaps = [c for c in checks if not c["ok"]]
        for c in checks:
            print(f"  [{'OK ' if c['ok'] else 'MISSING'}] {c['name']}" + ("" if c["ok"] else f"  ->  {c['fix']}"))
        print(f"\nsdlc-doctor: {len(checks) - len(gaps)}/{len(checks)} ready"
              + ("." if not gaps else f"; {len(gaps)} need the one-liner shown above."))
        return 0
    print("usage: doctor.py check [sdlc_dir]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
