# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Code-path symmetry proof -- done-when 3's timing half (issue #306 [E18.S1],
.sdlc/plans/306.md Decision 7 / Task E). NOT a wall-clock assertion (Decision 7 explicitly
rejects that as flaky) -- a deterministic proxy for constant work: verify_user calls
hashing.verify_password EXACTLY ONCE on every failure path, and the dummy hash it verifies
against for an unknown user is a precomputed constant, never something hashed inline in the
request path.

MODULE-LEVEL `pytest.importorskip("argon2")`: every test here really calls store.add_user/
verify_user, which really hash/verify -- needs a real argon2 install.
"""
import re

import pytest

argon2 = pytest.importorskip("argon2")

from insight.accounts import hashing, store  # noqa: E402


@pytest.fixture(autouse=True)
def fast_params(monkeypatch):
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)


def test_unknown_user_and_wrong_password_each_call_verify_password_exactly_once(tmp_path, monkeypatch):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    calls = {"count": 0}
    real_verify = hashing.verify_password

    def counting_verify(password, encoded_hash):
        calls["count"] += 1
        return real_verify(password, encoded_hash)

    monkeypatch.setattr(store.hashing, "verify_password", counting_verify)

    calls["count"] = 0
    with pytest.raises(store.InvalidCredentials):
        store.verify_user("alice", "wrong-password", accounts_path=accounts_path)
    assert calls["count"] == 1

    calls["count"] = 0
    with pytest.raises(store.InvalidCredentials):
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)
    assert calls["count"] == 1


def test_dummy_hash_has_the_same_kdf_parameters_as_a_real_account(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    import json
    real_hash = json.loads(accounts_path.read_text())["users"]["alice"]["password_hash"]
    dummy_hash = hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS)

    def _params(encoded):
        m = re.search(r"m=(\d+),t=(\d+),p=(\d+)", encoded)
        assert m, encoded
        return tuple(int(x) for x in m.groups())

    assert _params(real_hash) == _params(dummy_hash)


def test_dummy_hash_lookup_costs_no_kdf_operation(tmp_path, monkeypatch):
    """Catches the dummy-hash creation cost ever moving back inline (BLOCKING-3 finding): wraps
    hashing.hash_password itself (not verify_password) with a counting spy, calls
    store.verify_user for a nonexistent user, and asserts hash_password's call count is 0."""
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "correct-password", "manager", accounts_path=accounts_path)

    calls = {"count": 0}
    real_hash_password = hashing.hash_password

    def counting_hash_password(*args, **kwargs):
        calls["count"] += 1
        return real_hash_password(*args, **kwargs)

    monkeypatch.setattr(store.hashing, "hash_password", counting_hash_password)

    with pytest.raises(store.InvalidCredentials):
        store.verify_user("no-such-user", "whatever", accounts_path=accounts_path)
    assert calls["count"] == 0
