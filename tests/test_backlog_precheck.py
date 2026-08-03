"""The loop's pre-work cross-check hook (loop.py precheck, slice 0.9.22): opt-in, park-with-proof +
advance on a confident finding, annotate on a weak one, fail-open, off by default. Hermetic, $0."""
import json, pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class _FakeSource:
    def __init__(self): self.calls = []
    def park(self, goal, reason): self.calls.append(("park", goal, reason))
    def note(self, goal, text): self.calls.append(("note", goal, text))
    def complete(self, goal): self.calls.append(("complete", goal))
    def fail(self, goal, reason): self.calls.append(("fail", goal, reason))


_GOAL = "migrate the widget cache onto the acme storage backend"
_DUP = "move the widget cache to acme storage"


def _rec(n, title, state="open", closed_at=None):
    return {"number": n, "title": title, "body_excerpt": "", "labels": [], "state": state,
            "closed_at": closed_at, "updated_at": "2026-08-01T00:00:00Z", "content_hash": "x"}


def _sdlc(d, records, backlog_check, mirrored_at=999.0):
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github"}, "backlog_check": backlog_check,
         "budget": {"max_iterations": 100}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    (base / "state" / "board-mirror.ndjson").write_text("".join(json.dumps(r) + "\n" for r in records))
    (base / "state" / "board-mirror.meta.json").write_text(json.dumps({"mirrored_at": mirrored_at}))
    return str(base), json.loads((base / "config.json").read_text())


def test_precheck_off_when_disabled_touches_nothing():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d, [_rec(1, _GOAL), _rec(2, _DUP)], {"enabled": False})
        src = _FakeSource()
        assert lp.precheck(base, "1", cfg, src, now=1000.0) == "OFF"
        assert src.calls == []


def test_precheck_parks_a_confident_duplicate_with_proof_then_advances():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        # identical title -> cosine ~1.0 >= default park_threshold 0.80 -> confident -> park
        base, cfg = _sdlc(d, [_rec(1, _GOAL), _rec(2, _GOAL)], {"enabled": True})
        src = _FakeSource()
        result = lp.precheck(base, "1", cfg, src, now=1000.0)
        assert result.startswith("PARKED") and "duplicate of #2" in result
        assert any(c[0] == "park" and "duplicate of #2" in c[2] for c in src.calls)   # proof on the issue
        # _record ran: the run cursor advanced (the park counts as an iteration)
        assert _mod("state").load_cursor(base)["run_iteration"] == 1


def test_precheck_annotates_a_weak_match_and_proceeds():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        # a finding (dup_threshold 0.4) that is NOT confident (park_threshold unreachable) -> annotate
        base, cfg = _sdlc(d, [_rec(1, _GOAL), _rec(2, _GOAL)],
                          {"enabled": True, "dup_threshold": 0.4, "park_threshold": 1.01})
        src = _FakeSource()
        assert lp.precheck(base, "1", cfg, src, now=1000.0) == "PROCEED (advisory)"
        assert any(c[0] == "note" and "advisory" in c[2] for c in src.calls)
        assert not any(c[0] == "park" for c in src.calls)          # good work is NOT parked on a weak hit


def test_precheck_proceeds_cleanly_when_nothing_matches():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d, [_rec(1, "unique wombat telemetry pipeline"), _rec(2, _DUP)],
                          {"enabled": True})
        src = _FakeSource()
        assert lp.precheck(base, "1", cfg, src, now=1000.0) == "PROCEED"
        assert src.calls == []


def test_precheck_is_fail_open_when_the_action_raises():
    lp = _mod("loop")

    class _BoomSource(_FakeSource):
        def park(self, goal, reason):
            raise RuntimeError("gh exploded")

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d, [_rec(1, _GOAL), _rec(2, _GOAL)], {"enabled": True})
        # a park that raises must not crash the loop — precheck swallows it and proceeds
        assert lp.precheck(base, "1", cfg, _BoomSource(), now=1000.0) == "PROCEED"


def test_precheck_cli_in_process_local_mode(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "config.json").write_text(json.dumps(
            {"backlog_check": {"enabled": True}, "budget": {"max_iterations": 100}}))   # local-goals default
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (base / "state" / "review-queue.md").write_text("# Q\n")
        for name, title in (("0001.md", _GOAL), ("0002.md", _GOAL)):
            (base / "goals" / name).write_text(f"---\nid: {name[:-3]}\nstatus: pending\ntitle: {title}\n---\nx\n")
        goal = str(base / "goals" / "0001.md")
        assert lp.main(["loop.py", "precheck", str(base), goal]) == 0
        assert capsys.readouterr().out.startswith("PARKED")         # local park via LocalSource + review-queue
