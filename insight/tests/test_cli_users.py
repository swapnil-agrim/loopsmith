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
