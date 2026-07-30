"""A feature nobody can find is a feature that doesn't ship.

`/sdlc-init` scaffolds `.sdlc/config.json` from a template, and that file is where an adopter looks
to learn what this kit can do — before the README, before any skill. So a knob the CODE reads must
appear there. When it doesn't, the feature is real, tested, documented in its skill, and invisible:
the user has to already know it exists to go looking.

That is how `gates.decision_gate` shipped — enforced by a hook, reported by /sdlc-doctor, described
in /sdlc-decide, and absent from the one file every adopter opens.

This is the same bug family as `lane: auto` (scaffolded, nothing read it) and the plan file
(`hard_plan_gate` gated on a path the Plan phase was never told to write). The halves are built by
different changes at different times and nothing checks they meet."""
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMPL = (ROOT / "skills" / "sdlc-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")

#: Every `.py` under skills/ + hooks/ that could read config. Widened deliberately: the point is to
#: catch a knob added anywhere, including in a script nobody thought of as config-reading.
_SOURCES = sorted(ROOT.glob("skills/*/scripts/*.py")) + sorted(ROOT.glob("hooks/*.py"))

#: `(cfg.get("gates") or {}).get("<name>")` — the codebase's one idiom for reading a gate.
_GATE_READ = re.compile(r'"gates"\)\s*(?:or\s*\{\})?\s*\)?\.get\(\s*"([a-z_]+)"')


def _gates_read_by_code():
    found = set()
    for path in _SOURCES:
        found.update(_GATE_READ.findall(path.read_text(encoding="utf-8")))
    return found


def test_the_idiom_still_matches_something():
    """If the codebase changes how it reads gates, this file silently passes forever. Fail loudly
    instead — an empty result means the guard stopped guarding, not that the repo got clean."""
    assert _gates_read_by_code(), "no gate reads matched; the detection idiom went stale"


def test_every_gate_the_code_reads_is_discoverable_in_the_scaffolded_config():
    """The check that would have caught decision_gate before it shipped invisible."""
    missing = sorted(g for g in _gates_read_by_code() if g not in TMPL)
    assert not missing, (
        "read by code but absent from the scaffolded config, so no adopter will ever find "
        f"them: {missing}. Add each to `gates` in config.json.tmpl — a default line if it takes "
        "one, or a `_<name>` comment explaining how it is switched on.")


def test_a_gate_with_no_enabled_flag_says_how_it_is_actually_turned_on():
    """decision_gate is the odd one out: authoring the registry is the opt-in, so there is no
    `enabled: true` to set. Left unexplained next to two gates that DO take that flag, the natural
    reading is 'add enabled: true' — which does nothing, and the user concludes the gate is broken."""
    assert "_decision_gate" in TMPL
    section = TMPL.split("_decision_gate")[1][:1200]
    assert "decisions.json" in section                 # what to author
    assert "/sdlc-decide" in section                   # the skill that authors it
    assert "AUTHORING" in section or "authoring" in section


def test_the_off_switch_is_documented_where_someone_would_look_for_it():
    """Turning a gate OFF is what you search for at 2am when it is blocking you. If that is only in
    the skill, the person being blocked is reading their config file instead."""
    section = TMPL.split("_decision_gate")[1][:1200]
    assert "enabled" in section and ("OFF" in section or "off" in section)


def test_template_is_valid_json_once_comments_are_stripped():
    """The `_name` comment convention means a stray quote breaks EVERY adopter's scaffold, and the
    added entry embeds escaped quotes — exactly where that goes wrong."""
    import json
    cfg = json.loads(TMPL)
    assert isinstance(cfg.get("gates"), dict)
    assert "decision_gate" in " ".join(cfg["gates"].keys())      # present as a key or a _comment
