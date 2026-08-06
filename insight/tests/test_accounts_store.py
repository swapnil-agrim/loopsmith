# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.accounts.store: add_user/verify_user, duplicate rejection, permission bits,
missing/corrupt store handling, message equality (issue #306 [E18.S1], .sdlc/plans/306.md Task C).

MODULE-LEVEL `pytest.importorskip("argon2")`: every test here really calls store.add_user ->
hashing.hash_password, so a real, working argon2 install is required for any of it to mean
anything -- mirroring test_dash_ic_no_leak.py's own `duckdb = pytest.importorskip("duckdb")`
pattern for a different dependency.
"""
import os
import stat

import pytest

argon2 = pytest.importorskip("argon2")

from insight.accounts import hashing, store  # noqa: E402


@pytest.fixture(autouse=True)
def fast_params(monkeypatch):
    """Every test in this file uses TEST_PARAMS, not PRODUCTION_PARAMS -- Decision 8: this suite
    reruns on every future goal via verify.command, so it must not pay production-strength KDF
    cost repeatedly."""
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)


def test_add_user_then_verify_user_returns_role(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct horse battery staple", "manager", accounts_path=accounts_path)
    role = store.verify_user("alice", "correct horse battery staple", accounts_path=accounts_path)
    assert role == "manager"


def test_add_user_rejects_duplicate_username(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "pw1", "manager", accounts_path=accounts_path)
    import json

    before = json.loads(accounts_path.read_text())["users"]["alice"]["created_at"]

    with pytest.raises(store.UsernameExistsError):
        store.add_user("alice", "pw2", "viewer", accounts_path=accounts_path)

    after = json.loads(accounts_path.read_text())["users"]["alice"]["created_at"]
    assert before == after


def test_add_user_rejects_empty_username_and_empty_role(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    with pytest.raises(ValueError):
        store.add_user("", "pw", "manager", accounts_path=accounts_path)
    with pytest.raises(ValueError):
        store.add_user("alice", "pw", "", accounts_path=accounts_path)
    assert not accounts_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a concept on Windows")
def test_accounts_file_permissions_are_owner_only(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "pw", "manager", accounts_path=accounts_path)
    mode = stat.S_IMODE(accounts_path.stat().st_mode)
    assert mode == 0o600


def test_missing_store_verify_returns_same_invalid_credentials_as_unknown_user(tmp_path):
    accounts_path = tmp_path / "does-not-exist.json"
    with pytest.raises(store.InvalidCredentials) as exc_missing:
        store.verify_user("alice", "pw", accounts_path=accounts_path)

    accounts_path2 = tmp_path / "accounts2.json"
    store.add_user("bob", "pw", "viewer", accounts_path=accounts_path2)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("alice", "pw", accounts_path=accounts_path2)

    assert exc_missing.value.args == exc_unknown.value.args


def test_corrupt_store_raises_distinct_error_not_invalid_credentials(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_bytes(b"\x00\x01not-json-garbage{{{")

    with pytest.raises(store.AccountsStoreCorruptError) as exc:
        store.verify_user("alice", "pw", accounts_path=accounts_path)
    assert "not-json-garbage" not in str(exc.value)
    assert str(accounts_path) in str(exc.value)


def test_add_user_refuses_to_write_over_a_corrupt_store(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_bytes(b"\x00\x01not-json-garbage{{{")

    with pytest.raises(store.AccountsStoreCorruptError):
        store.add_user("alice", "pw", "manager", accounts_path=accounts_path)


def test_verify_user_message_identical_for_wrong_password_and_unknown_user(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    with pytest.raises(store.InvalidCredentials) as exc_wrong:
        store.verify_user("alice", "totally-wrong-password", accounts_path=accounts_path)
    with pytest.raises(store.InvalidCredentials) as exc_unknown:
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)

    assert type(exc_wrong.value) is store.InvalidCredentials
    assert type(exc_unknown.value) is store.InvalidCredentials
    assert exc_wrong.value.args == exc_unknown.value.args


# --------------------------------------------------------------------------- PR #461 review, BLOCKING 1: symlink/TOCTOU


def test_add_user_symlinked_tmp_path_does_not_clobber_the_symlink_target(tmp_path):
    """Plants the LIVE exploit the review proved: a symlink at the exact predictable temp-file
    path `_write_accounts` used to use (`.<name>.tmp`), pointing at a victim file the test process
    can write. Pre-fix, `add_user` (a) writes the accounts JSON THROUGH the symlink, clobbering
    the victim file, and (b) `os.replace()`s the symlink itself into place at the real accounts
    path, silently redirecting every future read/write. Post-fix, the temp file name is a fresh
    unpredictable `secrets.token_hex` name every call, so a symlink planted at the OLD fixed name
    is simply never touched: it is not read, not written through, and not replaced into place --
    it just sits there, inert, while add_user writes its real temp file under its own new name."""
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


def test_add_user_refuses_when_the_accounts_path_itself_is_a_symlink(tmp_path):
    """The second half of the review's exploit chain, tested directly: if the real accounts path
    is ALREADY a symlink (e.g. because some earlier attack succeeded, or an operator's mistake),
    add_user must refuse loudly rather than silently writing through it -- clobbering whatever the
    link points at."""
    accounts_path = tmp_path / "accounts.json"
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("must survive\n", encoding="utf-8")
    accounts_path.symlink_to(victim_path)

    with pytest.raises(store.AccountsStoreCorruptError):
        store.add_user("alice", "pw", "manager", accounts_path=accounts_path)

    assert victim_path.read_text(encoding="utf-8") == "must survive\n"
    assert accounts_path.is_symlink(), "must be refused, not silently replaced"


def test_verify_user_refuses_to_read_through_a_symlinked_accounts_path(tmp_path):
    """The READ side of the same hardening: an attacker-controlled file symlinked in as the
    accounts store must never be silently trusted -- it could contain a planted admin account
    with a password the attacker knows."""
    accounts_path = tmp_path / "accounts.json"
    real_store_path = tmp_path / "real-store-elsewhere.json"
    store.add_user("mallory", "attacker-controlled-password", "admin", accounts_path=real_store_path)
    accounts_path.symlink_to(real_store_path)

    with pytest.raises(store.AccountsStoreCorruptError):
        store.verify_user("mallory", "attacker-controlled-password", accounts_path=accounts_path)


# --------------------------------------------------------------------------- PR #461 review, BLOCKING 2: corrupt per-record hash


def test_corrupt_password_hash_on_one_record_raises_identical_invalid_credentials(tmp_path):
    """The reviewer's proof: a VALID store whose ONE user's `password_hash` field alone is
    mangled (still valid JSON -- a partial write, a bad migration, a hand-edit) must be
    indistinguishable, to the caller, from a wrong password or an unknown username: same
    exception TYPE, same message. Pre-fix this raised `argon2.exceptions.InvalidHashError`
    (uncaught) instead."""
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    import json

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
    """Same as above, but the wrong-password comparison is made against a SECOND, non-corrupt
    account in the SAME store, so the exact message/type equality is proven against a genuine
    credential mismatch rather than reusing the corrupted account for both sides."""
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "alice-password", "manager", accounts_path=accounts_path)
    store.add_user("bob", "bob-password", "viewer", accounts_path=accounts_path)

    import json

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
    """The corrupt-record path must not become a fast path that itself is a timing distinguisher
    (PR #461 review, BLOCKING 2's timing half): it must still call hashing.verify_password against
    the dummy hash, exactly like the unknown-user path does, rather than raising immediately on
    catching CorruptHashError."""
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    import json

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
    """An operator must have SOME way to learn a record is corrupt -- a stderr signal that does
    NOT reach the caller's exception (the exception itself stays identical to every other
    failure, per done-when 3)."""
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    import json

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
