# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.accounts.store: add_user/verify_user, duplicate rejection, permission bits,
missing/corrupt store handling, symlink/TOCTOU hardening, per-record corruption handling, message
equality (issue #306 [E18.S1], .sdlc/plans/306.md Task C; PR #461 review, both passes).

GATING IS PER-FUNCTION, NOT MODULE-LEVEL (PR #461 review, second pass, SHOULD-FIX 3 -- mirroring
the pattern already established in test_cli_users.py). A prior revision of this file had a single
MODULE-LEVEL `pytest.importorskip("argon2")`, which meant every symlink/TOCTOU guard and every
malformed-record-shape guard in this file -- none of which actually need a real KDF to prove --
reported SKIPPED, not merely-not-run, on a machine that genuinely lacks argon2-cffi (this machine,
as of implementation). That is precisely the machine the repo-wide verify gate actually runs on, so
those guards had ZERO regression protection in the run that matters, and would have stayed that way
even after this exact PR #461 review fixed them, per the project's ABSENT!=PASS rule.

Only the tests whose assertions genuinely require a real hash to have been produced call
`pytest.importorskip("argon2")` INSIDE the function body (never at module level):
- `store.add_user` always calls `hashing.hash_password` -- any test that needs `add_user` to
  actually SUCCEED (not merely be attempted and refused/rejected before hashing is ever reached) is
  gated.
- `store.verify_user` always calls `hashing.verify_password` on its OWN failure paths (the
  `record is None` branch, and the known-user branch below) -- any test that lets `verify_user` run
  that far (rather than being intercepted earlier by `_read_accounts` raising
  `AccountsStoreCorruptError` for a symlinked/corrupt store, which happens BEFORE any hashing call)
  is gated.
Tests below are grouped, and each group's own comment says which of these two rules applies and
why. `_read_accounts`/`_write_accounts` themselves touch no hashing at all -- calling them directly
with a synthetic `users` dict is how several groups below get ungated coverage for a guard whose
only previous test went through `add_user`/`verify_user` and paid an unnecessary gate.
"""
import json
import os
import pathlib
import stat

import pytest

from insight.accounts import hashing, store


@pytest.fixture(autouse=True)
def fast_params(monkeypatch):
    """Every test in this file uses TEST_PARAMS, not PRODUCTION_PARAMS -- Decision 8: this suite
    reruns on every future goal via verify.command, so it must not pay production-strength KDF
    cost repeatedly. Harmless to apply even when argon2 is absent -- it is a plain attribute set,
    never a hashing call."""
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)


def _synthetic_record(password_hash="a-synthetic-hash-never-verified-in-this-test", role="manager"):
    """A plain dict shaped like a real accounts-store record, for tests that need SOME record to
    exist but never actually verify a password against it (so no real argon2 is required)."""
    return {"password_hash": password_hash, "role": role, "created_at": "2024-01-01T00:00:00+00:00"}


# =================================================================================== gated: real add_user / verify_user round trips


def test_add_user_then_verify_user_returns_role(tmp_path):
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct horse battery staple", "manager", accounts_path=accounts_path)
    role = store.verify_user("alice", "correct horse battery staple", accounts_path=accounts_path)
    assert role == "manager"


def test_add_user_rejects_duplicate_username(tmp_path):
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "pw1", "manager", accounts_path=accounts_path)

    before = json.loads(accounts_path.read_text())["users"]["alice"]["created_at"]

    with pytest.raises(store.UsernameExistsError):
        store.add_user("alice", "pw2", "viewer", accounts_path=accounts_path)

    after = json.loads(accounts_path.read_text())["users"]["alice"]["created_at"]
    assert before == after


def test_missing_store_verify_returns_same_invalid_credentials_as_unknown_user(tmp_path):
    """Gated -- both branches call hashing.verify_password against the dummy hash (the
    `record is None` path), which needs a real argon2 install."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "does-not-exist.json"
    with pytest.raises(store.InvalidCredentials) as exc_missing:
        store.verify_user("alice", "pw", accounts_path=accounts_path)

    accounts_path2 = tmp_path / "accounts2.json"
    store.add_user("bob", "pw", "viewer", accounts_path=accounts_path2)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("alice", "pw", accounts_path=accounts_path2)

    assert exc_missing.value.args == exc_unknown.value.args


def test_verify_user_message_identical_for_wrong_password_and_unknown_user(tmp_path):
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    with pytest.raises(store.InvalidCredentials) as exc_wrong:
        store.verify_user("alice", "totally-wrong-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    assert type(exc_wrong.value) is store.InvalidCredentials
    assert type(exc_unknown.value) is store.InvalidCredentials
    assert exc_wrong.value.args == exc_unknown.value.args


# =================================================================================== ungated: control flow that never reaches hashing


def test_add_user_rejects_empty_username_and_empty_role(tmp_path):
    """Ungated -- add_user's empty-username/-role ValueError fires before path resolution even
    happens, let alone before hashing.hash_password is ever called."""
    accounts_path = tmp_path / "accounts.json"
    with pytest.raises(ValueError):
        store.add_user("", "pw", "manager", accounts_path=accounts_path)
    with pytest.raises(ValueError):
        store.add_user("alice", "pw", "", accounts_path=accounts_path)
    assert not accounts_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a concept on Windows")
def test_write_accounts_file_permissions_are_owner_only(tmp_path):
    """Ungated (PR #461 review, second pass, SHOULD-FIX 3) -- rewritten to call
    `store._write_accounts` directly with a synthetic record rather than through `add_user`, so
    this guard (owner-only 0600 permissions, set explicitly rather than left at whatever the
    process umask would produce) has coverage on every machine, not just ones with argon2-cffi
    installed. `_write_accounts` sets permission bits regardless of what `users` contains."""
    accounts_path = tmp_path / "accounts.json"
    store._write_accounts(accounts_path, {"alice": _synthetic_record()})
    mode = stat.S_IMODE(accounts_path.stat().st_mode)
    assert mode == 0o600


def test_corrupt_store_raises_distinct_error_not_invalid_credentials(tmp_path):
    """Ungated -- `_read_accounts` raises `AccountsStoreCorruptError` for unparseable JSON BEFORE
    `verify_user` ever reaches its own hashing calls."""
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_bytes(b"\x00\x01not-json-garbage{{{")

    with pytest.raises(store.AccountsStoreCorruptError) as exc:
        store.verify_user("alice", "pw", accounts_path=accounts_path)
    assert "not-json-garbage" not in str(exc.value)
    assert str(accounts_path) in str(exc.value)


def test_add_user_refuses_to_write_over_a_corrupt_store(tmp_path):
    """Ungated -- `add_user` calls `_read_accounts` (which raises for the corrupt store) before it
    ever reaches `hashing.hash_password`."""
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_bytes(b"\x00\x01not-json-garbage{{{")

    with pytest.raises(store.AccountsStoreCorruptError):
        store.add_user("alice", "pw", "manager", accounts_path=accounts_path)


# --------------------------------------------------------------------------- PR #461 review, first pass BLOCKING 1: symlink/TOCTOU (write side)


def test_add_user_symlinked_tmp_path_does_not_clobber_the_symlink_target(tmp_path):
    """Gated -- goes through add_user's real hashing so the final verify_user round trip below can
    prove the account was actually created correctly, not merely that nothing crashed. The
    low-level, ungated counterpart (same guard, no hashing) is
    test_write_accounts_symlinked_tmp_path_does_not_clobber_the_symlink_target below.

    Plants the LIVE exploit the review proved: a symlink at the exact predictable temp-file path
    `_write_accounts` used to use (`.<name>.tmp`), pointing at a victim file the test process can
    write. Pre-fix, `add_user` (a) writes the accounts JSON THROUGH the symlink, clobbering the
    victim file, and (b) `os.replace()`s the symlink itself into place at the real accounts path,
    silently redirecting every future read/write. Post-fix, the temp file name is a fresh
    unpredictable `secrets.token_hex` name every call, so a symlink planted at the OLD fixed name
    is simply never touched: it is not read, not written through, and not replaced into place -- it
    just sits there, inert, while add_user writes its real temp file under its own new name."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("victim content, must survive untouched\n", encoding="utf-8")

    old_predictable_tmp_path = tmp_path / (".%s.tmp" % accounts_path.name)
    old_predictable_tmp_path.symlink_to(victim_path)

    store.add_user("alice", "pw", "manager", accounts_path=accounts_path)

    assert victim_path.read_text(encoding="utf-8") == "victim content, must survive untouched\n", (
        "the attacker's symlink target was clobbered -- the write followed the symlink"
    )
    assert old_predictable_tmp_path.is_symlink(), (
        "the pre-planted symlink at the OLD predictable temp path must be left exactly as it "
        "was -- add_user must never touch it at all now that temp names are unpredictable"
    )
    assert not accounts_path.is_symlink(), (
        "the real accounts path must end up as a REAL file, never the attacker's symlink moved "
        "into place"
    )
    role = store.verify_user("alice", "pw", accounts_path=accounts_path)
    assert role == "manager"


def test_write_accounts_symlinked_tmp_path_does_not_clobber_the_symlink_target(tmp_path):
    """Ungated (PR #461 review, second pass, SHOULD-FIX 3) -- the same guard as the gated test
    above, exercised directly against `_write_accounts` with a synthetic record instead of through
    `add_user`, so it runs on every machine including one without argon2-cffi."""
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("victim content, must survive untouched\n", encoding="utf-8")

    old_predictable_tmp_path = tmp_path / (".%s.tmp" % accounts_path.name)
    old_predictable_tmp_path.symlink_to(victim_path)

    store._write_accounts(accounts_path, {"alice": _synthetic_record()})

    assert victim_path.read_text(encoding="utf-8") == "victim content, must survive untouched\n"
    assert old_predictable_tmp_path.is_symlink()
    assert not accounts_path.is_symlink()
    assert store._read_accounts(accounts_path) == {"alice": _synthetic_record()}


def test_add_user_refuses_when_the_accounts_path_itself_is_a_symlink(tmp_path):
    """Ungated -- `add_user` calls `_read_accounts` first, which refuses (via the O_NOFOLLOW open,
    ELOOP) before `hashing.hash_password` is ever reached. The second half of the review's exploit
    chain, tested directly: if the real accounts path is ALREADY a symlink (e.g. because some
    earlier attack succeeded, or an operator's mistake), add_user must refuse loudly rather than
    silently writing through it -- clobbering whatever the link points at."""
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("must survive\n", encoding="utf-8")
    accounts_path.symlink_to(victim_path)

    with pytest.raises(store.AccountsStoreCorruptError):
        store.add_user("alice", "pw", "manager", accounts_path=accounts_path)

    assert victim_path.read_text(encoding="utf-8") == "must survive\n"
    assert accounts_path.is_symlink(), "must be refused, not silently replaced"


def test_write_accounts_refuses_when_the_path_itself_is_a_symlink(tmp_path):
    """Ungated -- exercises `_write_accounts`'s own `_refuse_if_symlink` guard directly, so it has
    coverage that is not incidentally shadowed by `add_user`'s earlier `_read_accounts` call
    catching the exact same already-symlinked case first (see the `add_user` version above, which
    never actually reaches `_write_accounts` at all in this scenario)."""
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("must survive\n", encoding="utf-8")
    accounts_path.symlink_to(victim_path)

    with pytest.raises(store.AccountsStoreCorruptError):
        store._write_accounts(accounts_path, {"alice": _synthetic_record()})

    assert victim_path.read_text(encoding="utf-8") == "must survive\n"
    assert accounts_path.is_symlink(), "must be refused, not silently replaced"


# --------------------------------------------------------------------------- PR #461 review, second pass BLOCKING 2: symlink/TOCTOU (read side, atomicity)


def test_verify_user_refuses_to_read_through_a_symlinked_accounts_path(tmp_path):
    """Ungated -- rewritten so the setup writes `real_store_path` directly (a plain JSON store,
    never through `add_user`), because the actual guard under test -- `_read_accounts` refusing a
    symlinked path via the O_NOFOLLOW open -- fires before `verify_user` ever reaches a hashing
    call, so the setup doesn't need to either. The READ side of the symlink hardening: an
    attacker-controlled file symlinked in as the accounts store must never be silently trusted --
    it could contain a planted admin account with a password the attacker knows."""
    accounts_path = tmp_path / "accounts.json"
    real_store_path = tmp_path / "real-store-elsewhere.json"
    real_store_path.write_text(
        json.dumps({"version": 1, "users": {"mallory": _synthetic_record(role="admin")}}),
        encoding="utf-8",
    )
    accounts_path.symlink_to(real_store_path)

    with pytest.raises(store.AccountsStoreCorruptError):
        store.verify_user("mallory", "attacker-controlled-password", accounts_path=accounts_path)


def test_read_accounts_refuses_a_dangling_symlink_too(tmp_path):
    """Ungated -- a symlink whose target does not exist must still be refused, not misread as 'no
    accounts yet' (see `_read_accounts`'s own docstring: `O_NOFOLLOW` reports ELOOP for a symlink
    regardless of whether its target exists)."""
    accounts_path = tmp_path / "accounts.json"
    accounts_path.symlink_to(tmp_path / "this-target-does-not-exist.json")

    with pytest.raises(store.AccountsStoreCorruptError):
        store._read_accounts(accounts_path)


def test_read_accounts_wins_the_check_then_act_race_between_symlink_check_and_read(tmp_path, monkeypatch):
    """Ungated -- `_read_accounts` is a pure file-handling function; this test needs no hashing at
    all. Deterministically simulates the reviewer's live exploit: an attacker who swaps the real
    store file for a symlink to an attacker-controlled file in the EXACT window between the pre-fix
    code's `is_symlink()` check and its later read. Rather than relying on real thread-timing luck
    (which would make this test flaky), the swap is injected at the exact call the PRE-FIX code
    used as its symlink check (`pathlib.Path.is_symlink`), deterministically simulating "the check
    ran and correctly said 'not a symlink', then the attacker swapped it before the read" -- which
    is precisely the TOCTOU gap between two separate syscalls.

    Pre-fix (`is_symlink()` -> `exists()` -> `read_text()` as three separate steps): the hook fires
    during the FIRST step, performs the swap, and returns False (truthfully, at the instant it was
    asked). The next two steps then run against the just-planted symlink: `exists()` sees the
    (existing) attacker file through the link and returns True, and `read_text()` transparently
    follows the symlink and returns the attacker's content as trusted, with NO exception raised at
    all -- `mallory`'s planted record comes back as real.

    Post-fix (a single `os.open(O_NOFOLLOW)` call, no separate `is_symlink()` check to race
    against): this hook is simply never invoked by `_read_accounts` at all -- there is no
    `is_symlink()` call left to hang the swap off of -- so the swap never happens, and the
    original, attacker-free file is read safely."""
    accounts_path = tmp_path / "accounts.json"
    attacker_store_path = tmp_path / "attacker-controlled-elsewhere.json"
    attacker_store_path.write_text(
        json.dumps({"version": 1, "users": {"mallory": _synthetic_record(role="admin")}}),
        encoding="utf-8",
    )
    accounts_path.write_text(json.dumps({"version": 1, "users": {}}), encoding="utf-8")

    original_is_symlink = pathlib.Path.is_symlink
    swapped = {"done": False}

    def racing_is_symlink(self):
        if self == accounts_path and not swapped["done"]:
            swapped["done"] = True
            self.unlink()
            self.symlink_to(attacker_store_path)
            # The check ran BEFORE the swap landed -- truthfully "not a symlink" at the instant it
            # was asked. That is exactly the TOCTOU window a reviewer won live against the pre-fix
            # three-step read.
            return False
        return original_is_symlink(self)

    monkeypatch.setattr(pathlib.Path, "is_symlink", racing_is_symlink)

    users = store._read_accounts(accounts_path)

    assert "mallory" not in users, (
        "the check-then-act race between the symlink check and the read was won -- the "
        "attacker's planted record was returned as trusted content"
    )


# --------------------------------------------------------------------------- PR #461 review, third pass SHOULD-FIX: PermissionError is not a raw traceback, and a FIFO does not hang forever


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a concept on Windows")
def test_read_accounts_raises_a_clean_error_not_a_raw_permission_error(tmp_path):
    """Ungated -- `_read_accounts` is a pure file-handling function; this guard needs no hashing at
    all (PR #461 review, third pass SHOULD-FIX). Pre-fix, `os.open()`'s own `PermissionError` (a
    `chmod 000` store) fell through `_read_accounts`'s `except OSError` clause -- which only ever
    special-cased `errno.ELOOP` -- and re-raised RAW, with no dispatch clause anywhere in
    `insight/__main__.py` naming `PermissionError`, so it would reach a real CLI invocation as a
    bare traceback instead of a clean, actionable message. Skipped when running as root: `chmod
    000` does not deny root's own reads (a common CI/container default), which would make the
    assertions below meaningless rather than merely un-exercised."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root -- chmod 000 does not deny root's own read access")
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(json.dumps({"version": 1, "users": {}}), encoding="utf-8")
    os.chmod(accounts_path, 0o000)
    try:
        with pytest.raises(store.AccountsStoreCorruptError) as exc:
            store._read_accounts(accounts_path)
        assert "permission" in str(exc.value).lower()
        assert str(accounts_path) in str(exc.value)
    finally:
        os.chmod(accounts_path, 0o600)  # tmp_path's own cleanup needs to be able to remove this


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a concept on Windows")
def test_add_user_reports_a_clean_error_not_a_raw_traceback_for_an_unreadable_store(tmp_path):
    """Ungated -- `add_user` calls `_read_accounts` before it ever reaches `hashing.hash_password`,
    so this proves the fix all the way up to the CLI-adjacent entry point, not just
    `_read_accounts` in isolation -- `insight/__main__.py`'s `users add` dispatch already catches
    `store.AccountsStoreCorruptError` cleanly (see its own except clauses), so folding
    `PermissionError` into that existing type, rather than inventing a fourth caller-visible
    exception type, is what makes this fix a one-line change instead of new CLI plumbing."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root -- chmod 000 does not deny root's own read access")
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(json.dumps({"version": 1, "users": {}}), encoding="utf-8")
    os.chmod(accounts_path, 0o000)
    try:
        with pytest.raises(store.AccountsStoreCorruptError):
            store.add_user("alice", "pw", "manager", accounts_path=accounts_path)
    finally:
        os.chmod(accounts_path, 0o600)


@pytest.mark.skipif(os.name == "nt", reason="FIFOs are not a POSIX concept on Windows")
def test_read_accounts_refuses_a_fifo_instead_of_hanging_forever(tmp_path):
    """Ungated -- proves the `O_NONBLOCK` fix (PR #461 review, third pass SHOULD-FIX). Pre-fix,
    `os.open(path, O_RDONLY)` on a FIFO with no writer never returns AT ALL -- not an exception, an
    indefinite hang -- confirmed live during implementation with a bounded subprocess reproduction
    of the pre-fix flags (no O_NONBLOCK): it timed out after 5s with the child process still
    blocked inside the open() syscall, and would have stayed blocked indefinitely without the
    external timeout killing it.

    Run here in a background DAEMON thread with a generous, bounded `join(timeout=...)` so a
    regression fails this test PROMPTLY rather than hanging the actual test run: if the fix ever
    regresses, the thread is still blocked in the C-level `open()` syscall when the timeout
    expires, `thread.is_alive()` is True, and the assertion below fails outright. The thread itself
    is never joined past the timeout and is killed for good only when the process exits (a thread
    blocked in a blocking syscall cannot be interrupted from Python) -- acceptable here because
    `daemon=True` means it can never keep the test process alive on its own."""
    import threading

    accounts_path = tmp_path / "accounts.json"
    os.mkfifo(str(accounts_path))

    result = {}

    def target():
        try:
            result["value"] = store._read_accounts(accounts_path)
        except Exception as e:
            result["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), (
        "store._read_accounts blocked for more than 5s opening a FIFO with no writer -- it must "
        "refuse promptly instead of hanging forever"
    )
    assert "error" in result, "expected a refusal, got: %r" % result
    assert isinstance(result["error"], store.AccountsStoreCorruptError), result["error"]


# --------------------------------------------------------------------------- PR #461 review, first pass BLOCKING 2 / second pass BLOCKING 1: corrupt per-record hash, EVERY malformed shape


def test_corrupt_password_hash_on_one_record_raises_identical_invalid_credentials(tmp_path):
    """Gated. The first-pass reviewer's original proof: a VALID store whose ONE user's
    `password_hash` field alone is mangled (still valid JSON -- a partial write, a bad migration, a
    hand-edit) must be indistinguishable, to the caller, from a wrong password or an unknown
    username: same exception TYPE, same message. Pre-first-pass-fix this raised
    `argon2.exceptions.InvalidHashError` (uncaught) instead. See the parametrized test below for
    the shapes the first pass's fix DIDN'T cover (missing key, non-string values)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = "not-a-well-formed-argon2-hash-at-all"
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    # Restore owner-only permissions -- the raw json.dumps write above does not preserve 0600.
    os.chmod(accounts_path, 0o600)

    with pytest.raises(store.InvalidCredentials) as exc_corrupt:
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    assert type(exc_corrupt.value) is store.InvalidCredentials
    assert type(exc_unknown.value) is store.InvalidCredentials
    assert exc_corrupt.value.args == exc_unknown.value.args == (store._INVALID_CREDENTIALS_MESSAGE,)


def test_corrupt_password_hash_message_matches_a_real_wrong_password_on_another_account(tmp_path):
    """Gated. Same as above, but the wrong-password comparison is made against a SECOND,
    non-corrupt account in the SAME store, so the exact message/type equality is proven against a
    genuine credential mismatch rather than reusing the corrupted account for both sides."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "alice-password", "manager", accounts_path=accounts_path)
    store.add_user("bob", "bob-password", "viewer", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = "not-a-well-formed-argon2-hash-at-all"
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(accounts_path, 0o600)

    with pytest.raises(store.InvalidCredentials) as exc_corrupt:
        store.verify_user("alice", "alice-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_wrong:
        store.verify_user("bob", "totally-wrong-password", accounts_path=accounts_path)

    assert type(exc_corrupt.value) is type(exc_wrong.value) is store.InvalidCredentials
    assert exc_corrupt.value.args == exc_wrong.value.args


def test_corrupt_password_hash_still_calls_verify_password_so_it_is_not_a_timing_fast_path(
    tmp_path, monkeypatch
):
    """Gated. The corrupt-record path must not become a fast path that itself is a timing
    distinguisher: it must still call hashing.verify_password against the dummy hash, exactly like
    the unknown-user path does, rather than raising immediately on catching an exception."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = "not-a-well-formed-argon2-hash-at-all"
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(accounts_path, 0o600)

    calls = []
    real_verify_password = hashing.verify_password

    def spy(password, encoded_hash):
        calls.append(encoded_hash)
        return real_verify_password(password, encoded_hash)

    monkeypatch.setattr(hashing, "verify_password", spy)

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)

    # First call: against the corrupt record's own (malformed) hash -- raises CorruptHashError.
    # Second call: against the dummy hash, paying the same KDF-sized cost as every other path.
    assert calls == ["not-a-well-formed-argon2-hash-at-all", hashing.dummy_hash_for(hashing.TEST_PARAMS)]


def test_corrupt_password_hash_is_logged_to_stderr_but_not_in_the_exception(tmp_path, capsys):
    """Gated. An operator must have SOME way to learn a record is corrupt -- a stderr signal that
    does NOT reach the caller's exception (the exception itself stays identical to every other
    failure, per done-when 3)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = "not-a-well-formed-argon2-hash-at-all"
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(accounts_path, 0o600)

    with pytest.raises(store.InvalidCredentials) as exc:
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)

    assert str(exc.value) == store._INVALID_CREDENTIALS_MESSAGE
    err = capsys.readouterr().err
    assert "alice" in err
    assert str(accounts_path) in err
    assert "correct-password" not in err


@pytest.mark.parametrize(
    "shape_name, mutate",
    [
        ("missing_password_hash_key", lambda rec: rec.pop("password_hash")),
        ("password_hash_is_an_int", lambda rec: rec.__setitem__("password_hash", 12345)),
        (
            "password_hash_is_a_list",
            lambda rec: rec.__setitem__("password_hash", ["not", "a", "string"]),
        ),
        ("password_hash_is_empty_string", lambda rec: rec.__setitem__("password_hash", "")),
        (
            "password_hash_is_garbage_string",
            lambda rec: rec.__setitem__("password_hash", "not-a-well-formed-argon2-hash-at-all"),
        ),
        (
            "password_hash_is_well_formed_but_under_different_kdf_params",
            # A genuine, well-formed argon2id hash -- but produced under parameters different from
            # whatever hashing.PRODUCTION_PARAMS currently resolves to (TEST_PARAMS, via this
            # file's fast_params fixture). PR #461 review, third pass BLOCKING -- see
            # test_well_formed_hash_under_different_kdf_params_... below for the full exploit and
            # RED narrative; this parametrize entry is the cheap regression-coverage twin, proving
            # this shape is folded into the SAME "every malformed shape" guarantee as the others.
            lambda rec: rec.__setitem__(
                "password_hash",
                hashing.hash_password(
                    "correct-password",
                    params=hashing.Params(
                        time_cost=hashing.TEST_PARAMS.time_cost + 1,
                        memory_cost=hashing.TEST_PARAMS.memory_cost,
                        parallelism=hashing.TEST_PARAMS.parallelism,
                    ),
                ),
            ),
        ),
    ],
)
def test_every_malformed_password_hash_shape_raises_identical_invalid_credentials_and_pays_kdf_cost(
    tmp_path, monkeypatch, shape_name, mutate
):
    """Gated -- the REAL, argon2-backed proof (PR #461 review, second pass BLOCKING 1; extended in
    the third pass with one more shape, see below). The first pass's fix (`except
    hashing.CorruptHashError`) only ever caught the shape here named
    'password_hash_is_garbage_string' (argon2's own InvalidHashError/VerificationError). Every
    other shape reached the caller as a DIFFERENT, distinguishable exception type -- a plain
    `KeyError` for the missing key (raised before `hashing.verify_password` is even called), and
    `AttributeError`/`TypeError` from inside argon2-cffi's own internals for the non-string values
    -- and, measured, roughly 1000x faster than a real KDF operation: a louder timing tell than the
    one the first pass closed. The THIRD pass added one more shape
    ('password_hash_is_well_formed_but_under_different_kdf_params') that raises NOTHING at all
    pre-fix -- not even a fast exception -- because it decodes and verifies just fine, only under
    the wrong cost parameters; see hashing.py's own docstring for why. Every shape here must now be
    completely indistinguishable from a wrong password: identical exception type, identical
    message, and the real KDF-sized dummy-hash verification must still have been paid (the
    `assert ... in calls` below)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    mutate(data["users"]["alice"])
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(accounts_path, 0o600)

    calls = []
    real_verify_password = hashing.verify_password

    def spy(password, encoded_hash):
        calls.append(encoded_hash)
        return real_verify_password(password, encoded_hash)

    monkeypatch.setattr(hashing, "verify_password", spy)

    with pytest.raises(store.InvalidCredentials) as exc_corrupt:
        store.verify_user("alice", "correct-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    assert type(exc_corrupt.value) is store.InvalidCredentials, shape_name
    assert (
        exc_corrupt.value.args == exc_unknown.value.args == (store._INVALID_CREDENTIALS_MESSAGE,)
    ), shape_name
    # Real KDF work was paid on the corrupt-record path -- the dummy hash was verified against.
    assert hashing.dummy_hash_for(hashing.TEST_PARAMS) in calls, (
        "%s: no real KDF-sized dummy-hash verification happened" % shape_name
    )


# --------------------------------------------------------------------------- PR #461 review, third pass BLOCKING: a well-formed hash with weak/wrong embedded parameters is a 262x timing oracle


def test_well_formed_hash_under_different_kdf_params_raises_identical_invalid_credentials_and_pays_kdf_cost(
    tmp_path, monkeypatch
):
    """Gated -- the REAL, argon2-backed proof for a THIRD, independent way a record's hash can be
    untrustworthy without the store's JSON breaking (PR #461 review, third pass BLOCKING). Argon2's
    own `verify()` reads its cost parameters (m=/t=/p=) OUT OF the hash string itself, never from
    the verifying `PasswordHasher` instance's own config -- so a SYNTACTICALLY VALID argon2id hash
    carrying different-than-expected parameters raises NOTHING at all: `hasher.verify()` just runs
    those (here, cheaper) embedded parameters and returns a clean True/False. Pre-fix, that meant
    the malformed-record `except Exception` boundary in `store.verify_user` never fired for this
    shape -- unlike every OTHER malformed shape in the parametrized test above -- so it paid no
    KDF-sized cost at all. A reviewer measured this live, with a genuine argon2id hash produced
    under TEST_PARAMS swapped into a normal-strength record: 0.0001s for a wrong password against
    the weak-parameter record, versus 0.0234s for a normal record and 0.0238s for an unknown user
    -- a 262x gap, though the exception type and message were identical in all three.

    RED, PRECISELY (not a crash): pre-fix, the type/message assertions below ALREADY PASS -- the
    wrong password still fails to verify against the weak-param hash, same as any wrong password
    would, so `store.verify_user` still raises the identical `InvalidCredentials`. What fails
    pre-fix is the LAST assertion: pre-fix, `hashing.verify_password` is called EXACTLY ONCE
    (against the weak-param hash, which returns `False` cleanly -- no exception, so the
    except-Exception dummy-hash fallback below never runs), so `hashing.dummy_hash_for(...)` is
    never in `calls` and no real KDF-sized cost was ever paid for this path. Post-fix, it is called
    TWICE, exactly like every other shape in the parametrized test above: first against the
    weak-param hash (which now RAISES `CorruptHashError`), then against the dummy hash (paying a
    real, currently-in-force-parameter-strength cost)."""
    pytest.importorskip("argon2")
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    # A REAL, well-formed argon2id hash of a password, produced under parameters that are
    # DIFFERENT from whatever `hashing.PRODUCTION_PARAMS` currently resolves to (TEST_PARAMS, via
    # this file's `fast_params` fixture) -- simulating a hand-edit, a bad migration, or an account
    # created under an older/weaker parameter set. Only `time_cost` differs so this stays fast
    # (Decision 8) while still being unambiguously a parameter MISMATCH, not a coincidental match.
    different_params = hashing.Params(
        time_cost=hashing.TEST_PARAMS.time_cost + 1,
        memory_cost=hashing.TEST_PARAMS.memory_cost,
        parallelism=hashing.TEST_PARAMS.parallelism,
    )
    assert different_params != hashing.PRODUCTION_PARAMS  # sanity: really is a mismatch
    hash_under_different_params = hashing.hash_password(
        "correct-password", params=different_params
    )

    data = json.loads(accounts_path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = hash_under_different_params
    accounts_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(accounts_path, 0o600)

    calls = []
    real_verify_password = hashing.verify_password

    def spy(password, encoded_hash):
        calls.append(encoded_hash)
        return real_verify_password(password, encoded_hash)

    monkeypatch.setattr(hashing, "verify_password", spy)

    with pytest.raises(store.InvalidCredentials) as exc_corrupt:
        store.verify_user("alice", "totally-wrong-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    assert type(exc_corrupt.value) is store.InvalidCredentials
    assert (
        exc_corrupt.value.args == exc_unknown.value.args == (store._INVALID_CREDENTIALS_MESSAGE,)
    )
    # The assertion that fails RED pre-fix: comparable work, proven by the SAME
    # dummy-hash-was-verified-against signal the parametrized test above already uses for every
    # other malformed shape.
    assert hashing.dummy_hash_for(hashing.TEST_PARAMS) in calls, (
        "no real KDF-sized dummy-hash verification happened for a well-formed hash under "
        "different-than-expected parameters -- this is exactly the 262x timing oracle the fix "
        "closes"
    )
    # THREE calls, not two: the spy is shared across BOTH verify_user calls above. Alice's
    # mismatched-parameter record contributes two (the hash that must raise, then the dummy that
    # pays the real cost), and the unknown-user lookup contributes its own dummy verification.
    # Asserting two made this fail wherever argon2-cffi is installed -- CI, and nowhere the author
    # could see (PR #461, review 4). The unknown-user entry is part of the point: both callers end
    # up having paid exactly one real KDF operation.
    dummy = hashing.dummy_hash_for(hashing.TEST_PARAMS)
    assert calls == [hash_under_different_params, dummy, dummy], (
        "expected three verify_password calls -- for alice, the mismatched-parameter hash (which "
        "must raise) then the dummy hash (which pays the real cost); then the unknown user's own "
        "dummy-hash verification"
    )


# --------------------------------------------------------------------------- PR #461 review, second pass BLOCKING 1, ungated structural proof (SHOULD-FIX 3)


def test_verify_user_folds_any_exception_from_the_known_user_branch_into_invalid_credentials(
    tmp_path, monkeypatch
):
    """Ungated -- proves store.verify_user's OWN control flow (the `except Exception` boundary
    around the known-user branch), not argon2's cryptography, catches literally ANY exception type,
    by faking `hashing.verify_password`/`hashing.dummy_hash_for` so this needs no real argon2
    install at all. This is the direct proof that the fix is not an allowlist: it simulates "the
    next unpredicted exception type" with a deliberately arbitrary one (`ArithmeticError`) that
    neither review round ever mentioned, and it must still be folded into `InvalidCredentials`. The
    REAL, argon2-backed proof of the same property (for the specific shapes the review actually
    found) is
    `test_every_malformed_password_hash_shape_raises_identical_invalid_credentials_and_pays_kdf_cost`
    above (gated -- see module docstring)."""
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps({"version": 1, "users": {"alice": _synthetic_record()}}), encoding="utf-8"
    )
    os.chmod(accounts_path, 0o600)

    dummy_calls = []

    def fake_verify_password(password, encoded_hash):
        if encoded_hash == "THE-DUMMY-HASH":
            dummy_calls.append(encoded_hash)
            return False
        raise ArithmeticError("a completely arbitrary exception type nobody predicted")

    monkeypatch.setattr(store.hashing, "verify_password", fake_verify_password)
    monkeypatch.setattr(store.hashing, "dummy_hash_for", lambda params: "THE-DUMMY-HASH")

    with pytest.raises(store.InvalidCredentials) as exc:
        store.verify_user("alice", "irrelevant", accounts_path=accounts_path)

    assert exc.value.args == (store._INVALID_CREDENTIALS_MESSAGE,)
    assert dummy_calls == ["THE-DUMMY-HASH"], "the dummy-hash fallback must still be paid"


def test_verify_user_still_propagates_kdf_unavailable_error_from_the_known_user_branch(
    tmp_path, monkeypatch
):
    """Ungated -- proves the ONE deliberate exclusion from the broad `except Exception` boundary:
    `hashing.KDFUnavailableError` (argon2 itself absent) is NOT a malformed-record concern and must
    still propagate to `verify_user`'s own caller unchanged, exactly as it does from the
    `record is None` branch, which never wraps its own `hashing.verify_password` call either. This
    is a non-regression pin, not a red/green pair -- KDFUnavailableError was never a subclass of
    the narrower `hashing.CorruptHashError` the first pass caught, so this already passed before
    this fix too; it exists so a future edit that widens the except clause's exclusions (or removes
    this one) fails loudly instead of silently starting to swallow a real infra failure."""
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps({"version": 1, "users": {"alice": _synthetic_record()}}), encoding="utf-8"
    )
    os.chmod(accounts_path, 0o600)

    def fake_verify_password(password, encoded_hash):
        raise hashing.KDFUnavailableError("argon2-cffi is required ... pip install argon2-cffi")

    monkeypatch.setattr(store.hashing, "verify_password", fake_verify_password)

    with pytest.raises(hashing.KDFUnavailableError):
        store.verify_user("alice", "irrelevant", accounts_path=accounts_path)


# --------------------------------------------------------------------------- issue #308 [E18.S3], .sdlc/plans/308.md Task 1: fcntl-based locking primitive, wired through add_user
#
# Ungated throughout -- these guards are about file locking and process concurrency, not
# hashing, so none of them need a real argon2 install EXCEPT the two that actually exercise
# add_user's own hashing call end to end (marked below).


def test_locked_accounts_raises_loud_error_when_fcntl_unavailable(tmp_path, monkeypatch):
    """Direct proof of the guarded-import seam (mirrors hashing.py:70-73's own `argon2 = None`
    pattern exactly): `monkeypatch.setattr(store, "fcntl", None)` deterministically exercises the
    "locking unavailable" path on this machine, which genuinely HAS fcntl -- so this is the only
    way to prove the fail-loud behavior without a real non-POSIX machine. Must raise BEFORE
    creating or opening anything (no lock file, no accounts file)."""
    monkeypatch.setattr(store, "fcntl", None)
    accounts_path = tmp_path / "accounts.json"
    with pytest.raises(store.AccountsLockUnavailableError):
        with store._locked_accounts(accounts_path):
            pytest.fail("must raise before the with-block body ever runs")
    assert not accounts_path.exists()
    assert not (tmp_path / "accounts.json.lock").exists()


def test_add_user_raises_loud_error_when_fcntl_unavailable_rather_than_writing_unlocked(
    tmp_path, monkeypatch
):
    """add_user must propagate AccountsLockUnavailableError rather than silently falling back to
    an unlocked read-modify-write (Decision 4's whole point: a silent no-op would make this
    APPEAR tested and safe while providing zero protection)."""
    monkeypatch.setattr(store, "fcntl", None)
    accounts_path = tmp_path / "accounts.json"
    with pytest.raises(store.AccountsLockUnavailableError):
        store.add_user("alice", "pw", "manager", accounts_path=accounts_path)
    assert not accounts_path.exists()


def test_locked_accounts_refuses_to_follow_a_symlinked_lock_file(tmp_path):
    """issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 (both reviewers of #308, a consistency
    finding): `_locked_accounts` opens its sibling `<path>.lock` file with `O_NOFOLLOW` too now,
    matching `_read_accounts_data`/`_open_fresh_temp_file`'s own use of the same flag -- this was
    previously the one open in the module that omitted it. A symlink planted at the predictable
    `<accounts>.lock` path (pointing at some unrelated file this process has no business locking
    or touching) must be refused outright (`OSError`, `errno.ELOOP` on a platform that supports
    the flag), never silently followed and `flock()`ed -- `flock()` locks the OPEN FILE
    DESCRIPTION, so following the symlink would mean locking (and, on some code paths, later
    writing) the SYMLINK'S TARGET rather than the sibling lock file this function believes it is
    locking. Ungated, matching the module's other symlink-hijack tests -- this repo's own
    CI/local dev are both POSIX (see AccountsLockUnavailableError's own docstring)."""
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("must survive\n", encoding="utf-8")
    lock_path = tmp_path / "accounts.json.lock"
    lock_path.symlink_to(victim_path)

    with pytest.raises(OSError):
        with store._locked_accounts(accounts_path):
            pytest.fail("must raise before the with-block body ever runs")

    assert victim_path.read_text(encoding="utf-8") == "must survive\n"
    assert lock_path.is_symlink(), "must be refused, not silently replaced"
    assert not accounts_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="fcntl.flock is POSIX-only")
def test_locked_accounts_serializes_two_concurrent_callers(tmp_path):
    """Direct test of `_locked_accounts`'s mutual exclusion (issue #308 [E18.S3],
    .sdlc/plans/308.md Task 1) -- two THREADS, each opening its OWN file descriptor via a
    separate `_locked_accounts` call (an `flock` conflict is keyed on the open file description,
    not the process, so two distinct `os.open()` calls in the same process still correctly
    contend -- this is real OS-level locking, not a simulation). Ordering is asserted via a
    shared side-effect log built from `threading.Event`s, never via a timing/`sleep` race, so
    this is not flaky: the SECOND caller cannot log "second-enter" until the FIRST has both
    entered (proven by a wait on `first_has_entered`) and is deliberately held open by
    `first_may_release`, which does not fire until the assertion below has already inspected the
    log while the first caller is verified still holding the lock."""
    import threading

    accounts_path = tmp_path / "accounts.json"
    log = []
    first_has_entered = threading.Event()
    first_may_release = threading.Event()
    second_has_entered = threading.Event()

    def first():
        with store._locked_accounts(accounts_path):
            log.append("first-enter")
            first_has_entered.set()
            first_may_release.wait(timeout=5)
            log.append("first-exit")

    def second():
        first_has_entered.wait(timeout=5)
        with store._locked_accounts(accounts_path):
            log.append("second-enter")
            second_has_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t1_entered = first_has_entered.wait(timeout=5)
    assert t1_entered, "first caller never entered its critical section"
    t2.start()
    # Give the second thread a real chance to reach (and block on) the lock while the first
    # still holds it -- this sleep is NOT the correctness assertion (the log content below is),
    # it only makes the "second is still blocked" negative assertion meaningful rather than
    # trivially true because second() simply hadn't been scheduled yet.
    blocked_before_release = not second_has_entered.wait(timeout=0.3)
    assert blocked_before_release, (
        "the second caller entered its critical section WHILE the first still held the lock -- "
        "_locked_accounts is not providing mutual exclusion"
    )
    assert log == ["first-enter"]
    first_may_release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert log == ["first-enter", "first-exit", "second-enter"], log


@pytest.mark.skipif(os.name == "nt", reason="multiprocessing 'fork' context is POSIX-only")
def test_ten_concurrent_add_user_calls_against_one_store_all_succeed_under_the_lock(tmp_path):
    """Regression test for the exact race add_user's own docstring measured live PRE-#308 (10
    concurrent `add_user` calls, each for a distinct username against the same store, 8 of 10
    silently lost) -- issue #308 [E18.S3], .sdlc/plans/308.md Task 1. Uses REAL, separate OS
    processes (`multiprocessing.get_context("fork")`, never threads sharing one
    interpreter/GIL) so this is a genuine reproduction of the race shape, not a simulation. The
    `fork` context (rather than the platform-default `spawn` on macOS) means each child is a
    direct copy of this already-imported, already `fast_params`-monkeypatched process -- no
    pickling of the target closure is needed, and each child's `hashing.PRODUCTION_PARAMS` is
    already the fast TEST_PARAMS this file's autouse fixture set, so ten real KDF operations stay
    cheap."""
    pytest.importorskip("argon2")
    import multiprocessing

    accounts_path = tmp_path / "accounts.json"
    n = 10

    def worker(i):
        store.add_user("user%d" % i, "pw", "manager", accounts_path=accounts_path)

    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=worker, args=(i,)) for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    for i, p in enumerate(procs):
        assert p.exitcode == 0, "worker %d exited %r (should have succeeded)" % (i, p.exitcode)

    users = json.loads(accounts_path.read_text())["users"]
    assert len(users) == n, (
        "expected all %d accounts to exist -- lost accounts under concurrency is exactly the "
        "pre-#308 'last writer wins silently' race _locked_accounts exists to close (got %d)"
        % (n, len(users))
    )
    for i in range(n):
        assert "user%d" % i in users
