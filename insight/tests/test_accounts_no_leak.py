# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Done-when 2's own required test: `insight users add` never echoes, logs, or stores the
password in plaintext anywhere -- asserted by a test that greps every artifact the CLI actually
wrote, including stdout/stderr (issue #306 [E18.S1], .sdlc/plans/306.md Task D).

Mirrors insight/tests/test_dash_ic_no_leak.py's own structure and stated purpose: assertions run
against the RAW BYTES of every written artifact, and a shared assertion helper is proven
falsifiable via a shipped, permanent negative control.

GATING IS PER-FIXTURE, NOT MODULE-LEVEL (PR #461 review, second pass, SHOULD-FIX 3 -- see
test_accounts_store.py's module docstring for the fuller rationale). The REAL, argon2-backed
`written_artifacts` fixture below (gated -- `main(["users", "add", ...])` really hashes the
password with argon2) is the primary, fully-realistic proof. `written_artifacts_no_argon2` is an
UNGATED counterpart: the property under test here -- which files did the CLI write, and is the
plaintext password absent from every one of them plus stdout/stderr -- is a property of the CLI's
I/O plumbing (argument parsing, file writing, printing), not of argon2's cryptographic strength, so
it is proven again with `hashing.hash_password` swapped for a fast, non-reversible SHA-256-based
fake, which needs no real argon2 install at all. The fake is deliberately NOT a naive
`"FAKE:" + password` concatenation -- that WOULD trivially embed the plaintext into the "hash" and
make the ungated leak-check vacuous, exactly the kind of test-weakening the project's ABSENT!=PASS
rule exists to catch.
"""
import hashlib

import pytest

from insight.__main__ import main
from insight.accounts import hashing

PASSWORD = "Tr0ub4dor&3-the-plaintext-needle"


@pytest.fixture
def written_artifacts(tmp_path, monkeypatch, capsys):
    """Gated -- HERE, in the fixture itself, not in the tests that consume it: fixtures run during
    test SETUP, before the test function's own body (and any `pytest.importorskip` inside it)
    executes, so gating only the test bodies would let this fixture's real `main()` call run
    first, fail with a non-zero exit (KDFUnavailableError), and blow up as an ERROR instead of a
    clean SKIP on a machine without argon2-cffi. Snapshots tmp_path BEFORE calling
    `insight users add`, diffs AFTER, to get exactly the set of artifacts the CLI actually wrote --
    not an assumed single file. Returns (artifact_paths, stdout, stderr)."""
    pytest.importorskip("argon2")
    monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", hashing.TEST_PARAMS)
    monkeypatch.chdir(tmp_path)

    prompts = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    before = set(tmp_path.rglob("*"))
    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 0
    after = set(tmp_path.rglob("*"))
    new_paths = [p for p in (after - before) if p.is_file()]

    out, err = capsys.readouterr()
    return new_paths, out, err


@pytest.fixture
def written_artifacts_no_argon2(tmp_path, monkeypatch, capsys):
    """Ungated -- see module docstring. Identical shape to `written_artifacts` above, except
    `hashing.hash_password` is replaced with a fast, non-reversible SHA-256-based fake instead of
    calling into real argon2, so the CLI's artifact-enumeration and no-leak plumbing has regression
    coverage on every machine, including one without argon2-cffi installed (this one, as of
    implementation)."""

    def fake_hash_password(password, params=None):
        return "FAKEHASH$" + hashlib.sha256(password.encode("utf-8")).hexdigest()

    monkeypatch.setattr(hashing, "hash_password", fake_hash_password)
    monkeypatch.chdir(tmp_path)

    prompts = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))

    before = set(tmp_path.rglob("*"))
    code = main(["users", "add", "--username", "alice", "--role", "manager"])
    assert code == 0
    after = set(tmp_path.rglob("*"))
    new_paths = [p for p in (after - before) if p.is_file()]

    out, err = capsys.readouterr()
    return new_paths, out, err


def _assert_password_absent(raw_bytes, label):
    """The SHARED assertion both the gated and ungated positive tests below rely on -- so they can
    never drift into checking different things."""
    assert PASSWORD.encode("utf-8") not in raw_bytes, "plaintext password leaked via %s" % label


# =================================================================================== gated: real argon2 hashing


def test_no_new_artifact_is_empty_would_be_a_bug_not_a_pass(written_artifacts):
    """Structural precondition check: a grep test over an EMPTY artifact set would pass
    vacuously, proving nothing. This guard is load-bearing, not decorative -- see the negative
    control in this file's own docstring reasoning and .sdlc/plans/306.md's negative-controls
    table."""
    new_paths, _out, _err = written_artifacts
    assert new_paths, "insight users add wrote no new artifact -- the grep test below would pass vacuously"


def test_password_absent_from_every_written_artifact(written_artifacts):
    new_paths, _out, _err = written_artifacts
    for path in new_paths:
        _assert_password_absent(path.read_bytes(), str(path))


def test_password_absent_from_stdout_and_stderr(written_artifacts):
    _new_paths, out, err = written_artifacts
    _assert_password_absent(out.encode("utf-8"), "stdout")
    _assert_password_absent(err.encode("utf-8"), "stderr")


# =================================================================================== ungated: CLI plumbing, fake hash function


def test_no_new_artifact_is_empty_would_be_a_bug_not_a_pass_ungated(written_artifacts_no_argon2):
    new_paths, _out, _err = written_artifacts_no_argon2
    assert new_paths, "insight users add wrote no new artifact -- the grep test below would pass vacuously"


def test_password_absent_from_every_written_artifact_ungated(written_artifacts_no_argon2):
    new_paths, _out, _err = written_artifacts_no_argon2
    for path in new_paths:
        _assert_password_absent(path.read_bytes(), str(path))


def test_password_absent_from_stdout_and_stderr_ungated(written_artifacts_no_argon2):
    _new_paths, out, err = written_artifacts_no_argon2
    _assert_password_absent(out.encode("utf-8"), "stdout")
    _assert_password_absent(err.encode("utf-8"), "stderr")


def test_negative_control_proves_the_leak_methodology_has_teeth(tmp_path):
    """Ungated -- not shipped-code coverage, exists solely to prove `_assert_password_absent` is
    falsifiable, not a tautology that would pass against anything. Plants the plaintext password
    in a fixture file and asserts the SAME helper correctly reports it as a leak."""
    leaky_file = tmp_path / "leaky.json"
    leaky_file.write_text('{"password_hash": "%s"}' % PASSWORD, encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_password_absent(leaky_file.read_bytes(), str(leaky_file))
