# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`insight users add` CLI wiring (issue #306 [E18.S1], .sdlc/plans/306.md Task F step 17).

NO MODULE-LEVEL `pytest.importorskip("argon2")` in this file -- that would recreate the exact
defect a prior review caught (a module-level guard skips the KDF-unavailable seam test, which
lives in this file too, exactly where it matters most that it NOT skip). Gating is PER-FUNCTION,
only on the tests whose assertions genuinely require a real hash to have been produced:

- `test_help_lists_users_subcommand`, `test_users_add_requires_username_and_role_flags`,
  `test_users_add_mismatched_confirmation_fails_and_writes_nothing`, and
  `test_users_add_reports_kdf_unavailable_loudly_not_as_a_traceback` are UNGATED -- none of them
  reach `insight.accounts.hashing`'s real KDF call. The last of these is the CLI-level counterpart
  to test_accounts_hashing_kdf_unavailable.py's seam test and must run (not skip) on this exact
  machine, which genuinely lacks argon2-cffi.
- `test_users_add_prompts_twice_and_succeeds_on_matching_passwords` and
  `test_users_add_duplicate_username_exits_nonzero_with_clear_message` are GATED at function
  scope (`pytest.importorskip("argon2")`) -- both really call store.add_user -> hash_password.
"""
import pathlib

import pytest

from insight.__main__ import build_parser, main


def test_help_lists_users_subcommand(capsys):
    """Ungated -- building the parser and rendering --help never imports insight.accounts."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["users", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "add" in out


def test_users_add_requires_username_and_role_flags():
    """Ungated -- argparse rejects the missing flag before any dispatch branch runs."""
    with pytest.raises(SystemExit) as exc:
        main(["users", "add", "--role", "manager"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        main(["users", "add", "--username", "alice"])
    assert exc.value.code == 2


def test_users_add_prompts_twice_and_succeeds_on_matching_passwords(tmp_path, monkeypatch, capsys):
    """Gated: this path really calls store.add_user -> hashing.hash_password, so it needs a real
    KDF to succeed."""
    pytest.importorskip("argon2")
    import insight.accounts.hashing as hashing
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)
    monkeypatch.chdir(tmp_path)

    prompts = iter(["Tr0ub4dor&3", "Tr0ub4dor&3"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "alice" in out
    assert "manager" in out
    assert "Tr0ub4dor&3" not in out
    assert (tmp_path / ".sdlc" / "insight-accounts.json").exists()


def test_users_add_mismatched_confirmation_fails_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """Ungated -- the CLI must reject a mismatched confirmation before ever calling
    store.add_user, so this path never touches argon2 and is expected to pass identically with or
    without the package installed. If implementation ever reorders this so hashing happens before
    the match check, this test failing (not skipping) here is itself the signal that happened."""
    monkeypatch.chdir(tmp_path)
    prompts = iter(["password-one", "password-two"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "password-one" not in out and "password-one" not in err
    assert "password-two" not in out and "password-two" not in err
    assert err != ""
    assert not (tmp_path / ".sdlc" / "insight-accounts.json").exists()


def test_users_add_duplicate_username_exits_nonzero_with_clear_message(tmp_path, monkeypatch, capsys):
    """Gated -- proving "duplicate" requires a first real add_user to have already succeeded."""
    pytest.importorskip("argon2")
    import insight.accounts.hashing as hashing
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)
    monkeypatch.chdir(tmp_path)

    prompts = iter(["pw-one", "pw-one", "pw-two", "pw-two"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    assert main(["users", "add", "--username", "alice", "--role", "manager"]) == 0
    code = main(["users", "add", "--username", "alice", "--role", "viewer"])
    assert code == 1
    err = capsys.readouterr().err
    assert "alice" in err


def test_users_add_reports_kdf_unavailable_loudly_not_as_a_traceback(tmp_path, monkeypatch, capsys):
    """Ungated, and must run (not skip) on this machine -- the CLI-level counterpart to
    test_accounts_hashing_kdf_unavailable.py's seam test. Monkeypatches argon2 to None regardless
    of what's really installed, so it proves the loud-failure path even where argon2-cffi is
    genuinely absent, which is exactly this machine today."""
    import insight.accounts.hashing as hashing
    monkeypatch.setattr(hashing, "argon2", None)
    monkeypatch.chdir(tmp_path)

    prompts = iter(["some-password", "some-password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "argon2-cffi" in err
    assert "pip install" in err
    assert "Traceback" not in out
    assert "Traceback" not in err


def test_help_lists_verify_subcommand(capsys):
    """Ungated -- building the parser and rendering --help never imports insight.accounts."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["users", "verify", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "accounts-path" in out


def test_users_verify_malformed_stdin_exits_4_not_traceback(monkeypatch, capsys):
    """Ungated: a Node bridge bug (bad JSON, missing keys) is an operator problem, distinct from
    exit 1 (bad credentials) -- issue #307 [E18.S2], .sdlc/plans/307.md Decision 1."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    code = main(["users", "verify"])
    assert code == 4
    out, err = capsys.readouterr()
    assert out == ""
    assert "Traceback" not in err


def test_users_verify_kdf_unavailable_exits_2_never_as_invalid_credentials(
    tmp_path, monkeypatch, capsys
):
    """Ungated, and MUST run (not skip) on this machine -- the CLI-level counterpart to
    test_users_add_reports_kdf_unavailable_loudly_not_as_a_traceback. This is the seam Decision 1
    exists to protect: a missing KDF must never read as 'wrong password' to a caller parsing exit
    codes, or it silently locks out every user behind a misleading message."""
    import io, json
    import insight.accounts.hashing as hashing
    monkeypatch.setattr(hashing, "argon2", None)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"username": "alice", "password": "x"}))
    )
    accounts_path = tmp_path / "accounts.json"
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "argon2-cffi" in err


def test_users_verify_correct_password_returns_role_exit_0(tmp_path, monkeypatch, capsys):
    """Gated: exercises the real KDF end to end."""
    pytest.importorskip("argon2")
    import io, json
    import insight.accounts.hashing as hashing
    from insight.accounts import store
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "Tr0ub4dor&3", "manager", accounts_path=accounts_path)

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"username": "alice", "password": "Tr0ub4dor&3"})),
    )
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {"role": "manager"}
    assert err == ""
    assert "Tr0ub4dor&3" not in out


def test_users_verify_wrong_password_exits_1_generic_message(tmp_path, monkeypatch, capsys):
    """Gated: needs a real hash to compare against."""
    pytest.importorskip("argon2")
    import io, json
    import insight.accounts.hashing as hashing
    from insight.accounts import store
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)
    accounts_path = tmp_path / "accounts.json"
    store.add_user("alice", "Tr0ub4dor&3", "manager", accounts_path=accounts_path)

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"username": "alice", "password": "wrong"}))
    )
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 1
    out, err = capsys.readouterr()
    # stdout carries the machine verdict the Node bridge REQUIRES before it will call this a
    # credentials failure (independent security review of #307): exit 1 alone is also what CPython
    # returns for any uncaught exception, so a bare exit 1 must read as an operator failure. See
    # test_users_verify_exit_1_carries_the_invalid_credentials_marker below.
    assert json.loads(out) == {"error": "invalid_credentials"}
    assert "invalid username or password" in err


def test_users_verify_exit_1_carries_the_invalid_credentials_marker(
    tmp_path, monkeypatch, capsys
):
    """The Node half of this contract is pinned in
    insight/web/scripts/prove-python-bridge-exit-codes.mjs; this is the Python half.

    Why it exists: pythonBridge.ts used to map a BARE exit 1 to "invalid username or password".
    CPython also exits 1 for any uncaught exception -- e.g. the ModuleNotFoundError raised when
    `insight` is not importable from the child's CWD, a fragility pythonBridge.ts's own REPO_ROOT
    comment admits -- and __main__.py's "not implemented yet" fallthrough returns 1 too. So a
    broken interpreter told users with the CORRECT password that it was wrong, masking a total
    outage as a credentials problem. The verdict is now asserted positively here and demanded
    there.

    Gated: reaching InvalidCredentials for an UNKNOWN user still pays store.py's dummy-hash KDF
    (the timing-equalisation path), so this needs argon2-cffi just like its siblings above."""
    pytest.importorskip("argon2")
    import io, json
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(json.dumps({"version": 1, "users": {}}))
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"username": "nobody", "password": "x"}))
    )
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 1
    out, err = capsys.readouterr()
    assert json.loads(out) == {"error": "invalid_credentials"}
    # ...and the marker stays a constant: it must not become an oracle distinguishing "no such
    # user" from "wrong password", which is precisely what store.InvalidCredentials' single
    # message and dummy-hash KDF exist to prevent.
    assert "nobody" not in out
    assert "invalid username or password" in err


def test_users_verify_corrupt_store_exits_3_not_as_invalid_credentials(
    tmp_path, monkeypatch, capsys
):
    """Ungated: a whole-store parse failure happens before any KDF call, so this runs identically
    with or without argon2-cffi installed -- see store.AccountsStoreCorruptError's own docstring
    distinguishing 'store as a whole is broken' from 'one record is bad' (the latter folds into
    InvalidCredentials/exit 1, correctly, inside store.verify_user itself)."""
    import io, json
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text("{not valid json")
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"username": "alice", "password": "x"}))
    )
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 3
    out, err = capsys.readouterr()
    assert out == ""


def test_users_add_reports_lock_unavailable_loudly_not_as_a_traceback(tmp_path, monkeypatch, capsys):
    """Code review finding, issue #308 [E18.S3], .sdlc/plans/308.md Decision 4:
    store.AccountsLockUnavailableError was not caught by this handler, so it escaped as a raw
    traceback instead of this module's established clean-CLI-error convention (every other
    account-store error above prints one stderr line and returns non-zero). Ungated -- fcntl
    unavailability is simulated via monkeypatch, never a real KDF call is reached."""
    monkeypatch.chdir(tmp_path)
    from insight.accounts import store as accounts_store
    monkeypatch.setattr(accounts_store, "fcntl", None)

    prompts = iter(["some-password", "some-password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "fcntl" in err
    assert "Traceback" not in out
    assert "Traceback" not in err


def test_users_verify_lock_unavailable_exits_2_same_as_kdf_unavailable_not_traceback_not_invalid_credentials(
    tmp_path, monkeypatch, capsys
):
    """Both independent code/security reviews of #308: same gap as the `add` handler's own test
    above, for `verify`. Exit code 2 is REUSED from `hashing.KDFUnavailableError` -- deliberately
    not a fresh code -- because pythonBridge.ts's `switch` already maps exit 2 to
    `CredentialCheckUnavailableError`, and a locking failure is exactly that same class of
    operator/infra problem, never "invalid credentials." Reusing the existing code keeps this
    inside the Python/Node exit-code contract `insight/web/scripts/
    prove-python-bridge-exit-codes.mjs` pins, rather than growing it ad hoc. Critically, this must
    NOT be exit 1 either (which the Node bridge treats as a genuine credentials verdict when
    marked)."""
    import io, json
    from insight.accounts import store as accounts_store
    monkeypatch.setattr(accounts_store, "fcntl", None)
    accounts_path = tmp_path / "accounts.json"
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"username": "alice", "password": "x"}))
    )
    code = main(["users", "verify", "--accounts-path", str(accounts_path)])
    assert code == 2, "AccountsLockUnavailableError must exit with the SAME code as KDFUnavailableError"
    out, err = capsys.readouterr()
    assert out == ""
    assert "fcntl" in err
    assert "Traceback" not in err
