# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Argon2id KDF wrapper (issue #306 [E18.S1], .sdlc/plans/306.md Decision 1/2/6/8).

`argon2` is imported in a guarded `try/except ImportError` at module level, binding the name
`None` on failure -- a TESTABLE SEAM (Decision 6), not a convenience: it lets a test
`monkeypatch.setattr(hashing, "argon2", None)` to deterministically exercise the
"KDF unavailable" path on ANY machine, including one that genuinely lacks argon2-cffi (this
machine, as of implementation). `hash_password`/`verify_password` check this name and raise
`KDFUnavailableError` -- a loud, actionable failure naming the package and the fix -- rather than
silently no-op or fall back to something weaker. See
insight/tests/test_accounts_hashing_kdf_unavailable.py, which must run (not skip) everywhere.

`PRODUCTION_PARAMS` mirrors argon2-cffi's own `PasswordHasher` defaults (time_cost=3,
memory_cost=65536 KiB [64 MiB], parallelism=4) -- OWASP's current argon2id baseline, verified live
against this exact version of argon2-cffi during plan review. `TEST_PARAMS` is deliberately weak
(time_cost=1, memory_cost=8, parallelism=1) so `insight/tests/` -- which reruns on EVERY future
goal via `.sdlc/config.json`'s `verify.command` -- does not pay production-strength KDF cost on
every test run (Decision 8). `hash_password`'s `params` argument defaults to `None` and is
resolved to `PRODUCTION_PARAMS` INSIDE the function body, not as a bound default -- a bare
`params=PRODUCTION_PARAMS` signature would bind the object at *definition* time and silently
ignore any later `monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", TEST_PARAMS)`, which is
exactly how the store/CLI/symmetry tests get fast parameters.

`DUMMY_HASH_PRODUCTION`/`DUMMY_HASH_TEST` are PRECOMPUTED, hand-generated constants (Decision 7)
-- never computed at runtime -- so `store.verify_user`'s unknown-user branch never pays a KDF hash
operation inside the request path (only a cheap value lookup via `dummy_hash_for`). Each was
generated once, interactively, on a machine with argon2-cffi installed, by calling
`hash_password("dummy-password-never-verified-against", params=<the matching PARAMS>)` and pasting
the resulting encoded hash string here.
"""
import collections

try:
    import argon2
except ImportError:  # pragma: no cover - exercised via the monkeypatch seam, not a real absence
    argon2 = None


class KDFUnavailableError(Exception):
    """Raised by `hash_password`/`verify_password` when `argon2` could not be imported. The
    message names the package and the fix -- never a bare traceback, never silence (see the
    module docstring's "testable seam" note)."""


#: A KDF parameter set. Plain attribute access (`.time_cost`, `.memory_cost`, `.parallelism`) --
#: no argon2-cffi type leaks into this tuple's shape.
Params = collections.namedtuple("Params", ("time_cost", "memory_cost", "parallelism"))

#: OWASP-baseline argon2id parameters -- argon2-cffi's own `PasswordHasher()` defaults, verified
#: live against this exact library version during plan review (time_cost=3, memory_cost=65536,
#: parallelism=4).
PRODUCTION_PARAMS = Params(time_cost=3, memory_cost=65536, parallelism=4)

#: Deliberately weak parameters, used by every functional test that only needs to observe
#: behavior (does verify succeed/fail, is the plaintext absent, etc.) -- see Decision 8.
TEST_PARAMS = Params(time_cost=1, memory_cost=8, parallelism=1)

#: Frozen snapshots, captured ONCE at import time, right after the public constants above are
#: defined. `dummy_hash_for` compares its `params` argument against THESE, never against the
#: public `PRODUCTION_PARAMS`/`TEST_PARAMS` names directly -- see the module docstring and
#: .sdlc/plans/306.md Decision 7 for why that distinction is load-bearing: a test that
#: `monkeypatch.setattr(hashing, "PRODUCTION_PARAMS", TEST_PARAMS)` (the standard speed pattern
#: used throughout insight/tests/test_accounts_*.py) makes the PUBLIC `PRODUCTION_PARAMS` name
#: hold the same value as `TEST_PARAMS` for the rest of that test. Comparing against the public
#: names directly would then make `params == PRODUCTION_PARAMS` and `params == TEST_PARAMS` both
#: true simultaneously, and an if/elif would deterministically but WRONGLY select
#: `DUMMY_HASH_PRODUCTION` (the real, slow, production-strength dummy) for a test that
#: monkeypatched specifically to avoid that cost.
_ORIGINAL_PRODUCTION_PARAMS = PRODUCTION_PARAMS
_ORIGINAL_TEST_PARAMS = TEST_PARAMS

#: Hand-generated once, on a machine with argon2-cffi 25.1.0 installed, via:
#:   hash_password("dummy-password-never-verified-against", params=PRODUCTION_PARAMS)
#: Generated under PRODUCTION_PARAMS (time_cost=3, memory_cost=65536, parallelism=4).
DUMMY_HASH_PRODUCTION = (
    "$argon2id$v=19$m=65536,t=3,p=4$00KQEfKiz5bH+KcgxVyZgg$"
    "Y5a5mpbLTHp+wT2b+IM5f6X1WZr1WQAGtsq1aL0yZno"
)

#: Hand-generated once, on a machine with argon2-cffi 25.1.0 installed, via:
#:   hash_password("dummy-password-never-verified-against", params=TEST_PARAMS)
#: Generated under TEST_PARAMS (time_cost=1, memory_cost=8, parallelism=1).
DUMMY_HASH_TEST = (
    "$argon2id$v=19$m=8,t=1,p=1$CW5aKR2QM53bDQ3ioGIB2A$"
    "ChkoyPdpctVMxRMZBca+R/gsAQwFQF1Bl6GVI23s+74"
)


def _require_argon2():
    if argon2 is None:
        raise KDFUnavailableError(
            "argon2-cffi is required to hash/verify passwords but is not installed. "
            "Install it with: pip install argon2-cffi"
        )


def hash_password(password, params=None):
    """Hash `password` with argon2id, returning the encoded hash string (parameters embedded).
    `params` defaults to `PRODUCTION_PARAMS`, resolved HERE (inside the function body) rather
    than as a bound default -- see the module docstring."""
    _require_argon2()
    params = params or PRODUCTION_PARAMS
    hasher = argon2.PasswordHasher(
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
    )
    return hasher.hash(password)


def verify_password(password, encoded_hash):
    """Verify `password` against `encoded_hash` using argon2-cffi's own constant-time compare
    (`PasswordHasher().verify(...)` -- never a bare `==`/`!=` string comparison, which would
    reintroduce the timing side-channel argon2's own verify() avoids). The hasher's own
    parameters (time_cost/memory_cost/parallelism) are irrelevant here -- `verify()` reads the
    real parameters back out of `encoded_hash` itself, so a default-constructed `PasswordHasher()`
    correctly verifies a hash produced under ANY parameter set. Returns True/False for a
    well-formed hash; raises for a malformed one (a store-level concern, not a wrong password)."""
    _require_argon2()
    hasher = argon2.PasswordHasher()
    try:
        hasher.verify(encoded_hash, password)
    except argon2.exceptions.VerifyMismatchError:
        return False
    except (argon2.exceptions.InvalidHashError, argon2.exceptions.VerificationError):
        # Order matters: VerifyMismatchError IS-A VerificationError in argon2-cffi's own
        # hierarchy, so it must be caught first (above), or this clause would silently also
        # swallow a wrong password. A malformed/corrupt stored hash is a distinct, store-level
        # concern -- re-raised as-is; insight.accounts.store catches this to distinguish a
        # corrupt record from a genuine credential mismatch.
        raise
    return True


def dummy_hash_for(params):
    """Return the precomputed dummy hash matching `params`, by VALUE comparison against the
    frozen import-time snapshots (never the public, monkeypatchable names -- see the module
    docstring). Zero KDF cost: a value comparison and a constant lookup, every time, including
    the very first call after process start (Decision 7's cold-start closure). Raises
    `ValueError` for any `params` that matches neither known set -- a defensive floor against a
    future third parameter set silently falling through to an inline runtime hash."""
    if params == _ORIGINAL_PRODUCTION_PARAMS:
        return DUMMY_HASH_PRODUCTION
    if params == _ORIGINAL_TEST_PARAMS:
        return DUMMY_HASH_TEST
    raise ValueError(
        "dummy_hash_for: no precomputed dummy hash for params=%r -- add one rather than "
        "falling through to an inline hash_password() call in the request path" % (params,)
    )
