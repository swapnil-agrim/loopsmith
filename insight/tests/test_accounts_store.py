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
