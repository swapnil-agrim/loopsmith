"""Model auto-selection predictor (sdlc-model/predict.py): a deterministic goal->tier heuristic.
Pins each tier, the upward conflict-resolution rule, and the default so a wording change that
silently down-tiers hard work fails here."""
import pathlib, importlib.util

P = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-model" / "scripts" / "predict.py"


def _mod():
    spec = importlib.util.spec_from_file_location("predict", P)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_hard_goals_get_opus():
    p = _mod().predict
    for g in ("migrate the database schema", "redesign the auth architecture",
              "fix the race condition in the scheduler", "add payment processing"):
        assert p(g) == "opus", g


def test_trivial_goals_get_haiku():
    p = _mod().predict
    for g in ("fix a typo in the README", "rename the helper for clarity",
              "reformat the config", "remove dead code"):
        assert p(g) == "haiku", g


def test_creative_goals_get_fable():
    p = _mod().predict
    for g in ("draft the product vision", "write the launch blog narrative",
              "write the storytelling for the launch"):
        assert p(g) == "fable", g


def test_ordinary_code_defaults_to_sonnet():
    p = _mod().predict
    for g in ("add a retry to the http client", "wire the new CLI flag", ""):
        assert p(g) == "sonnet", g


def test_conflict_resolves_upward():
    # a trivial word next to a hard one must NOT down-tier: hard wins.
    assert _mod().predict("fix the typo in the security module") == "opus"


def test_no_false_trigger_on_substrings():
    p = _mod().predict
    assert p("update the revision history") == "sonnet"   # 'vision'/'story' are substrings — must not fire
    assert p("improve the provision logic") == "sonnet"


def test_agile_story_phrasing_is_not_fable():
    # bare 'story' is agile jargon (user story / story point / 'Story:' label), not creative
    # writing — must not route to the fable (creative) tier. Genuine storytelling still does,
    # via the 'storytell' pattern (see test_creative_goals_get_fable).
    p = _mod().predict
    for g in ("Story: add pagination", "Implement the user story for checkout",
              "Add a story point field"):
        assert p(g) != "fable", g


def test_main_reads_text_and_file(tmp_path):
    m = _mod()
    assert m.main(["predict.py", "migrate the tables"]) == 0
    f = tmp_path / "goal.md"; f.write_text("fix a typo")
    assert m.main(["predict.py", str(f)]) == 0
    assert m.main(["predict.py"]) == 2   # usage


def _sdlc(tmp_path, model_selection):
    import json
    base = tmp_path / ".sdlc"; base.mkdir(exist_ok=True)   # same tmp_path reused across calls in a test
    (base / "config.json").write_text(json.dumps({"model_selection": model_selection}))
    return str(base)


def test_resolve_gates_on_config(tmp_path):
    """resolve() returns a tier only under model_selection:auto — the loop stays unchanged when off."""
    m = _mod()
    assert m.resolve("migrate the schema", _sdlc(tmp_path, "auto")) == "opus"
    assert m.resolve("migrate the schema", _sdlc(tmp_path, "off")) is None
    assert m.resolve("migrate the schema", str(tmp_path / "missing")) is None   # no config → off


def test_resolve_cli_prints_off_when_disabled(tmp_path, capsys):
    m = _mod()
    m.main(["predict.py", "resolve", "add a retry", _sdlc(tmp_path, "off")])
    assert capsys.readouterr().out.strip() == "off"
    m.main(["predict.py", "resolve", "migrate the db", _sdlc(tmp_path, "auto")])
    assert capsys.readouterr().out.strip() == "opus"


def test_orchestrators_wire_model_selection():
    """The loop must call the resolver + dispatch a subagent; goal must surface it — so a refactor
    can't silently drop the wiring."""
    sk = pathlib.Path(__file__).resolve().parent.parent / "skills"
    loop = (sk / "sdlc-loop" / "SKILL.md").read_text()
    goal = (sk / "sdlc-goal" / "SKILL.md").read_text()
    assert "predict.py" in loop and "resolve" in loop and "subagent" in loop
    assert "predict.py" in goal and "model_selection" in goal


# --- effort axis + per-step resolution (0.6) ---

def test_effort_axis_low_medium_high():
    m = _mod()
    assert m.predict_effort("run the tests for the slice") == "low"
    assert m.predict_effort("watch the job status and poll") == "low"
    assert m.predict_effort("debug the race condition in the scheduler") == "high"
    assert m.predict_effort("add a retry to the http client") == "medium"


def test_resolve_step_gated_by_config(tmp_path):
    import json
    m = _mod()
    base = tmp_path / ".sdlc"; base.mkdir()
    base.joinpath("config.json").write_text(json.dumps({"model_selection": "off"}))
    assert m.resolve_step("run the tests", str(base)) is None          # off → None
    base.joinpath("config.json").write_text(json.dumps({"model_selection": "auto"}))
    pair = m.resolve_step("run the tests", str(base))
    assert pair == {"model": "sonnet", "effort": "low"}


def test_resolve_cli_output_stays_backward_compatible(tmp_path, capsys):
    import json
    m = _mod()
    base = tmp_path / ".sdlc"; base.mkdir()
    base.joinpath("config.json").write_text(json.dumps({"model_selection": "auto"}))
    assert m.main(["predict.py", "resolve", "fix a typo", str(base)]) == 0
    assert capsys.readouterr().out.strip() == "haiku"                  # bare tier, no pair
    assert m.main(["predict.py", "resolve-step", "fix a typo", str(base)]) == 0
    assert capsys.readouterr().out.strip() == "model=haiku effort=low"


# --- #543: security routing across the in- prefix, on BOTH axes ---------------------------------
# `\b(...|secure|...)` cannot match inside "insecure": `n` and `s` are both word characters, so
# there is no boundary between them. The most common way a security goal is actually phrased —
# naming the DEFECT rather than the property — therefore missed the opus tier entirely. Separately,
# `_EFFORT_PATTERNS` carried `securit` but not `secure`, so even a goal that DID reach opus could
# come back at medium effort: an internal inconsistency between the two lists, independent of the
# boundary question. Both directions contradict the module's own stated bias that over-powering is
# cheaper than under-powering.


def test_insecure_phrasing_routes_like_security_phrasing():
    p = _mod().predict
    for g in ("Fix the insecure default in the token store",
              "Fix the insecurity in the session handler",
              "Harden the security of the token store",
              "Make the token store secure by default"):
        assert p(g) == "opus", g


def test_security_phrasings_all_get_high_effort():
    """The two lists have to agree: a goal that reaches opus on the model axis must not come back
    at medium on the effort axis just because it said "secure" instead of "security"."""
    effort = _mod().predict_effort
    for g in ("Fix the insecure default in the token store",
              "Fix the insecurity in the session handler",
              "Harden the security of the token store",
              "Make the token store secure by default"):
        assert effort(g) == "high", g


def test_the_in_prefix_widening_stays_anchored_at_a_word_boundary():
    """Widening to `(in)?secur` admits exactly ONE prefix, not any prefix: "resecuring" still has no
    word boundary before its "secur", so it must not fire. Same discipline the revision/provision
    pin above enforces for the fable tier — asserted on BOTH axes, since both lists changed."""
    m = _mod()
    for g in ("schedule the resecuring of the vault", "update the revision history",
              "improve the provision logic"):
        assert m.predict(g) == "sonnet", g
        assert m.predict_effort(g) == "medium", g
