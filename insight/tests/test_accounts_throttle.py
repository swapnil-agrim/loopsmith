# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the per-account throttle policy in insight.accounts.store.verify_user (issue #308
[E18.S3], .sdlc/plans/308.md Decisions 3/4/5/6, Tasks 2/3).

Kept in its own file, separate from test_accounts_store.py, matching that file's own convention
of grouping dense security reasoning together (see its module docstring) -- the throttle's
enumeration/timing-parity reasoning is dense enough to deserve its own space, and this file's own
existence make it easy to find every test protecting Decision 5/6 in one place.

GATING FOLLOWS test_accounts_store.py's OWN PER-FUNCTION RULE, not a module-level
`pytest.importorskip("argon2")` -- see that file's module docstring for why a module-level guard
would silently drop coverage on a machine that genuinely lacks argon2-cffi (this repo's own CI/
local dev, as of implementation), which is exactly the machine the always-on gate runs on. Most
tests below DO need argon2 (verify_user's every branch calls hashing.verify_password at least
once), so most are gated; the ones that are not are called out individually.
"""
import datetime
import json
import threading

import pytest

from insight.accounts import hashing, store


@pytest.fixture(autouse=True)
def fast_params(monkeypatch):
    """Every test in this file uses TEST_PARAMS -- this suite reruns on every future goal via
    verify.command and must not pay production-strength KDF cost repeatedly (Decision 8 of
    .sdlc/plans/306.md, unchanged by #308)."""
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)


def _read_record(accounts_path, username):
    return json.loads(accounts_path.read_text())["users"][username]


# =================================================================================== Decision 5: threshold, lockout window, no-extension, decay, reset-on-success


def test_exhausting_five_wrong_attempts_locks_the_account_even_against_the_correct_password(
    tmp_path,
):
    """The core exhaustion proof (done-when 3): 5 wrong-password attempts, then a 6th attempt
    WITH THE CORRECT PASSWORD, still raises InvalidCredentials -- proving the lockout is real,
    not just "still guessing wrong"."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    for _ in range(store._THROTTLE_THRESHOLD):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong-password", accounts_path=accounts_path)

    record = _read_record(accounts_path, "alice")
    assert record["failed_attempts"] == store._THROTTLE_THRESHOLD
    assert record["locked_until"] is not None

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)


def test_message_and_type_identical_across_wrong_password_unknown_user_corrupt_record_and_locked(
    tmp_path,
):
    """Extends done-when 3's message/type-indistinguishability proof to cover the FOURTH case
    #308 adds: a locked-out account being probed with its own objectively-correct password.
    `type(exc) is store.InvalidCredentials` and `exc.args` must be equal in all four."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "alice-password", "manager", accounts_path=accounts_path)
    store.add_user("bob", "bob-password", "manager", accounts_path=accounts_path)

    with pytest.raises(store.InvalidCredentials) as exc_wrong:
        store.verify_user("bob", "totally-wrong", accounts_path=accounts_path)

    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text())
    data["users"]["carol"] = {
        "password_hash": "not-a-well-formed-argon2-hash-at-all",
        "role": "manager",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    accounts_path.write_text(json.dumps(data))
    with pytest.raises(store.InvalidCredentials) as exc_corrupt:
        store.verify_user("carol", "whatever", accounts_path=accounts_path)

    for _ in range(store._THROTTLE_THRESHOLD):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_locked:
        store.verify_user("alice", "alice-password", accounts_path=accounts_path)

    exceptions = [exc_wrong.value, exc_unknown.value, exc_corrupt.value, exc_locked.value]
    for exc in exceptions:
        assert type(exc) is store.InvalidCredentials
        assert exc.args == (store._INVALID_CREDENTIALS_MESSAGE,)


def test_non_dict_record_branch_still_performs_the_throttle_write_like_every_sibling_branch(
    tmp_path, monkeypatch
):
    """Write-parity regression test (code review finding, issue #308 [E18.S3],
    .sdlc/plans/308.md Decision 5). Not reachable by username choice -- it needs pre-existing
    on-disk corruption (a hand-edited store mapping a username to a bare string instead of a
    record dict) -- but Decision 5 declares write-parity across EVERY verify_user failure branch
    load-bearing: if this one branch silently skipped its write while its siblings
    (unknown-user, locked-account, wrong-password) all write, an attacker who could somehow
    distinguish "wrote to disk" from "did not" (e.g. by observing mtime, or inode churn under
    heavy concurrent access) would gain exactly the enumeration signal PR #461 spent three review
    passes closing. A call-recording spy around `_write_accounts_data`, same pattern as this
    file's own Decision 6 parity test above, proves the write actually happens -- not merely that
    the exception raised is the expected InvalidCredentials, which passed even before this fix."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)
    data = json.loads(accounts_path.read_text())
    data["users"]["mallory"] = "not-a-record-dict-at-all"
    accounts_path.write_text(json.dumps(data))

    calls = []
    real_write_accounts_data = store._write_accounts_data

    def spy(path, data):
        calls.append(1)
        return real_write_accounts_data(path, data)

    monkeypatch.setattr(store, "_write_accounts_data", spy)

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("mallory", "whatever", accounts_path=accounts_path)

    assert calls == [1], (
        "verify_user's non-dict-record branch must perform exactly one _write_accounts_data "
        "call -- the same lock+read+write shape as the unknown-user and locked-account branches "
        "-- got %d call(s)" % len(calls)
    )


def test_lockout_expires_after_the_window_via_the_now_clock_seam_with_zero_real_sleeping(
    tmp_path, monkeypatch
):
    """After exhausting the threshold, monkeypatch `store._now` to return `locked_until + 1s`
    (never a real `time.sleep`) -- the next attempt, with the CORRECT password, must succeed and
    return the role."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    for _ in range(store._THROTTLE_THRESHOLD):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)

    locked_until = store._parse_iso(_read_record(accounts_path, "alice")["locked_until"])
    assert locked_until is not None

    future = locked_until + datetime.timedelta(seconds=1)
    monkeypatch.setattr(store, "_now", lambda: future)

    role = store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    assert role == "manager"

    record = _read_record(accounts_path, "alice")
    assert record["failed_attempts"] == 0
    assert record["locked_until"] is None


def test_successful_login_resets_failed_attempts_and_the_reset_does_not_carry_over(tmp_path):
    """2 wrong, then 1 correct: failed_attempts must be back to 0 (asserted via a direct read of
    the persisted JSON, not just behavior). A SUBSEQUENT 4 more wrong attempts must NOT yet lock
    -- proving the earlier 2 wrong attempts did not carry over past the reset."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    for _ in range(2):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)
    assert _read_record(accounts_path, "alice")["failed_attempts"] == 2

    role = store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    assert role == "manager"
    assert _read_record(accounts_path, "alice")["failed_attempts"] == 0

    for _ in range(4):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)
    record = _read_record(accounts_path, "alice")
    assert record["failed_attempts"] == 4
    assert record["locked_until"] is None, "4 attempts after a reset must not yet lock"


def test_no_extension_locked_until_is_byte_identical_after_one_more_attempt_while_locked(
    tmp_path,
):
    """Decision 5's 'no extension' bullet, proven precisely: while locked, one MORE wrong
    attempt must leave `locked_until` byte-identical to what it was -- not pushed further out.
    `failed_attempts` is still expected to increment (the write is NOT skipped -- Decision 5's
    own explicit warning against a read-only early return)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    for _ in range(store._THROTTLE_THRESHOLD):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)

    before = _read_record(accounts_path, "alice")
    locked_until_before = before["locked_until"]
    failed_attempts_before = before["failed_attempts"]
    assert locked_until_before is not None

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("alice", "wrong", accounts_path=accounts_path)

    after = _read_record(accounts_path, "alice")
    assert after["locked_until"] == locked_until_before, (
        "locked_until must not be extended by an attempt made while already locked"
    )
    assert after["failed_attempts"] == failed_attempts_before + 1, (
        "the write must still happen (failed_attempts still increments) -- a locked account "
        "must not become a read-only early return, see Decision 5's own explicit warning"
    )


# =================================================================================== Decision 6: enumeration/timing parity between an unknown user and a locked known user


def test_unknown_user_and_locked_known_user_both_call_verify_password_exactly_once_against_the_dummy_hash(
    tmp_path, monkeypatch
):
    """The direct proof of Decision 6's cost-shape parity requirement: a call-recording spy
    (same pattern as test_accounts_store.py's own
    test_corrupt_password_hash_still_calls_verify_password_...) shows an unknown-user failure and
    a locked-known-user failure BOTH call hashing.verify_password EXACTLY ONCE, and BOTH times
    against the dummy hash -- never the real hash. This is what makes the two indistinguishable
    by KDF cost/shape, not merely by exception message."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)
    for _ in range(store._THROTTLE_THRESHOLD):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user("alice", "wrong", accounts_path=accounts_path)
    assert _read_record(accounts_path, "alice")["locked_until"] is not None

    dummy = hashing.dummy_hash_for(hashing.TEST_PARAMS)
    calls = []
    real_verify_password = hashing.verify_password

    def spy(password, encoded_hash):
        calls.append(encoded_hash)
        return real_verify_password(password, encoded_hash)

    monkeypatch.setattr(hashing, "verify_password", spy)

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)
    assert calls == [dummy], "unknown-user branch must call verify_password exactly once, vs dummy"

    calls.clear()
    with pytest.raises(store.InvalidCredentials):
        # objectively CORRECT password, but the account is locked -- must still be exactly one
        # dummy-hash call, never a real-hash call.
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    assert calls == [dummy], "locked-known-user branch must call verify_password exactly once, vs dummy"


def test_unknown_user_never_creates_a_per_account_record_only_touches_the_shared_dummy_counter(
    tmp_path,
):
    """Decision 6.2: an unknown username must NEVER accumulate real per-account state. Repeated
    failures against unknown/never-existing usernames must not create ANY new entry under
    "users" -- only the shared, bounded, top-level `_dummy_throttle_attempts` sentinel may
    change."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    for name in ("mallory", "eve", "mallory", "trent", "eve"):
        with pytest.raises(store.InvalidCredentials):
            store.verify_user(name, "guess", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text())
    assert set(data["users"]) == {"alice"}, (
        "an unknown username must never gain its own per-account record -- got %r"
        % sorted(data["users"])
    )
    assert data[store._DUMMY_THROTTLE_KEY] == 5


def test_unknown_user_against_a_never_provisioned_store_still_only_touches_the_shared_counter(
    tmp_path,
):
    """Ungated -- no hashing successfully round-trips a real hash here, but verify_user still
    calls hashing.verify_password against the dummy hash on its unknown-user path, so this is
    gated after all (the accounts store does not exist yet at all, the most extreme "unknown
    user" case)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "does-not-exist-yet.json"
    with pytest.raises(store.InvalidCredentials):
        store.verify_user("alice", "whatever", accounts_path=accounts_path)
    data = json.loads(accounts_path.read_text())
    assert data["users"] == {}
    assert data[store._DUMMY_THROTTLE_KEY] == 1


# =================================================================================== Decision 4: the fcntl hard-fail applies to EVERY verify_user call, not just add_user


def test_verify_user_raises_loud_error_when_fcntl_unavailable_even_for_a_successful_login(
    tmp_path, monkeypatch
):
    """issue #308 [E18.S3], .sdlc/plans/308.md Decision 4's "blast radius stated honestly" note,
    proven directly: unlike add_user (a rarely-invoked CLI action), verify_user is the ordinary
    login path, and even a login with the CORRECT password must still fail loudly with
    AccountsLockUnavailableError when fcntl is unavailable -- never silently proceed unlocked."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    monkeypatch.setattr(store, "fcntl", None)
    with pytest.raises(store.AccountsLockUnavailableError):
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)

    # No throttle state was written -- the failure happened before the lock (and therefore
    # before any read/write) was ever acquired.
    monkeypatch.undo()
    record = _read_record(accounts_path, "alice")
    assert record.get("failed_attempts", 0) == 0
    assert record.get("locked_until") is None


# =================================================================================== PR #485 code-review finding: the wait for the store-global lock is BOUNDED, not indefinite


def test_verify_user_times_out_loudly_instead_of_blocking_forever_on_a_held_lock(
    tmp_path, monkeypatch
):
    """issue #308 [E18.S3], PR #485 code-review finding. The lock is store-GLOBAL and is held
    across the full argon2id KDF on every login attempt (Decision 6.2 requires the unknown-user
    branch to pay the same cost), so a flood of garbage login POSTs queues behind one critical
    section. An INDEFINITE block would turn that queue into unbounded process pileup -- every
    waiter is a live python3 spawned by pythonBridge.ts, pinned for the whole queue depth.

    Proven here by holding the lock in a background thread and asserting the waiter gives up
    with AccountsLockUnavailableError -- crucially NOT InvalidCredentials, so a contended login
    can never be mistaken for a wrong password (and __main__.py maps it to the "check could not
    run" exit code, never to an auth failure). The timeout is monkeypatched tiny so this asserts
    the mechanism in milliseconds rather than sleeping for the real 5s default."""
    pytest.importorskip("argon2")
    if store.fcntl is None:  # pragma: no cover - POSIX-only, like the module's other lock tests
        pytest.skip("fcntl is unavailable on this platform")

    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    monkeypatch.setattr(store, "_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(store, "_LOCK_POLL_SECONDS", 0.005)

    holding = threading.Event()
    release = threading.Event()

    def hold_the_lock():
        with store._locked_accounts(accounts_path):
            holding.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    try:
        assert holding.wait(timeout=10), "the holder thread never acquired the lock"
        # The CORRECT password, so this can only fail because of the lock -- never because the
        # credentials were wrong.
        with pytest.raises(store.AccountsLockUnavailableError):
            store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    finally:
        release.set()
        holder.join(timeout=10)

    # And once the lock is free again the very same call succeeds -- the timeout is a bounded
    # wait, not a latch that leaves the store permanently unusable.
    assert (
        store.verify_user("alice", "correct-password", accounts_path=accounts_path) == "manager"
    )


# =================================================================================== issue #308 Task 3: concurrency-race test for the throttle counter (Risk B, proven not just asserted)


def test_ten_concurrent_verify_subprocesses_against_one_account_lose_no_increments(
    tmp_path, monkeypatch
):
    """issue #308 [E18.S3], .sdlc/plans/308.md Task 3 -- the direct rebuttal of dossier Risk B,
    using genuinely separate OS processes (`python3 -m insight users verify`, real subprocesses,
    never threads sharing one interpreter/GIL), matching the real attack shape (pythonBridge.ts
    spawns a fresh python3 per login attempt).

    N=10 concurrent wrong-password attempts against ONE account, fired as close to simultaneously
    as `concurrent.futures.ThreadPoolExecutor.submit` allows (each thread blocks on its own
    `subprocess.run()`, so the OS schedules the N child processes for genuinely concurrent
    execution). Without `_locked_accounts` serializing each subprocess's read-modify-write, this
    is exactly `add_user`'s own pre-#308-documented "8 of 10 lost" failure mode, reproduced here
    against the throttle counter instead of a set of distinct usernames: the persisted
    `failed_attempts` afterward must equal EXACTLY N, never less.

    The account is created here with the REAL, unpatched production KDF params (undoing this
    file's own `fast_params` autouse fixture for just this test) -- the N child subprocesses
    always use the real, unpatched default (a monkeypatch never crosses a process boundary), so
    creating the account under TEST_PARAMS would make every child's verify hit a KDF-PARAMETER
    MISMATCH instead of a genuine wrong password, paying an extra real KDF call per attempt for
    no reason relevant to what this test proves."""
    pytest.importorskip("argon2")
    import concurrent.futures
    import pathlib
    import subprocess
    import sys

    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing._ORIGINAL_PRODUCTION_PARAMS)

    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    n = 10
    payload = json.dumps({"username": "alice", "password": "definitely-wrong-password"})

    def run_one(_):
        return subprocess.run(
            [
                sys.executable, "-m", "insight", "users", "verify",
                "--accounts-path", str(accounts_path),
            ],
            cwd=str(repo_root),
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(run_one, range(n)))

    for r in results:
        assert r.returncode == 1, "expected every attempt to fail: stdout=%r stderr=%r" % (
            r.stdout, r.stderr,
        )

    record = _read_record(accounts_path, "alice")
    assert record["failed_attempts"] == n, (
        "lost increments under concurrency: expected exactly %d, got %d -- this is precisely "
        "the race _locked_accounts exists to close (an unlocked read-modify-write would produce "
        "a count LESS than %d, never more, as concurrent writers silently clobber each other)"
        % (n, record["failed_attempts"], n)
    )
    # n (10) >= _THROTTLE_THRESHOLD (5), so the account must also have ended up locked.
    assert record["locked_until"] is not None
