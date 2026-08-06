# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Done-when 2's own required test: `insight users add` never echoes, logs, or stores the
password in plaintext anywhere -- asserted by a test that greps every artifact the CLI actually
wrote, including stdout/stderr (issue #306 [E18.S1], .sdlc/plans/306.md Task D).

Mirrors insight/tests/test_dash_ic_no_leak.py's own structure and stated purpose: assertions run
against the RAW BYTES of every written artifact, and a shared assertion helper is proven
falsifiable via a shipped, permanent negative control.

MODULE-LEVEL `pytest.importorskip("argon2")`: the fixture below really calls
`main(["users", "add", ...])`, which really hashes a password -- needs a real argon2 install.
"""
import pytest

argon2 = pytest.importorskip("argon2")

from insight.__main__ import main  # noqa: E402
from insight.accounts import hashing  # noqa: E402

PASSWORD = "Tr0ub4dor&3-the-plaintext-needle"


@pytest.fixture
def written_artifacts(tmp_path, monkeypatch, capsys):
    """Snapshots tmp_path BEFORE calling `insight users add`, diffs AFTER, to get exactly the
    set of artifacts the CLI actually wrote -- not an assumed single file. Returns
    (artifact_paths, stdout, stderr)."""
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


def test_no_new_artifact_is_empty_would_be_a_bug_not_a_pass(written_artifacts):
    """Structural precondition check: a grep test over an EMPTY artifact set would pass
    vacuously, proving nothing. This guard is load-bearing, not decorative -- see the negative
    control in this file's own docstring reasoning and .sdlc/plans/306.md's negative-controls
    table."""
    new_paths, _out, _err = written_artifacts
    assert new_paths, "insight users add wrote no new artifact -- the grep test below would pass vacuously"


def _assert_password_absent(raw_bytes, label):
    """The SHARED assertion the positive tests below rely on -- so the positive and negative
    tests can never drift into checking different things."""
    assert PASSWORD.encode("utf-8") not in raw_bytes, "plaintext password leaked via %s" % label


def test_password_absent_from_every_written_artifact(written_artifacts):
    new_paths, _out, _err = written_artifacts
    for path in new_paths:
        _assert_password_absent(path.read_bytes(), str(path))


def test_password_absent_from_stdout_and_stderr(written_artifacts):
    _new_paths, out, err = written_artifacts
    _assert_password_absent(out.encode("utf-8"), "stdout")
    _assert_password_absent(err.encode("utf-8"), "stderr")


def test_negative_control_proves_the_leak_methodology_has_teeth(tmp_path):
    """Not shipped-code coverage -- exists solely to prove `_assert_password_absent` is
    falsifiable, not a tautology that would pass against anything. Plants the plaintext password
    in a fixture file and asserts the SAME helper correctly reports it as a leak."""
    leaky_file = tmp_path / "leaky.json"
    leaky_file.write_text('{"password_hash": "%s"}' % PASSWORD, encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_password_absent(leaky_file.read_bytes(), str(leaky_file))
