# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The KDF-unavailable seam test (issue #306 [E18.S1], .sdlc/plans/306.md Decision 6).

DELIBERATELY a SEPARATE FILE, with NO `pytest.importorskip("argon2")` anywhere in it, module
level or otherwise. `insight.accounts.hashing` always imports cleanly regardless of whether the
real `argon2` package is installed (it guards the import in a `try/except ImportError`, binding
`argon2 = None` on failure) -- so this test needs no real KDF operation, only the module import
plus a monkeypatch, and must therefore actually RUN -- not skip -- on every machine, including one
that genuinely lacks argon2-cffi.

THIS IS THE ONE TEST IN THE WHOLE HASHING SURFACE THAT MUST EXECUTE, NOT SKIP, ON A MACHINE
WITHOUT ARGON2-CFFI INSTALLED -- that is precisely the condition its own reason for existing is
about. Had it been added to test_accounts_hashing.py's module-level guard instead, this whole
file would report SKIPPED on such a machine and the seam would only ever run where it's least
needed (a machine that already has argon2-cffi). See insight/tests/test_cli_users.py for this
same seam's CLI-level counterpart, similarly ungated.
"""
import insight.accounts.hashing as hashing


def test_kdf_unavailable_raises_actionable_error_via_seam(monkeypatch):
    monkeypatch.setattr(hashing, "argon2", None)

    try:
        hashing.hash_password("irrelevant")
    except hashing.KDFUnavailableError as exc:
        assert "argon2-cffi" in str(exc)
        assert "pip install" in str(exc)
    else:
        raise AssertionError("hash_password must raise KDFUnavailableError when argon2 is None")

    try:
        hashing.verify_password("irrelevant", "irrelevant-hash")
    except hashing.KDFUnavailableError as exc:
        assert "argon2-cffi" in str(exc)
        assert "pip install" in str(exc)
    else:
        raise AssertionError("verify_password must raise KDFUnavailableError when argon2 is None")
