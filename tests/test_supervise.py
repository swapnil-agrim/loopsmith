"""Overnight supervisor: the exit classifier (pure) + the wrapper e2e with a fake
session command. No real sessions, no sleeping (scale=0), no network."""
import importlib.util, os, pathlib, subprocess, time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod():
    spec = importlib.util.spec_from_file_location("sc", S / "supervise_classify.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class _FixedRng:
    def randint(self, lo, hi):
        return lo


def test_done_when_loop_reports_its_own_stop():
    m = _mod()
    for tail in ("stopped: backlog-empty", "LOOP STOP: backlog-empty\n3 done, 1 parked",
                 "Backlog is empty.", "DONE"):
        assert m.classify(tail, rng=_FixedRng())[0] == "done", tail


def test_stop_report_alone_is_NOT_done_budget_wins():
    # THE review-found bug: the "N done, M parked" report prints on EVERY stop —
    # a budget stop carrying it must classify relaunch, never done.
    m = _mod()
    action, _, _ = m.classify("LOOP STOP: budget\n0 done, 2 parked, 0 failed", rng=_FixedRng())
    assert action == "relaunch"
    action, _, _ = m.classify("2 done, 1 parked, 0 failed", rng=_FixedRng())
    assert action != "done"                     # report without a success marker = unknown


def test_budget_stop_relaunches_after_short_pause():
    m = _mod()
    # the true contract: `loop.py next` prints BUDGET on its own line
    action, secs, _ = m.classify("$ loop.py next .sdlc\nBUDGET\n0 done, 2 parked", rng=_FixedRng())
    assert action == "relaunch" and secs == 60


def test_limit_with_reset_time_sleeps_until_reset_plus_jitter():
    m = _mod()
    # now = 03:00 local; message says resets at 5:30 -> 2.5h + 120s jitter
    now = time.mktime((2026, 7, 24, 3, 0, 0, 0, 0, -1))
    action, secs, reason = m.classify(
        "You have hit your usage limit. Your limit resets at 5:30am.",
        now=now, rng=_FixedRng())
    assert action == "sleep"
    assert secs == int(2.5 * 3600) + 120
    assert "reset" in reason


def test_limit_reset_earlier_today_means_tomorrow():
    m = _mod()
    now = time.mktime((2026, 7, 24, 6, 0, 0, 0, 0, -1))   # 6:00; "resets at 5:30" = next day
    action, secs, _ = m.classify("usage limit reached — resets at 5:30", now=now, rng=_FixedRng())
    assert action == "sleep"
    assert secs == int(23.5 * 3600) + 120


def test_limit_without_time_backs_off_capped():
    m = _mod()
    waits = [m.classify("rate limit exceeded, try later", attempt=a, rng=_FixedRng())[1]
             for a in (0, 1, 2, 9)]
    assert waits == [1800, 3600, 3600, 3600]


def test_unknown_crash_escalates_capped():
    m = _mod()
    waits = [m.classify("Traceback (most recent call last): boom", attempt=a, rng=_FixedRng())[1]
             for a in (0, 1, 2, 3, 9)]
    assert waits == [300, 600, 1200, 3600, 3600]


def _run_supervisor(tmp_path, fake_script, max_runs="10"):
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    fake = tmp_path / "fake-claude.sh"
    fake.write_text("#!/usr/bin/env bash\n" + fake_script)
    fake.chmod(0o755)
    env = {**os.environ,
           "LOOPSMITH_CLAUDE_CMD": str(fake),
           "LOOPSMITH_SUPERVISE_MAX_RUNS": max_runs,
           "LOOPSMITH_SUPERVISE_SLEEP_SCALE": "0"}
    return subprocess.run(["bash", str(S / "supervise.sh"), str(base)],
                          capture_output=True, text=True, env=env, timeout=60), base


def test_wrapper_exits_zero_on_backlog_empty(tmp_path):
    proc, base = _run_supervisor(
        tmp_path, 'echo "run complete"; echo "LOOP STOP: backlog-empty"\n')
    assert proc.returncode == 0
    assert "action=done" in (base / "state" / "supervisor.log").read_text()


def test_wrapper_relaunches_through_limit_then_finishes(tmp_path):
    # 1st session: limit (no parseable time -> backoff, scaled to 0s); 2nd: done.
    script = (
        'N="$(cat "$STATE_DIR/n" 2>/dev/null || echo 0)"; N=$((N+1)); echo "$N" > "$STATE_DIR/n"\n'
        'if [ "$N" -lt 2 ]; then echo "usage limit reached, try again later"; else echo "LOOP STOP: backlog-empty"; fi\n')
    base_dir = tmp_path / ".sdlc"
    script = script.replace("$STATE_DIR", str(tmp_path))
    proc, base = _run_supervisor(tmp_path, script)
    assert proc.returncode == 0
    log = (base / "state" / "supervisor.log").read_text()
    assert "action=backoff" in log and "action=done" in log


def test_wrapper_stop_file_halts_cleanly(tmp_path):
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "state" / "supervisor.stop").write_text("")
    env = {**os.environ, "LOOPSMITH_CLAUDE_CMD": "false",
           "LOOPSMITH_SUPERVISE_SLEEP_SCALE": "0"}
    proc = subprocess.run(["bash", str(S / "supervise.sh"), str(base)],
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0 and "stop-file" in proc.stdout


def test_wrapper_max_runs_caps_a_crash_loop(tmp_path):
    proc, base = _run_supervisor(tmp_path, 'echo "segfault-ish nonsense"\n', max_runs="3")
    assert proc.returncode == 1
    assert "max runs (3) reached" in proc.stdout + (base / "state" / "supervisor.log").read_text()


# --------------------------------------------------------------------------- progress / blocked
# These cover the two endings the classifier could not name, both of which it charged the
# escalating CRASH ladder for. Measured live on 2026-08-04: a run that merged a PR and a run that
# stopped cleanly on denied permissions were both scored "unclassified exit", taking the ladder to
# 300s -> 600s -> 1200s -> 3600s. The loop then spends most of the night asleep BETWEEN SUCCESSFUL
# goals, which is the opposite of what the backoff is for.

def test_a_session_that_landed_a_goal_is_not_a_crash():
    """The common case, and the one that was costing hours: a session finishes a goal and exits
    with no stop marker at all. Progress is not success (the backlog may still be full), so this
    must NOT be `done` -- but it is emphatically not a crash either."""
    m = _mod()
    action, secs, _ = m.classify("1 done, 0 parked, 0 failed", rng=_FixedRng())
    assert action == "relaunch", "a session that landed a goal must not be charged crash backoff"
    assert secs <= 300
    # and it must not escalate with attempt, which is the whole point
    waits = [m.classify("2 done, 1 parked, 0 failed", attempt=a, rng=_FixedRng())[1]
             for a in (0, 3, 9)]
    assert len(set(waits)) == 1, f"progress must not escalate: {waits}"


def test_zero_done_is_not_reported_as_progress():
    """`0 done` must never be described as progress -- the report's mere PRESENCE is not evidence
    that anything landed, and the `> 0` guard is what enforces that.

    NOTE this test was narrowed when the fall-through default was inverted, and the narrowing is
    deliberate rather than a concession to make the code pass. It originally also asserted that
    0-done rides the escalating CRASH ladder. That assertion was wrong once "unknown" stopped
    meaning "crashed": a session reporting `0 done, 2 parked` ran correctly and parked two goals,
    which is a clean stop, not breakage. The hot-loop it was guarding against is now covered by
    two other mechanisms -- the supervisor's MAX_RUNS cap, and the fact that parked goals shed the
    `sdlc:goal` label, so a backlog that parks everything drains to backlog-empty and terminates
    the supervisor via `done`. What remains genuinely this test's job is the wording guard below,
    which still fails if the `> 0` check is removed."""
    m = _mod()
    action, _, reason = m.classify("0 done, 2 parked, 0 failed", rng=_FixedRng())
    assert "landed" not in reason and "progress" not in reason, \
        f"0-done must not be called progress, got: {reason}"
    assert action == "relaunch"   # a clean stop, no crash signature


def test_blocked_stop_relaunches_instead_of_escalating():
    """A `LOOP STOP: blocked` is a CLEAN stop -- the loop decided it could not proceed and said so.
    Charging it the crash ladder is wrong twice over: it is not a crash, and a fresh session
    frequently clears the cause (proven live -- run #1 stopped blocked-on-permissions, run #2
    merged a PR)."""
    m = _mod()
    for tail in ("LOOP STOP: blocked-on-permissions", "LOOP STOP: blocked", "loop stop: BLOCKED"):
        action, secs, _ = m.classify(tail, rng=_FixedRng())
        assert action == "relaunch", f"{tail!r} -> {action}"
        assert secs > 0, "a blocked stop still deserves a pause, not a hot retry"


def test_usage_limit_still_wins_over_progress():
    """Ordering guard. A session that lands a goal and THEN hits a usage limit must sleep until the
    stated reset -- relaunching immediately would burn the remaining runs against a closed door."""
    m = _mod()
    action, _, _ = m.classify("1 done, 0 parked, 0 failed\nyou've hit your usage limit; resets at 3pm",
                              rng=_FixedRng())
    assert action == "sleep", f"limit must outrank progress, got {action}"


def test_real_world_normal_session_tail(tmp_path):
    """The verbatim tail of the live run that merged PR #282 and was scored a crash for it."""
    m = _mod()
    tail = ("Polling in the background; I'll report when the gate clears or the checks settle.\n"
            "1 done, 0 parked, 0 failed")
    assert m.classify(tail, rng=_FixedRng())[0] == "relaunch"


def test_unrecognised_but_clean_exit_does_not_escalate():
    """The live case the first pass MISSED, and the reason the ladder was climbing.

    A headless `claude -p` session prints only its final message. The real tail of the run that
    merged PR #282 was a single sentence -- "Polling in the background; I'll report when the gate
    clears or the checks settle." -- with no stop marker and no "N done, M parked" report, because
    the session ended mid-goal while waiting on CI. Matching on progress text cannot catch that.

    So the DEFAULT is what has to change: an ending we cannot name is not evidence of a crash. Only
    a recognisable crash signature earns the escalating ladder; anything else that produced
    coherent output gets a flat, modest pause. MAX_RUNS remains the backstop against a
    pathological loop."""
    m = _mod()
    tail = "Polling in the background; I'll report when the gate clears or the checks settle."
    waits = [m.classify(tail, attempt=a, rng=_FixedRng()) for a in (0, 1, 2, 9)]
    assert all(w[0] == "relaunch" for w in waits), [w[0] for w in waits]
    assert len({w[1] for w in waits}) == 1, f"an unnamed clean exit must not escalate: {waits}"


def test_real_crash_signatures_still_escalate():
    """The counterweight. Inverting the default is only safe if genuine breakage is still caught,
    so each signature is pinned explicitly rather than trusting the fall-through."""
    m = _mod()
    for tail in ("Traceback (most recent call last): boom",
                 "Killed",
                 "fatal error: out of memory",
                 "zsh: command not found: claude",
                 "   \n  \n"):                      # empty output tells us nothing -> treat as crash
        waits = [m.classify(tail, attempt=a, rng=_FixedRng())[1] for a in (0, 1, 2, 3)]
        assert waits == [300, 600, 1200, 3600], f"{tail!r} should escalate, got {waits}"
