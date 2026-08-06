#!/usr/bin/env python3
"""Model auto-selection — predict the model tier a goal deserves, so the loop runs its phases at a
tier matched to the work instead of one-size-fits-all. Deterministic regex over the goal text (like
the intent hook), so it's zero-dep, hermetically testable, and never drifts. Returns one of the Task
tool's model tiers: haiku | sonnet | opus | fable.

Predict ONCE from the goal; the loop then runs that goal's phases at the returned tier (the design:
"the rest of the steps will be executed with that model"). Conflicts resolve UPWARD to the more
capable tier — over-powering a mislabelled goal is cheaper than under-powering a hard one.

Two axes, one gate (`model_selection: "auto"`): the MODEL tier (which brain) and the reasoning
EFFORT (how hard it thinks: low|medium|high). Resolution is available at two granularities —
per GOAL (`resolve`, the ceiling for the goal's phases) and per STEP (`resolve-step`, so a
mechanical step inside a hard goal — run the tests, watch a job, lint — can run on a cheaper
tier/effort than the goal's ceiling). Step conflicts resolve upward within the step text.

ponytail: heuristic with a known ceiling. Upgrade path = swap `predict` for an LLM classifier behind
the same signature if the keyword rubric ever proves too coarse; the config flag + tests stay put."""
import re, sys, pathlib

# Ordered high -> low; first tier whose signal appears wins (so hard beats trivial on a mixed goal).
_PATTERNS = [
    ("opus",  r"\b(migrat|architect|redesign|securit|secure|authenticat|authoriz|crypto|concurren|"
              r"distribut|race condition|performance|breaking change|complex|scalab|scaling|"
              r"multi-service|data loss|backward compat|threat model|financial|payment)"),
    ("fable", r"\b(vision|narrativ|storytell|blog|marketing copy|prose|tagline|creative writing)"),
    ("haiku", r"\b(typo|renam|whitespace|reformat|formatting|lint|docstring|changelog|spelling|"
              r"indentation|dead code|comment)"),
]
_DEFAULT = "sonnet"

# Effort is its own axis: a hard-model goal can still contain low-effort steps. First match wins.
_EFFORT_PATTERNS = [
    ("high", r"\b(migrat|architect|redesign|securit|threat model|concurren|race condition|"
             r"distribut|breaking change|data loss|backward compat|debug|root.?cause|diagnos)"),
    ("low",  r"\b(typo|renam|whitespace|reformat|formatting|lint|docstring|changelog|spelling|"
             r"dead code|comment|run (the )?tests?|re-?run|watch|watcher|monitor|poll|status check)"),
]
_EFFORT_DEFAULT = "medium"


def predict_effort(goal_text):
    """Return the reasoning effort (low|medium|high) for a goal/step text. Deterministic."""
    t = (goal_text or "").lower()
    for tier, pat in _EFFORT_PATTERNS:
        if re.search(pat, t):
            return tier
    return _EFFORT_DEFAULT


def predict(goal_text):
    """Return the model tier (haiku|sonnet|opus|fable) for a goal from its text. Deterministic."""
    t = (goal_text or "").lower()
    for tier, pat in _PATTERNS:
        if re.search(pat, t):
            return tier
    return _DEFAULT


def resolve(goal_text, sdlc_dir=".sdlc"):
    """The tier to run this goal's phases at, honoring config. Returns a tier only when
    `model_selection` is 'auto'; otherwise None (→ run at the session model, unchanged)."""
    import json
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    if (cfg.get("model_selection") or "off") != "auto":
        return None
    return predict(goal_text)


def resolve_step(step_text, sdlc_dir=".sdlc"):
    """Per-STEP resolution: {model, effort} honoring the same config gate, else None.
    Lets a mechanical step inside a hard goal run cheaper than the goal's ceiling."""
    import json
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    if (cfg.get("model_selection") or "off") != "auto":
        return None
    return {"model": predict(step_text), "effort": predict_effort(step_text)}


def _read(arg):
    p = pathlib.Path(arg)
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else arg


def main(argv):
    # resolve: config-gated tier for the loop ("off" when model_selection isn't auto).
    # Output stays a bare tier for backward compatibility with existing callers.
    if len(argv) >= 3 and argv[1] == "resolve":
        print(resolve(_read(argv[2]), argv[3] if len(argv) > 3 else ".sdlc") or "off")
        return 0
    # resolve-step: the per-step pair — `model=<tier> effort=<low|medium|high>` or `off`.
    if len(argv) >= 3 and argv[1] == "resolve-step":
        pair = resolve_step(_read(argv[2]), argv[3] if len(argv) > 3 else ".sdlc")
        print(f"model={pair['model']} effort={pair['effort']}" if pair else "off")
        return 0
    if len(argv) < 2 or argv[1] in ("resolve", "resolve-step"):
        print("usage: predict.py '<goal>' | resolve '<goal>' [sdlc_dir] | "
              "resolve-step '<step>' [sdlc_dir]", file=sys.stderr)
        return 2
    print(predict(_read(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
