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

WEAK-EMBEDDED-PARAMETER HANDLING (PR #461 review, third pass BLOCKING -- a well-formed hash with
weak parameters is a timing oracle, following the first pass's BLOCKING 2 and the second pass's
BLOCKING 1, both about a malformed `password_hash` field; this is a THIRD, independent way one
account record's hash can be untrustworthy without the store's JSON breaking). Argon2's own
`PasswordHasher().verify()` reads its cost parameters (`m=`/`t=`/`p=`) OUT OF `encoded_hash` itself
-- the hasher instance's own configured `time_cost`/`memory_cost`/`parallelism` are irrelevant to
which parameters the KDF actually runs with (see `verify_password`'s own docstring). That means a
SYNTACTICALLY VALID argon2id hash carrying cheap parameters (e.g. `m=8,t=1,p=1` instead of the
account's real `m=65536,t=3,p=4`) does not raise anything at all -- it runs a now-trivial KDF and
returns a clean `True`/`False`. A reviewer measured this live: a wrong password against a
weak-parameter record cost 0.0001s, against a normal record 0.0234s, and against an unknown user
0.0238s -- a 262x gap, though every path raised the identical `InvalidCredentials` with the
identical message.

`verify_password` closes this the same way `store.verify_user` already closes every other
malformed-record shape: by refusing to trust ANYTHING about a hash whose embedded parameters do not
match the caller's `params` (`PRODUCTION_PARAMS` by default, resolved dynamically -- see
`hash_password`'s own docstring for why that resolution happens inside the function body). A
parameter mismatch is treated as `CorruptHashError` -- the exact same store-level "this record
cannot be trusted" signal a garbled hash string or a wrong-typed field already raises -- which
`store.verify_user`'s existing `except Exception` boundary already folds into `InvalidCredentials`
and pays a real, `PRODUCTION_PARAMS`-strength dummy-hash KDF operation for (no changes needed in
`store.py` beyond its own docstring, which claimed the dummy-hash fallback fires on "ANY exception
raised while processing this known user's record" -- true after this fix, but was NOT true before
it for this one shape, since a weak-param hash raised nothing to catch).

The parameter check itself (`_embedded_cost_params`) is deliberately pure `re`-stdlib string
parsing, with NO dependency on `argon2` being importable at all -- a second testable seam, alongside
the `argon2 = None` one below, so the extraction/comparison logic has real unit coverage on a
machine that lacks argon2-cffi entirely (this one, as of this fix). It inspects ONLY the cost
parameters (`m=`/`t=`/`p=`) embedded in the PHC-format hash string -- never `salt_len`/`hash_len`/
the argon2 variant (`id` vs `i` vs `d`) also embedded there -- because cost parameters are the only
embedded property that materially changes `verify()`'s wall-clock cost; the others affect output
size/format, not KDF work, so they are out of THIS check's scope (a mismatch there still very
likely fails verification for unrelated reasons, just not via this path).
"""
import collections
import re

try:
    import argon2
except ImportError:  # pragma: no cover - exercised via the monkeypatch seam, not a real absence
    argon2 = None


class KDFUnavailableError(Exception):
    """Raised by `hash_password`/`verify_password` when `argon2` could not be imported. The
    message names the package and the fix -- never a bare traceback, never silence (see the
    module docstring's "testable seam" note)."""


class CorruptHashError(Exception):
    """Raised by `verify_password` when `encoded_hash` is not a well-formed argon2 hash -- e.g. a
    single account record's `password_hash` field was mangled by a partial write, a bad migration,
    or a hand-edit, while the rest of the store stays valid JSON (PR #461 review, BLOCKING 2) --
    OR when `encoded_hash` parses fine but embeds different cost parameters than the ones currently
    in force (PR #461 review, third pass BLOCKING -- see the module docstring's
    WEAK-EMBEDDED-PARAMETER HANDLING note): a well-formed hash under stale/weak parameters is just
    as untrustworthy as a garbled one, since verifying against it would run a cheaper-than-expected
    KDF and leak that fact through timing. Distinct from a wrong-password mismatch
    (`VerifyMismatchError`, handled separately inside `verify_password` and turned into a plain
    `False` return, never an exception) and from `KDFUnavailableError` (argon2 itself unavailable,
    checked before any hash is ever touched).

    `insight.accounts.store.verify_user` catches this and folds it into the exact same
    `InvalidCredentials` exception and message it already uses for a wrong password and an unknown
    username -- it is never re-raised to `verify_user`'s own caller as a distinguishable type, and
    the caller-facing behavior for "this specific known user's record is corrupt" is deliberately
    indistinguishable from "wrong password" or "no such user" (done-when 3). See `store.py`'s
    `verify_user` docstring for exactly how, including why that path pays a second, dummy-hash KDF
    verification rather than becoming a timing fast path."""


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


#: Matches the `$m=<memory_cost>,t=<time_cost>,p=<parallelism>$` segment of a standard PHC-format
#: argon2 encoded hash string (e.g. `$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`). Pure `re`
#: stdlib, no argon2-cffi dependency -- see `_embedded_cost_params`'s own docstring and the module
#: docstring's WEAK-EMBEDDED-PARAMETER HANDLING note for why that independence is load-bearing.
_COST_PARAMS_RE = re.compile(r"\$m=(\d+),t=(\d+),p=(\d+)\$")


def _embedded_cost_params(encoded_hash):
    """Parse the `m=`/`t=`/`p=` argon2 cost parameters embedded in `encoded_hash`'s own PHC-format
    string, returning a `Params`. Pure `re`-stdlib string parsing -- deliberately does NOT import
    or call anything from `argon2`, so this function (and the parameter-mismatch check that uses
    it, inside `verify_password`) is unit-testable on a machine that lacks argon2-cffi entirely
    (this one, as of this fix), exactly like the `argon2 = None` seam above.

    Raises `CorruptHashError` -- never lets a `TypeError`/`AttributeError` from a non-string
    `encoded_hash`, or a silent `None` from a failed regex match, escape as some third,
    caller-distinguishable exception type -- if `encoded_hash` is not a string, or is a string with
    no well-formed `$m=...,t=...,p=...$` segment. Both cases mean "the embedded parameters cannot
    be trusted," which is exactly the same bucket a garbled or wrongly-typed `password_hash` value
    already falls into elsewhere in this module and in `store.verify_user`."""
    if not isinstance(encoded_hash, str):
        raise CorruptHashError(
            "encoded_hash must be a string to read its embedded KDF parameters, got %s"
            % type(encoded_hash).__name__
        )
    match = _COST_PARAMS_RE.search(encoded_hash)
    if not match:
        raise CorruptHashError(
            "could not find argon2 m=/t=/p= cost parameters embedded in the hash string"
        )
    memory_cost, time_cost, parallelism = (int(group) for group in match.groups())
    return Params(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)


def _require_matching_cost_params(encoded_hash, params):
    """Raise `CorruptHashError` unless `encoded_hash`'s own embedded cost parameters equal
    `params` exactly. Deliberately a SEPARATE function from `verify_password`, not inlined there,
    so the `!=` comparison below -- a plain comparison of small integers, never of secret/derived
    material, so it carries no timing signal about the password -- lives outside
    `verify_password`'s own function body. `verify_password`'s own AST is scanned elsewhere
    (test_accounts_hashing.py's `test_verify_uses_librarys_constant_time_compare_not_bare_equality`)
    for exactly this operator, as a blunt guard against the PASSWORD/HASH comparison ever
    regressing to a bare `==`/`!=`; keeping this unrelated, cleartext parameter comparison in its
    own function avoids that guard flagging a comparison it was never meant to catch."""
    embedded = _embedded_cost_params(encoded_hash)
    if embedded != params:
        raise CorruptHashError(
            "hash embeds KDF parameters %r but %r are currently in force -- refusing to trust a "
            "well-formed hash under different-than-expected parameters" % (embedded, params)
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


def verify_password(password, encoded_hash, params=None):
    """Verify `password` against `encoded_hash` using argon2-cffi's own constant-time compare
    (`PasswordHasher().verify(...)` -- never a bare `==`/`!=` string comparison, which would
    reintroduce the timing side-channel argon2's own verify() avoids). `params` defaults to
    `PRODUCTION_PARAMS`, resolved HERE inside the function body (never a bound default -- same
    reason as `hash_password`, see the module docstring) -- it names the cost parameters this
    caller EXPECTS `encoded_hash` to have been produced under.

    BEFORE doing any cryptographic work, this function checks `encoded_hash`'s OWN embedded cost
    parameters against `params` (`_require_matching_cost_params` -- a separate function, not
    inlined here, so the equality check itself stays outside this docstring's own "never a bare
    `==`/`!=`" guarantee and the AST scan that enforces it; see that function's own docstring) and
    raises `CorruptHashError` on a mismatch (PR #461 review, third pass BLOCKING -- see the module
    docstring's WEAK-EMBEDDED-PARAMETER HANDLING note for the full exploit and why). This is necessary because,
    for the actual argon2 comparison below, the hasher's own configured parameters
    (time_cost/memory_cost/parallelism) are IRRELEVANT to what the KDF actually runs with --
    `verify()` reads the real parameters back out of `encoded_hash` itself, so a
    default-constructed `PasswordHasher()` "correctly" verifies a hash produced under ANY parameter
    set, including a trivially cheap one an attacker (or a bad migration) planted. Without the
    check above, that "correctness" is exactly what turns a well-formed-but-weak hash into a 260x+
    timing oracle: the KDF would run, but for a fraction of the expected cost.

    Returns True/False for a well-formed hash embedding the EXPECTED parameters; raises
    `CorruptHashError` for a malformed hash, one embedding unexpected parameters, or an
    `encoded_hash` that is not even a string (a store-level concern, not a wrong password -- see
    `CorruptHashError`'s own docstring for exactly how `insight.accounts.store.verify_user` handles
    it, which is NOT by exposing it to its own caller as a distinguishable type; PR #461 review,
    BLOCKING 2, correcting an earlier version of this docstring that claimed a distinguishing
    `try/except` existed in store.py when it did not)."""
    _require_argon2()
    params = params or PRODUCTION_PARAMS
    _require_matching_cost_params(encoded_hash, params)
    hasher = argon2.PasswordHasher()
    try:
        hasher.verify(encoded_hash, password)
    except argon2.exceptions.VerifyMismatchError:
        return False
    except (argon2.exceptions.InvalidHashError, argon2.exceptions.VerificationError) as e:
        # Order matters: VerifyMismatchError IS-A VerificationError in argon2-cffi's own
        # hierarchy, so it must be caught first (above), or this clause would silently also
        # swallow a wrong password. A malformed/corrupt stored hash is a distinct, store-level
        # concern -- wrapped in CorruptHashError (never argon2's own exception type, which would
        # leak an argon2-cffi implementation detail across the module boundary) so
        # insight.accounts.store can catch ONE hashing-module-owned type regardless of which of
        # argon2-cffi's own exception classes actually fired.
        raise CorruptHashError(str(e)) from e
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
