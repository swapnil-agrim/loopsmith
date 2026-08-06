# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""KDF unit tests for insight.accounts.hashing (issue #306 [E18.S1]).

GATING IS PER-FUNCTION, NOT MODULE-LEVEL (PR #461 review, second pass, SHOULD-FIX 3 -- mirroring
test_cli_users.py's own established pattern; see test_accounts_store.py's module docstring for the
fuller rationale). `insight.accounts.hashing` always imports cleanly regardless of whether argon2
is installed (guarded `try/except ImportError` at module level -- see hashing.py's own docstring),
so only the tests that actually call `hash_password`/`verify_password` to do REAL hashing need
`pytest.importorskip("argon2")`, called inside the function body. `test_production_params_meet_a_
security_floor` (a plain namedtuple attribute check) and `test_verify_uses_librarys_constant_time_
compare_not_bare_equality` (a source/AST scan of hashing.py's text) touch no hashing at runtime at
all and are UNGATED. The one hashing-adjacent test that does NOT need real argon2 for a different
reason -- the KDF-unavailable seam test -- does not live here at all; see
test_accounts_hashing_kdf_unavailable.py, which is deliberately its own file so it runs (not skips)
on a machine without argon2-cffi installed.
"""
import time

import pytest

from insight.accounts import hashing


def test_hash_then_verify_roundtrip_succeeds():
    pytest.importorskip("argon2")
    encoded = hashing.hash_password("correct horse battery staple", params=hashing.TEST_PARAMS)
    assert hashing.verify_password("correct horse battery staple", encoded) is True


def test_wrong_password_fails_verify():
    pytest.importorskip("argon2")
    encoded = hashing.hash_password("correct horse battery staple", params=hashing.TEST_PARAMS)
    assert hashing.verify_password("not the right password", encoded) is False


def test_hash_output_never_contains_the_plaintext_password():
    pytest.importorskip("argon2")
    password = "correct horse battery staple"
    encoded = hashing.hash_password(password, params=hashing.TEST_PARAMS)
    assert password not in encoded


def test_production_params_meet_a_security_floor():
    """Ungated -- fast, no hashing at all -- catches a future edit that quietly weakens the
    shipped constants (.sdlc/plans/306.md Decision 8)."""
    assert hashing.PRODUCTION_PARAMS.memory_cost >= 65536
    assert hashing.PRODUCTION_PARAMS.time_cost >= 2
    assert hashing.PRODUCTION_PARAMS.parallelism >= 1


def test_production_params_actually_hash_and_verify_for_real():
    """Gated. The ONE test in the whole suite that runs a real hash+verify round trip using the
    actual PRODUCTION_PARAMS, unmocked. Bounded with a generous wall-clock ceiling that exists only
    to catch a pathological misconfiguration, not to assert anything about comparative timing --
    see .sdlc/plans/306.md Decision 8. The ceiling was set from a real measurement taken on this
    machine during implementation (see the implementation report for the exact number); it is not
    a guess."""
    pytest.importorskip("argon2")
    password = "correct horse battery staple"
    start = time.perf_counter()
    encoded = hashing.hash_password(password, params=hashing.PRODUCTION_PARAMS)
    assert hashing.verify_password(password, encoded) is True
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, "production-parameter hash+verify round trip took %.3fs" % elapsed


def test_verify_uses_librarys_constant_time_compare_not_bare_equality():
    """Ungated -- a pure source/AST scan of hashing.py's own text; touches no hashing at runtime,
    so it needs no argon2 install to mean something. Source-scans hashing.py's text for a bare
    `==`/`!=` comparing a hash-like local against encoded_hash inside verify_password's body,
    asserting none exists, and asserts `PasswordHasher().verify(` appears in the function --
    mirroring test_cli.py's AST-based style. See .sdlc/plans/306.md Decision 6."""
    import ast
    import pathlib

    source_path = pathlib.Path(hashing.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert "PasswordHasher().verify(" in source or "PasswordHasher()\n" in source, (
        "verify_password must call PasswordHasher().verify(...)"
    )

    tree = ast.parse(source, filename=str(source_path))
    verify_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "verify_password":
            verify_fn = node
            break
    assert verify_fn is not None, "verify_password function not found"

    for node in ast.walk(verify_fn):
        if isinstance(node, ast.Compare):
            for op in node.ops:
                assert not isinstance(op, (ast.Eq, ast.NotEq)), (
                    "verify_password must not use a bare ==/!= comparison -- that reintroduces "
                    "the timing side-channel argon2's own verify() avoids"
                )
