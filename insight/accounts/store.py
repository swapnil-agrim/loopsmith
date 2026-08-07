# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The accounts store: read/write `.sdlc/insight-accounts.json` (issue #306 [E18.S1],
.sdlc/plans/306.md Decision 3/7/9).

`resolve_accounts_path` is a direct structural mirror of `insight/ingest/store.py`'s
`resolve_db_path` -- resolved relative to CWD at CALL time, never at import time, so this
subpackage stays importable and testable with no assumption about where `insight/` itself lives
(insight/ must stay extractable -- no import of anything at the repo root).

`add_user(username, password, role, accounts_path=None) -> None` and
`verify_user(username, password, accounts_path=None) -> str` (returns the role on success) are
the two public entry points -- deliberately free of any KDF parameter or hashing detail.

SYMLINK/TOCTOU HARDENING (PR #461 review, first pass BLOCKING 1; the read side hardened further in
a second pass, BLOCKING 2 below -- both tracked under the same issue). Neither the read nor the
write path silently follows a symlink at the accounts-store path itself, but they do it two
DIFFERENT ways, deliberately: the write path calls `_refuse_if_symlink` (a separate
`Path.is_symlink()` check) before it ever opens anything, because its actual write always goes
through a same-directory temp file plus `os.replace()` -- a destination that `replace()`'s own
POSIX `rename()` semantics never dereference, so the separate check is a courtesy for the
ALREADY-symlinked case, not the thing standing between the write and the exploit. The read path
CANNOT rely on the same courtesy-check-then-act shape, because a plain read has no second atomic
step to fall back on the way `replace()` does for the write -- so `_read_accounts` opens the file
descriptor exactly once with `O_NOFOLLOW` and reads from that fd, never a separate
`is_symlink()` / `exists()` / `read_text()` sequence (that three-step, check-then-act shape was
itself the second review's finding: a reviewer swapped the store for a symlink in the window
between the check and the read and got a planted admin record trusted as real content -- see
`_read_accounts`'s own docstring for exactly how the fix closes that window, and its own docstring
for what it does NOT close: a symlinked or attacker-writable ANCESTOR directory, which no leaf-level
guard can fix). The write path's temp file (`_open_fresh_temp_file`) is opened with a fresh,
unpredictable, per-call random name via `O_CREAT | O_EXCL` (never a fixed, guessable name like
`.accounts.json.tmp`) plus `O_NOFOLLOW` where the platform supports it, so a symlink pre-planted at
a predictable temp path can no longer be written through, nor `os.replace()`d into place at the real
store path (the exact two-step exploit the review proved live). See
insight/tests/test_accounts_store.py's symlink-hijack tests, which plant the attack for real and
must go RED against the pre-hardening code.

CORRUPT-PER-RECORD-HASH HANDLING (PR #461 review, first-pass BLOCKING 2; hardened further in the
second pass, BLOCKING 1 below, after the first pass's `except hashing.CorruptHashError` allowlist
was proven insufficient -- both tracked under the same issue). A single user's `password_hash`
field can be malformed independently of the rest of the store (a partial write, a bad migration, a
hand-edit) while the store's JSON stays perfectly valid -- this is NOT the same as
`AccountsStoreCorruptError` (which covers the store shape as a whole) and must not surface as some
third, caller-distinguishable exception type either. The first pass caught this only when argon2
itself raised `InvalidHashError`/`VerificationError` (a well-formed-JSON-but-garbage hash string);
a missing `password_hash` key (`KeyError`) or a non-string value (`AttributeError`/`TypeError` from
inside argon2-cffi's own internals) reached the caller as a different, distinguishable exception
type, raised roughly 1000x faster than a real KDF operation -- an allowlist of specific exception
types is not a fix for "any malformed record," it is a fix for "the malformed records this reviewer
happened to try." `verify_user` now wraps the WHOLE known-user branch in a single `except
Exception` boundary (deliberately not an allowlist -- see its own docstring) so that it folds into
the exact same `InvalidCredentials`/message as a wrong password or an unknown username, regardless of
which exception type the NEXT unpredicted malformed shape happens to raise -- see
`hashing.CorruptHashError` and `verify_user`'s own docstring for exactly how and why, including the
extra dummy-hash verification that keeps this path from becoming a timing fast-path.

Done-when 3 (message and timing indistinguishability): `verify_user` raises the SAME exception
type, `InvalidCredentials`, with the IDENTICAL message string, for both "user exists, wrong
password" and "user does not exist" -- no second exception type for a caller to `except`
selectively. `verify_user` always calls `hashing.verify_password` EXACTLY ONCE on every failure
path -- for a known user, against their real stored hash; for an unknown user, against the
precomputed dummy hash (`hashing.dummy_hash_for`, a value lookup, never an inline KDF operation)
-- the standard mitigation for user-enumeration-by-timing (see hashing.py's own docstring for why
the dummy hash is a constant, not computed lazily).

WEAK-EMBEDDED-KDF-PARAMETER HANDLING (PR #461 review, third pass BLOCKING -- a third, independent
way one user's `password_hash` can be untrustworthy without the store's JSON breaking, following
the first pass's BLOCKING 2 and the second pass's BLOCKING 1 above). A record's `password_hash` can
be a completely well-formed argon2id hash -- decodes fine, `hasher.verify()` runs without raising --
while still embedding cheap KDF parameters (e.g. `m=8,t=1,p=1`) instead of the real,
currently-in-force ones (e.g. `m=65536,t=3,p=4`), because argon2's own `verify()` reads its cost
parameters OUT OF the hash string itself, never from the verifying `PasswordHasher` instance's own
config. Neither of the first two passes' fixes catches this: nothing raises, `verify_password`
returns a clean `True`/`False`, and the malformed-record `except Exception` boundary in
`verify_user` below never fires because there is no exception to catch -- so this ONE path pays no
KDF-sized cost at all. A reviewer measured it live: 0.0001s for a wrong password against a
weak-parameter record, versus 0.0234s for a normal record and 0.0238s for an unknown user -- a 262x
gap, though the exception type and message were identical in all three. The fix lives in
`hashing.verify_password`, not here: it now checks `encoded_hash`'s OWN embedded cost parameters
against the caller's expected `params` BEFORE running any cryptographic work, and raises
`hashing.CorruptHashError` on a mismatch -- see `hashing.py`'s own module docstring
(WEAK-EMBEDDED-PARAMETER HANDLING) and `verify_password`'s docstring for exactly how. That
`CorruptHashError` then falls straight into the SAME `except Exception` boundary below that already
handles every other malformed-record shape, with no changes needed in this module: the dummy-hash
fallback call fires exactly as it does for a missing key or a non-string value, so a weak-parameter
record now costs the same one real, `PRODUCTION_PARAMS`-strength KDF operation as every other
failure path. This is also why the "calls `verify_password` a SECOND time... so it still pays a
real KDF-sized cost" claim in `verify_user`'s own docstring below is true only AS OF this fix -- a
prior version of that docstring made the same claim unconditionally, which was false for exactly
this one shape.

PERMISSION-DENIED AND FIFO HANDLING (PR #461 review, third pass SHOULD-FIX, same review as the
BLOCKING above but a robustness gap rather than a per-user timing leak -- both are GLOBAL
conditions of the store as a whole, not something that varies per account, so neither is an
enumeration oracle). `_read_accounts` now (a) catches `PermissionError` (e.g. the store file is
`chmod 000`) and turns it into the same `AccountsStoreCorruptError` a symlinked or unparseable store
already raises, rather than letting a raw `PermissionError` escape uncaught all the way through
`insight/__main__.py` (which has no dispatch clause for it) as a bare traceback; and (b) opens with
`O_NONBLOCK` in addition to `O_NOFOLLOW`, so that a FIFO planted at the accounts path can no longer
block `os.open()` forever waiting for a writer that will never arrive -- see `_read_accounts`'s own
docstring for both.

TRUST BOUNDARY (PR #461 review, second pass, SHOULD-FIX 4 -- distinct from the FIRST pass's own
SHOULD-FIX 4 below, about concurrent `add_user` invocations; the two reviews independently reused
the same finding number for different findings). Every guard in this module operates on the LEAF
path component only -- `_read_accounts`'s `O_NOFOLLOW` open and `_write_accounts`'s
`_refuse_if_symlink` both ask "is *this exact path* a symlink," never "is anything ABOVE this path
in the directory tree attacker-controlled." That is a deliberate, unfixed precondition, not an
oversight: `resolve_accounts_path` resolves relative to CWD AT CALL TIME (see its own docstring),
so this module already assumes the CWD and every ancestor directory above the accounts path (in
practice, `.sdlc/` and whatever contains it) are trustworthy -- the same precondition every
relative-path tool in this repo relies on (`insight/ingest/store.py`'s `resolve_db_path` included).
A leaf-level symlink guard cannot meaningfully extend past that precondition: if an ancestor
directory is itself a symlink, or writable by an attacker, the attacker does not need to plant a
symlink at the leaf at all -- they can simply write an ordinary, non-symlink file there containing
a planted admin account, and `O_NOFOLLOW` has no opinion whatsoever about an ordinary file (it only
ever rejects a symlink). Closing that would mean validating every ancestor directory's ownership
and permissions up to the filesystem root on every read/write, which is disproportionate machinery
for a single-operator local CLI's account store; a compromised or attacker-writable `.sdlc/` is
already a total-compromise scenario this module was never designed to survive.

CONCURRENT-INVOCATION LOCKING AND PER-ACCOUNT THROTTLING (issue #308 [E18.S3],
.sdlc/plans/308.md, Decisions 3/4/5/6). A new `_locked_accounts(path)` context manager holds an
exclusive `fcntl.flock` around the ENTIRE read-check-mutate-write cycle of both `add_user` and
`verify_user` -- not just `_write_accounts_data`'s own atomic temp-file swap, which says nothing
about two calls racing each other's READ. This closes the race `add_user`'s docstring used to
document as an accepted, unfixed limitation (see that docstring's own history) and, more
importantly, closes the same race against the new per-account throttle counters
(`failed_attempts`/`locked_until`, extra optional fields on each known user's record) that an
attacker firing concurrent guesses instead of sequential ones could otherwise use to under-count
failed attempts. `fcntl` is imported in a guarded `try/except ImportError` mirroring
`hashing.py:70-73`'s own `argon2 = None` seam; if unavailable, `_locked_accounts` raises a loud
`AccountsLockUnavailableError` rather than proceeding unlocked -- see that exception's own
docstring for why a silent no-op is unacceptable, and note that this now fires on EVERY
`verify_user` call (including a successful login, since success also resets the throttle
counters under the same lock), not merely `add_user`.

The throttle policy itself (5 consecutive failed attempts, a 15-minute lockout with no
extension on further attempts, full reset once the window passes, `store._now()` as an
injectable clock seam) is implemented entirely inside `verify_user` -- see that function's own
docstring for the full policy and, critically, for how a locked-out account stays
message/timing-indistinguishable from an unknown username (Decision 6): the locked response is
the SAME `InvalidCredentials`, and an attempt against an already-locked account still performs
the FULL locked read-modify-write (never a read-only early return) so it remains
indistinguishable by cost and filesystem side effect from the unknown-user path, which now also
touches a single, bounded, shared dummy counter (`_dummy_throttle_attempts`, top-level in the
accounts JSON, never per-username -- an unknown username never accumulates real per-account
state)."""
import contextlib
import datetime
import errno
import json
import os
import pathlib
import secrets
import sys
import time

from insight.accounts import hashing

# issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 -- imported in a guarded try/except
# ImportError, mirroring hashing.py:70-73's own `argon2 = None` seam EXACTLY: a TESTABLE seam (a
# test can `monkeypatch.setattr(store, "fcntl", None)` to deterministically exercise the
# "locking unavailable" path on any machine, including this repo's own POSIX dev/CI, which
# always has fcntl). `_locked_accounts` below checks this name and raises
# `AccountsLockUnavailableError` -- a loud, actionable failure -- rather than silently proceeding
# unlocked; see that exception's own docstring for why a silent no-op is unacceptable here.
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised via the monkeypatch seam, not a real absence
    fcntl = None

#: Resolved relative to CWD at call time (see resolve_accounts_path), never at import time --
#: mirrors insight/ingest/store.py's DEFAULT_DB_PATH exactly.
DEFAULT_ACCOUNTS_PATH = pathlib.Path(".sdlc") / "insight-accounts.json"

#: The single message string used for BOTH "wrong password" and "unknown user" -- done-when 3's
#: message-indistinguishability requirement. No second, more specific message exists anywhere in
#: this module.
_INVALID_CREDENTIALS_MESSAGE = "invalid username or password"

_FORMAT_VERSION = 1

#: issue #308 [E18.S3], PR #485 code-review finding -- the longest a caller will wait for the
#: store-global lock before giving up with AccountsLockUnavailableError. See
#: `_locked_accounts`'s docstring for the full reasoning. Sized well above a contended
#: single-operator CLI or a handful of simultaneous logins (the critical section is one ~23ms
#: KDF plus a small JSON rewrite) and well below the point where waiters pile up as pinned
#: `python3` processes. A module constant, not a literal, so a test can monkeypatch it tiny and
#: prove the timeout deterministically instead of waiting seconds.
_LOCK_TIMEOUT_SECONDS = 5.0

#: How often `_flock_bounded` retries while waiting. Short enough that an uncontended-by-the-
#: time-we-look lock is picked up promptly, long enough not to spin a CPU.
_LOCK_POLL_SECONDS = 0.005

#: issue #308 [E18.S3], .sdlc/plans/308.md Decision 5 -- 5 consecutive failed attempts locks the
#: account. "Consecutive" per Decision 5's own definition: any verify_user call for a known
#: username that does not return a role (wrong password, a malformed record, or a
#: correct-password attempt made while already locked) counts; a real success resets to 0.
_THROTTLE_THRESHOLD = 5

#: issue #308 [E18.S3], .sdlc/plans/308.md Decision 5 -- the lockout window, computed ONCE (as an
#: absolute `locked_until` timestamp) when the threshold-th failure lands, never extended by
#: further attempts during the window (see verify_user's own docstring for why extending would
#: be a self-inflicted denial-of-service an attacker who cannot guess the password could still
#: trigger just by continuing to poll).
_THROTTLE_LOCKOUT = datetime.timedelta(minutes=15)

#: issue #308 [E18.S3], .sdlc/plans/308.md Decision 6.2 -- a single, bounded, shared sentinel
#: counter (top-level in the accounts JSON, never per-username) that the unknown-user branch of
#: verify_user increments under the SAME lock as a known user's own throttle write, so the two
#: remain indistinguishable by cost/filesystem side effect. Its VALUE is never consulted to
#: change behavior or the message -- only its read+increment+write SHAPE matters. Leading
#: underscore (unlike "version"/"users") flags it as an internal bookkeeping key, not part of
#: the public accounts-store shape any caller should read.
_DUMMY_THROTTLE_KEY = "_dummy_throttle_attempts"


class InvalidCredentials(Exception):
    """Raised by `verify_user` for a wrong password, an unknown username, a malformed record, OR
    a locked-out account (issue #308 [E18.S3]) -- the SAME exception type, with the SAME message,
    in every case (done-when 3, extended by Decision 6 to also cover "locked")."""


class AccountsStoreCorruptError(Exception):
    """Raised when the accounts file exists but cannot be parsed as a valid accounts store.
    The message names only the file PATH -- never raw file bytes, so a corrupt store's contents
    (which could themselves be a botched write containing partial secret material) never surface
    through an exception message."""


class UsernameExistsError(Exception):
    """Raised by `add_user` when the given username already has an account."""


class AccountsLockUnavailableError(Exception):
    """Raised for the TWO distinct reasons the accounts-store lock can be unusable (issue #308
    [E18.S3]). Both mean the same thing to a caller -- the credential check could not run, which
    is never "invalid credentials" -- so they share one exception type and one exit code, but an
    operator triaging them needs to know which fired, and the message says so.

    CAUSE 1, `fcntl` could not be imported -- this process is not running on a POSIX platform
    (.sdlc/plans/308.md Decision 4). Never fires on this repo's POSIX CI and dev; see below.

    CAUSE 2, the wait for the lock TIMED OUT (`_LOCK_TIMEOUT_SECONDS`, PR #485 code review). This
    one CAN fire in practice, under ordinary contention on a healthy POSIX host: the lock is
    store-global and held across a full KDF on every login attempt, so a flood of login POSTs
    queues behind it. See `_locked_accounts`'s docstring for why the wait is bounded, and issue
    #490 for the residual this does not remove.

    Cause 1's reasoning, unchanged. A SILENT
    no-op lock would make the throttle counter (and add_user's own concurrency-safety) APPEAR
    present and tested while providing zero actual protection against the exact concurrent
    read-modify-write race this lock exists to close, on any platform lacking `fcntl` -- so this
    fails LOUD instead: every operation that needs the lock refuses outright rather than
    proceeding unlocked.

    THIS FIRES ON EVERY `verify_user` CALL, NOT JUST `add_user` (stated plainly, not resting on
    `add_user`'s narrower precedent -- add_user is a rarely-invoked CLI action; verify_user is the
    ordinary login path, and a SUCCESSFUL login still takes this lock, because a successful login
    still resets the throttle counters under it, per Decision 5). On a platform without `fcntl`
    (Windows), this decision turns ordinary web login into a hard failure, not merely `insight
    users add`. CI (`ubuntu-latest`) and this repo's own local dev (`darwin`) are both POSIX, so
    this never fires in practice today; it is a fail-closed floor for the day it might. If
    Windows support is ever required, the fix is a portable lock (`msvcrt.locking`, or a
    lock-file protocol), NOT relaxing this to a no-op."""


def resolve_accounts_path(path=None):
    """Resolve the accounts store path: `path` if given, else `DEFAULT_ACCOUNTS_PATH`. Returns a
    `pathlib.Path`, not yet created or opened. Direct structural mirror of
    insight/ingest/store.py's `resolve_db_path`."""
    return pathlib.Path(path) if path is not None else DEFAULT_ACCOUNTS_PATH


#: How many fresh random temp-file names `_open_fresh_temp_file` will try before giving up. A real
#: collision this many times in a row does not happen by chance (see `_open_fresh_temp_file`'s own
#: docstring) -- it most likely means something is actively fighting this process for the
#: directory, so failing loudly beats looping forever.
_MAX_TEMP_FILE_ATTEMPTS = 64


def _refuse_if_symlink(path):
    """Refuse loudly if `path` is itself a symlink, rather than silently writing through it
    (PR #461 review, BLOCKING 1; used by `_write_accounts` only -- `_read_accounts` uses its own
    single-syscall `O_NOFOLLOW` open instead, see that function's own docstring for why a
    check-then-act call like this one is not safe to reuse for the read path). `Path.is_symlink()`
    never follows the link -- it asks about the dentry at `path` itself -- so this is safe to call
    even when the link's target does not exist (a dangling symlink must be refused too, not
    misread as "no accounts yet"). Writing through a symlinked store path would clobber whatever
    process-writable file the link points at; this check is a courtesy for the ALREADY-symlinked
    case (see the module docstring's SYMLINK/TOCTOU HARDENING note for why the write path's real
    protection is `os.replace()`'s own non-dereferencing semantics, not this check). Raises
    `AccountsStoreCorruptError` -- the existing "this store cannot be trusted as-is" exception,
    not a new caller-visible type -- naming only the path, never a target."""
    if path.is_symlink():
        raise AccountsStoreCorruptError(
            "accounts store at %s is a symlink -- refusing to write through it" % path
        )


def _open_fresh_temp_file(directory, name_hint, mode):
    """Open a brand-new, unpredictably-named temp file inside `directory` and return `(fd,
    tmp_path)` (PR #461 review, BLOCKING 1). `O_CREAT | O_EXCL` makes the open fail rather than
    silently reuse or follow an existing dentry at that exact name; `O_NOFOLLOW` (where the
    platform supports it -- absent on some platforms, in which case this is `0` and a no-op) is a
    second, independent layer that refuses to follow a symlink even in the case `O_EXCL` doesn't
    cover: a symlink that a previous attempt already raced into existence at this same random name.
    The name itself is `secrets.token_hex` -- cryptographically unguessable -- unlike the old fixed
    `.<name>.tmp` path, so an attacker cannot pre-plant a symlink at it before this call runs; that
    predictable-name-plus-no-`O_NOFOLLOW` combination was the exact live exploit BLOCKING 1 proved
    (plant a symlink at the guessable temp path; the write follows it, and the later `os.replace()`
    moves the symlink itself into place at the real store path). A same-name collision on a fresh
    64-hex-character random name is not something that happens by chance; retrying
    `_MAX_TEMP_FILE_ATTEMPTS` times and then failing loudly is a defensive floor, not an expected
    code path."""
    directory.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(_MAX_TEMP_FILE_ATTEMPTS):
        tmp_path = directory / (".%s.%s.tmp" % (name_hint, secrets.token_hex(16)))
        try:
            fd = os.open(str(tmp_path), flags, mode)
        except FileExistsError:
            continue
        return fd, tmp_path
    raise OSError(
        "could not create a fresh temp file in %s after %d attempts"
        % (directory, _MAX_TEMP_FILE_ATTEMPTS)
    )


@contextlib.contextmanager
def _locked_accounts(path):
    """Hold an exclusive `fcntl.flock` on a SIBLING lock file (`<path>.lock`) for the duration of
    the `with` block -- issue #308 [E18.S3], .sdlc/plans/308.md Decision 4. Both `add_user` and
    `verify_user` wrap their ENTIRE read-check-mutate-write cycle in this, not merely
    `_write_accounts_data`'s own temp-file-plus-rename swap: that swap is already atomic per
    CALL, but says nothing about two calls racing each other's READ -- two callers can each read
    the same pre-write state, independently decide what to do, and the second `os.replace()`
    silently clobbers whatever the first one just wrote. This directly targets the race
    `add_user`'s docstring measured live pre-#308 (10 concurrent calls, 8 of 10 silently lost)
    and the equivalent race against the throttle counters this same lock now also protects.

    A SIBLING file, never `path` itself: locking `path` directly would conflict with
    `_write_accounts_data`'s own `O_NOFOLLOW`/symlink-refusal guards on that exact path, and a
    lock file has no JSON content of its own for a corrupt-store guard to worry about. The lock
    file is opened with `O_CREAT` and simply never removed -- an `flock` held against a
    since-deleted-and-recreated *inode* is a real class of bug this sidesteps by construction; a
    leftover empty `.lock` file next to the real store is harmless and expected.

    `O_NOFOLLOW`, TOO (both reviewers of #308, for consistency with every other open in this
    module -- `_read_accounts_data` and `_open_fresh_temp_file` both already carry it). This
    open was the one place in the module that didn't: a planted symlink at the predictable
    `<path>.lock` name would otherwise be silently followed and `flock`ed (POSIX `flock()` locks
    the OPEN FILE DESCRIPTION, which after following a symlink refers to whatever the link's
    target is, not the sibling lock file this function thinks it is locking) -- opening the lock,
    not the accounts store itself, so it never reaches the read/write path's own symlink guards
    at all. `getattr(os, "O_NOFOLLOW", 0)`, exactly like the module's other two uses: `0` (a
    no-op) on a platform lacking the flag, never a hard dependency.

    Raises `AccountsLockUnavailableError` immediately, before opening or creating anything, if
    `fcntl` could not be imported -- see that exception's own docstring for why this is a loud
    failure rather than a silent no-op, and note it now fires for EVERY `verify_user` call
    (success included), not merely the rarely-invoked `add_user`.

    The wait for the lock is BOUNDED (`_LOCK_TIMEOUT_SECONDS`), not the indefinite block a plain
    `flock(LOCK_EX)` would give -- issue #308 [E18.S3], PR #485 code-review finding. This lock is
    store-GLOBAL and, because `verify_user` must keep the unknown-user branch cost-identical to
    every known-user branch (Decision 6.2), it is held across the full ~23ms argon2id KDF on
    EVERY login attempt, valid or not. An unauthenticated flood of garbage login POSTs therefore
    queues behind one critical section. An indefinite block turns that queue into unbounded
    process pileup: each waiter is a live `python3` spawned by `pythonBridge.ts`, pinned for the
    whole queue depth. Timing out instead caps the pileup at (arrival rate x timeout) and returns
    a loud, actionable `AccountsLockUnavailableError` -- which `insight/__main__.py` maps to the
    "check could not run" exit code, NEVER to `InvalidCredentials`, so a contended login can
    never be mistaken for a wrong password and never silently authenticates.

    No enumeration signal: contention is not credential-correlated, so the timeout fires
    identically for a real username, an unknown one, and any password.

    Two alternatives were considered and REJECTED, recorded so a later reader does not "fix" this
    into one of them. (a) Moving the KDF outside the lock would shrink the critical section but
    reopens the lost-increment race on the throttle counters that Decision 4 exists to close, and
    swaps this bounded serialisation for unbounded CONCURRENT 64MiB argon2 allocations -- a worse
    exhaustion vector, not a better one. (b) A per-account lock is not sound over a single-JSON-
    document store: every writer rewrites the whole document, so per-account locks reintroduce
    lost cross-account updates.

    ponytail: bounding the wait narrows the blast radius; it does not remove it. The real
    mitigation for an unauthenticated login flood is network-layer rate limiting, which #308
    puts explicitly out of scope. Upgrade path if this store ever outgrows one operator: per-
    account records in a store that supports row-level writes, so the lock can be per-account
    without the whole-document rewrite -- never a relaxation of the lock itself."""
    if fcntl is None:
        raise AccountsLockUnavailableError(
            "file locking (fcntl) is required for concurrent-safe writes to the accounts "
            "store but is not available on this platform"
        )
    path = pathlib.Path(path)
    # mkdir mirrors _open_fresh_temp_file's own parents=True, exist_ok=True call -- the FIRST
    # add_user/verify_user invocation against a brand-new checkout may need to create .sdlc/
    # itself before any lock file can live inside it.
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    # issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 (both reviewers, consistency finding):
    # O_NOFOLLOW added alongside O_CREAT | O_RDWR -- see this function's own docstring above for
    # why a planted symlink here is a real, distinct exploit from the ones the read/write path
    # already guards against.
    fd = os.open(
        str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        _flock_bounded(fd, lock_path)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _flock_bounded(fd, lock_path):
    """Acquire `fd`'s exclusive flock, waiting at most `_LOCK_TIMEOUT_SECONDS` -- see
    `_locked_accounts`'s docstring for why the wait is bounded rather than indefinite.

    `LOCK_NB` plus a poll, not a `SIGALRM` timeout: signals only fire on the main thread, and
    this module is called from test threads and from a `python3` subprocess per login alike.
    `time.monotonic`, never `_now()` -- `_now` is the injectable WALL clock a test moves by
    15 minutes to prove lockout expiry, and a lock timeout must not move with it."""
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise AccountsLockUnavailableError(
                    "timed out after %ss waiting for the accounts-store lock at %s"
                    % (_LOCK_TIMEOUT_SECONDS, lock_path)
                )
            time.sleep(_LOCK_POLL_SECONDS)


def _now():
    """The store's own clock seam (issue #308 [E18.S3], .sdlc/plans/308.md Decision 5) --
    factored into its own function purely so a test can `monkeypatch.setattr(store, "_now", ...)`
    to prove the 15-minute lockout expiry with ZERO real sleeping, mirroring hashing.py's own
    injectable-seam style for `argon2`/`PRODUCTION_PARAMS`. Always UTC and timezone-aware -- never
    a naive datetime, so comparisons against a parsed `locked_until` (also always UTC-aware, see
    `_parse_iso`) never raise `TypeError: can't compare offset-naive and offset-aware datetimes`."""
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value):
    """Parse a stored `locked_until` ISO-8601 string back into a timezone-aware `datetime`, or
    return `None` for anything that isn't one (missing, `None`, or -- defensively, matching this
    module's broader "a hand-edited/partially-written field must not become a new way to crash"
    philosophy -- an unparseable string or wrong-typed value). Treated as "not locked" rather than
    raising: a corrupt `locked_until` must not itself become a distinguishable failure mode."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _coerce_int(value, default=0):
    """Best-effort coercion of a stored throttle counter (`failed_attempts`) back to a plain
    `int`, falling back to `default` for anything else (a hand-edited or partially-written
    field, a `bool` -- `isinstance(True, int)` is True in Python, deliberately excluded here so a
    stray `true`/`false` in the JSON doesn't silently become 1/0). Same "malformed field must not
    itself crash verify_user" reasoning as `_parse_iso` above."""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _read_accounts_data(path):
    """Return the FULL parsed top-level accounts-store dict for `path` -- `{"version": ...,
    "users": {...}}`, plus any additional top-level keys such as `_dummy_throttle_attempts`
    (issue #308 [E18.S3], .sdlc/plans/308.md Decision 6.2). `{"version": _FORMAT_VERSION,
    "users": {}}` if the file does not exist yet -- a missing store is NOT a corrupt one, it is
    simply "no accounts exist yet" (treated identically to an unknown user by `verify_user`).
    Raises `AccountsStoreCorruptError` for unparseable JSON,
    a shape missing the required `users`/`version` keys, `path` itself being a symlink, `path`
    being unreadable (permission denied), or `path` being a FIFO/other special file that would
    otherwise never yield readable JSON.

    ATOMIC, not check-then-act (PR #461 review, second pass, BLOCKING 2 -- correcting the FIRST
    pass, which called `_refuse_if_symlink` -- an `is_symlink()` check -- then separately called
    `path.exists()`, then separately called `path.read_text()`: three distinct filesystem calls,
    each one a fresh opportunity for the path to have changed underneath since the previous call.
    A reviewer proved that live: swap the real store for a symlink to an attacker-controlled file
    in the window between the `is_symlink()` check (which still correctly saw a real file) and the
    `read_text()` call (which then transparently followed the just-planted symlink), and
    `_read_accounts` returns the attacker's planted admin record as trusted content, with no
    exception at all.

    The fix opens the file descriptor EXACTLY ONCE, with `O_NOFOLLOW` (where the platform supports
    it -- absent on some platforms, in which case this is `0` and a no-op, exactly like
    `_open_fresh_temp_file`'s own use of the same flag), and reads from that fd -- there is no
    separate earlier check for an attacker to race against, because there is no separate check:
    the kernel itself refuses to follow a symlink at the leaf path component as an atomic part of
    the SAME `open()` syscall that reads the file, reporting it as `errno.ELOOP` (which this
    function maps to `AccountsStoreCorruptError` below) whether or not the symlink's target exists
    -- a dangling symlink must be refused too, not misread as "no accounts yet".

    LEAF-ONLY (see this module's own "TRUST BOUNDARY" docstring note): `O_NOFOLLOW` only ever
    inspects the FINAL path component. It says nothing about `path`'s ancestor directories, and
    cannot be extended to say something without a much larger, deliberately out-of-scope check
    (second pass SHOULD-FIX 4, documented not fixed -- see the module docstring's TRUST BOUNDARY
    note).

    NEVER BLOCKS (PR #461 review, third pass SHOULD-FIX): the open also carries `O_NONBLOCK`
    (absent on some platforms, in which case `0`, a no-op, exactly like `O_NOFOLLOW` above) so that
    a FIFO planted at `path` -- deliberately, or a stray `mkfifo` -- cannot turn a read into a hang.
    Without it, `os.open(path, O_RDONLY)` on a FIFO with no writer never returns at all: not an
    exception, not a timeout, an indefinite hang, which is worse than any of the exceptions this
    function raises (an operator can at least see and act on an exception). `O_NONBLOCK` is
    deliberately NOT paired with a separate `stat()`-then-`open()` check for "is this a FIFO" --
    that would reintroduce exactly the check-then-act race the ATOMIC note above already closed,
    just for a different special file type. Added to the SAME single `open()` call instead: for a
    regular file it changes nothing (`O_NONBLOCK` has no effect on disk I/O); for a FIFO with no
    writer, `open()` itself still returns immediately (POSIX: `O_NONBLOCK` set on a read-only FIFO
    open never blocks the open() call, regardless of whether a writer exists), and the subsequent
    read either sees immediate EOF or raises `BlockingIOError` (an `OSError` subclass, already
    caught by the JSON-parse `except (OSError, ValueError)` below) -- either way, a FIFO at the
    accounts path (never a shape `_write_accounts_data` itself produces) ends up refused as
    `AccountsStoreCorruptError`, not hung forever."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        return {"version": _FORMAT_VERSION, "users": {}}
    except PermissionError:
        # A store the process cannot read at all -- e.g. `chmod 000` -- is not a symlink and not
        # unparseable JSON, but it is exactly as untrustworthy-as-is (PR #461 review, third pass
        # SHOULD-FIX): a raw `PermissionError` propagating past this point would reach
        # `insight/__main__.py` as an unhandled traceback, since none of its dispatch clauses name
        # this type. Folded into the same `AccountsStoreCorruptError` the CLI already handles
        # cleanly, rather than adding a fourth caller-visible exception type for one more way the
        # store can be unusable.
        raise AccountsStoreCorruptError(
            "accounts store at %s could not be opened (permission denied) -- check the file's "
            "owner and permissions" % path
        )
    except OSError as e:
        if getattr(os, "O_NOFOLLOW", 0) and e.errno == errno.ELOOP:
            raise AccountsStoreCorruptError(
                "accounts store at %s is a symlink -- refusing to read through it" % path
            )
        raise
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
    except (OSError, ValueError):
        raise AccountsStoreCorruptError(
            "accounts store at %s could not be parsed as JSON" % path
        )
    if not isinstance(data, dict) or "users" not in data or "version" not in data:
        raise AccountsStoreCorruptError(
            "accounts store at %s is not a valid accounts store (missing version/users)" % path
        )
    if not isinstance(data["users"], dict):
        raise AccountsStoreCorruptError(
            "accounts store at %s is not a valid accounts store (users is not an object)" % path
        )
    return data


def _read_accounts(path):
    """Return just the parsed `users` dict for `path` -- a thin, users-only-shaped wrapper
    around `_read_accounts_data` (issue #308 [E18.S3]), kept for every existing caller/test that
    only ever needed the `users` sub-dict and never the extra top-level
    `_dummy_throttle_attempts` sentinel Decision 6.2 introduced. See `_read_accounts_data`'s own
    docstring for the full set of guarantees (symlink/TOCTOU hardening, corrupt-store handling,
    PermissionError/FIFO handling) -- all unchanged, all still enforced by the function this now
    delegates to."""
    return _read_accounts_data(path)["users"]


def _write_accounts_data(path, data):
    """Write the FULL top-level accounts-store dict `data` (`{"version": ..., "users": {...},
    ...}`, including any extra top-level keys such as `_dummy_throttle_attempts` -- issue #308
    [E18.S3], .sdlc/plans/308.md Decision 6.2) to `path` atomically, with owner-only (0600)
    permissions -- never a plain `write_text()`, which would briefly (or permanently, depending
    on umask) leave the file group/world-readable. Refuses loudly if `path` itself is already a
    symlink (`_refuse_if_symlink`, PR #461 review BLOCKING 1) rather than clobbering whatever it
    points at. Writes to a same-directory, freshly and unpredictably named temp file
    (`_open_fresh_temp_file` -- `O_EXCL`/`O_NOFOLLOW`, never the old fixed `.<name>.tmp` path a
    symlink could be pre-planted at) with the identical explicit mode, then `os.replace()`s it
    into place, so the file is never briefly at default permissions and a mid-write crash never
    leaves a half-written store at the real path. `os.replace()`'s destination semantics matter
    here too: POSIX `rename()` never dereferences an existing destination -- if something raced a
    symlink into place at `path` between the check above and this call, `replace()` still only
    swaps the dentry, so the new file always lands as a REAL file at `path`, never written through
    a race-planted link; the check above exists to refuse loudly on the ALREADY-symlinked case,
    not because `replace()` itself is unsafe without it."""
    _refuse_if_symlink(path)
    payload = json.dumps(data, indent=2)
    fd, tmp_path = _open_fresh_temp_file(path.parent, path.name, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _write_accounts(path, users):
    """Write just `users` (`{"version": _FORMAT_VERSION, "users": users}`, no extra top-level
    keys) to `path` -- a thin, users-only-shaped wrapper around `_write_accounts_data` (issue
    #308 [E18.S3]), kept for every existing caller/test that only ever dealt with the `users`
    dict directly. See `_write_accounts_data`'s own docstring for the full set of guarantees
    (atomic swap, 0600 permissions, symlink refusal) -- all unchanged. NOTE: unlike
    `_write_accounts_data`, this DISCARDS any `_dummy_throttle_attempts` sentinel a previous
    write may have set -- callers that must preserve it (verify_user) use
    `_write_accounts_data` directly instead of this wrapper."""
    _write_accounts_data(path, {"version": _FORMAT_VERSION, "users": users})


def add_user(username, password, role, accounts_path=None):
    """Create a new account. `username`/`role` are stripped and must be non-empty (`ValueError`
    otherwise, no file written). Raises `UsernameExistsError` if the username already has an
    account -- the existing record is never overwritten. Raises `AccountsStoreCorruptError`
    rather than silently paving over a corrupt existing store -- a corrupt store must be fixed
    deliberately, since paving over it could silently destroy other admins' accounts.

    LOCKED AGAINST CONCURRENT INVOCATIONS (issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 --
    CORRECTING this docstring's own PRE-#308 claim, quoted below for the historical record). The
    whole read-check-write sequence below now runs inside `_locked_accounts` (an exclusive
    `fcntl.flock` on a sibling lock file), so two `insight users add` processes running at the
    same time no longer race each other's READ: the second caller's lock acquisition simply
    BLOCKS until the first has finished its own full read-modify-write and released, then
    proceeds against the now-current state. A losing writer is never silently dropped anymore --
    at worst it now correctly raises `UsernameExistsError` if the winner created the SAME
    username, or succeeds cleanly if it created a DIFFERENT one. See
    insight/tests/test_accounts_store.py's ten-concurrent-writers regression test, which replays
    the exact scenario measured below and asserts all ten now succeed.

    PRE-#308 HISTORY (the race this section used to document as accepted-but-unfixed no longer
    exists, per the locking fix above -- kept verbatim as the record of what was measured and
    why a lock was originally deferred): "NOT SAFE AGAINST CONCURRENT INVOCATIONS (PR #461
    review, first pass, SHOULD-FIX 4 ...). Two `insight users add` processes running at the same
    time each read the same pre-write `users` dict, independently hash, and `os.replace()` --
    last writer wins silently: no error, no lock, no detection that the file changed underneath.
    The second pass's reviewer MEASURED this for real rather than reasoning about it in the
    abstract: 10 concurrent `add_user` calls, each for a distinct username against the same
    store, and 8 of the 10 accounts were silently lost -- overwritten by a later racing write
    that never saw them -- with zero errors, zero exceptions, and no signal to the caller that
    anything was wrong." That measurement is exactly what issue #308's regression test now
    proves no longer happens."""
    username = (username or "").strip()
    role = (role or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if not role:
        raise ValueError("role must not be empty")

    path = resolve_accounts_path(accounts_path)
    with _locked_accounts(path):
        data = _read_accounts_data(path)
        users = data["users"]
        if username in users:
            raise UsernameExistsError("account %r already exists" % username)

        password_hash = hashing.hash_password(password)
        users[username] = {
            "password_hash": password_hash,
            "role": role,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        _write_accounts_data(path, data)


def verify_user(username, password, accounts_path=None):
    """Verify `username`/`password` and return the stored role on success. Raises
    `InvalidCredentials` (identical message/type for a wrong password, an unknown username, a
    LOCKED-OUT account -- even with the objectively CORRECT password, issue #308 [E18.S3],
    .sdlc/plans/308.md Decision 6 -- OR ANY way a known user's own record turns out to be
    malformed -- see below, PR #461 review, second pass BLOCKING 1) or `AccountsStoreCorruptError`
    (the store AS A WHOLE is unparseable/malformed/a symlink, distinct from "no such user" or
    "this one user's record is bad") or `hashing.KDFUnavailableError` (argon2-cffi not installed
    -- propagated unchanged, from every branch, including the malformed-record one; see below for
    why that one exception type is deliberately NOT folded in) or `AccountsLockUnavailableError`
    (`fcntl` unavailable on this platform -- issue #308 [E18.S3], .sdlc/plans/308.md Decision 4;
    see that exception's own docstring, and note it fires on EVERY call to this function,
    including a successful login, not merely `add_user`).

    THROTTLING (issue #308 [E18.S3], .sdlc/plans/308.md Decisions 3/4/5/6). This ENTIRE function
    body runs inside `_locked_accounts` -- the same `fcntl` lock `add_user` uses -- around its
    whole read-check-mutate-write cycle, so every branch below (including a locked-account
    refusal and a successful login's own counter reset) is race-free against a concurrent
    invocation. Two extra optional per-record fields, `failed_attempts` (int) and `locked_until`
    (an ISO datetime string or `None`), implement a flat threshold-and-window policy: 5
    consecutive failures locks the account for 15 minutes (`locked_until`, an ABSOLUTE timestamp
    computed once when the 5th failure lands, never extended by further attempts during the
    window -- extending on every attempt would let an attacker who cannot even guess the password
    still hold a real account locked out indefinitely just by continuing to poll it, a
    self-inflicted denial-of-service this throttle must not create); once `locked_until` has
    passed, the very next attempt is evaluated as if `failed_attempts` were 0 (a full reset, not
    a sliding/partial-credit window); a SUCCESSFUL login always resets both fields. `_now()` is
    this module's own clock seam, so tests prove the 15-minute expiry with zero real sleeping.

    AN ATTEMPT AGAINST AN ALREADY-LOCKED ACCOUNT STILL ACQUIRES THE LOCK AND PERFORMS THE FULL
    READ-MODIFY-WRITE, even though `locked_until` itself comes out unchanged (only
    `failed_attempts` still increments) -- this is NOT "optimised" into a read-only early return,
    deliberately: Decision 6.2's unknown-username branch below unconditionally does a full
    lock+read+write of EQUIVALENT shape against a shared dummy counter, and if the known-locked
    path stopped writing while the unknown path kept writing, the two would become
    distinguishable by cost and by filesystem side effect -- precisely the enumeration signal PR
    #461 spent three review passes closing. The parity requirement is the invariant; a skipped
    write here would be a bug, not an optimisation.

    AN UNKNOWN USERNAME NEVER ACCUMULATES REAL PER-ACCOUNT STATE (Decision 6.2): it never gets
    its own `failed_attempts`/`locked_until` (unbounded storage growth under a trivial
    enumerate-fake-usernames attack) -- instead it touches a single, bounded, shared sentinel
    counter (`_dummy_throttle_attempts`, top-level in the accounts JSON) under the SAME lock,
    with the SAME read+increment+write shape as the known-locked path above, before falling
    through to the existing dummy-hash KDF call. The counter's VALUE is never consulted to change
    behavior or the message -- only the shape of the operation matters, for cost/timing parity
    with a real, known, locked account.

    Calls `hashing.verify_password` EXACTLY ONCE on every failure path in the common case -- for a
    known user, against their real stored hash; for an unknown user, against the precomputed dummy
    hash (`hashing.dummy_hash_for`, a constant lookup, zero KDF cost) -- so an unknown-user lookup
    costs the same KDF work as a wrong-password lookup, at every point including immediately after
    process start (done-when 3's timing half; see hashing.py's own docstring). The ONE exception is
    a known user whose own record turns out to be malformed in ANY way -- that path calls
    `verify_password` a SECOND time, against the dummy hash, specifically so it still pays a real
    KDF-sized cost rather than becoming a fast path that would itself be a timing side-channel
    distinguishing "malformed record" from "wrong password" -- both now cost one real KDF
    verification's worth of wall-clock time, same as every other failure path.

    THIS CLAIM DEPENDS ON `hashing.verify_password` RAISING FOR EVERY MALFORMED SHAPE, INCLUDING A
    SYNTACTICALLY VALID HASH UNDER THE WRONG PARAMETERS (PR #461 review, third pass BLOCKING --
    correcting an earlier version of this docstring, which made the "always pays a second, real
    KDF-sized cost" claim unconditionally when it was not yet true). A well-formed argon2id hash
    embedding cheap KDF parameters (e.g. `m=8,t=1,p=1` where the account's real hash carries
    `m=65536,t=3,p=4`) does not raise anything on its own -- `verify()` happily runs the now-trivial
    KDF and returns a clean `True`/`False`, so the `except Exception` boundary below never fired for
    it and this path paid no KDF-sized cost at all: a reviewer measured a 262x gap between that case
    and a normal wrong password, message and exception type identical throughout. `verify_password`
    now closes that by checking `encoded_hash`'s embedded parameters against the ones currently in
    force and raising `hashing.CorruptHashError` on a mismatch (see hashing.py's own docstring's
    WEAK-EMBEDDED-PARAMETER HANDLING note and this module's own WEAK-EMBEDDED-KDF-PARAMETER
    HANDLING note above) -- which this function's `except Exception` boundary catches exactly like
    any other malformed shape, so the claim above now holds for it too, with no changes needed in
    this function itself.

    "Malformed in any way" is enforced by a SINGLE `except Exception` around the whole known-user
    branch below -- deliberately not a narrower `except hashing.CorruptHashError`, which is exactly
    what the first pass shipped and the second pass's review broke twice, live: a record missing
    the `password_hash` key raises a plain `KeyError` (from the dict lookup itself, never even
    reaching `hashing.verify_password`); a `password_hash` that is not a string (an int, a list --
    a plausible shape for a botched migration to produce) raises `AttributeError`/`TypeError` from
    INSIDE argon2-cffi's own internals, upstream of `hashing.verify_password`'s own
    `except (InvalidHashError, VerificationError)` clause. Both reached the first pass's caller as
    a different, caller-distinguishable exception type, and roughly 1000x faster than a real KDF
    operation -- a louder timing tell than the one the first pass closed. An allowlist of caught
    exception TYPES is, structurally, a promise to correctly guess every way a hand-edit, a bad
    migration, or a partial write can break a JSON record -- which is not a promise this function
    can keep. Catching the base `Exception` instead makes the guarantee "any exception raised while
    processing this known user's record becomes IDENTICAL InvalidCredentials", full stop, with no
    third case for the next unpredicted malformed shape to slip through as. The ONE exception type
    excluded from that boundary is `hashing.KDFUnavailableError` (re-raised, never folded in): a
    completely absent argon2-cffi install is not a property of any one user's record -- it is
    identical for every branch of this function, including the `record is None` one above (which
    never wraps its own `hashing.verify_password` call either) -- so treating it as "just another
    malformed-record exception" here would make this one branch behave differently from every
    other call site in this module for the exact same underlying condition.

    The malformed-record case is logged to stderr (naming the username, the store path, and the
    exception's type/message, never the password or the record's raw bytes) so an operator has a
    way to learn about it -- that signal deliberately does NOT reach the caller's exception, which
    stays identical to every other failure (done-when 3)."""
    path = resolve_accounts_path(accounts_path)
    # issue #308 [E18.S3], .sdlc/plans/308.md Decision 4 -- the fcntl lock now wraps this
    # function's ENTIRE read-check-mutate-write cycle, exactly like add_user's. Stated plainly,
    # not resting on add_user's narrower precedent (see AccountsLockUnavailableError's own
    # docstring): this fires on EVERY verify_user call, including a SUCCESSFUL login, since a
    # successful login also resets the throttle counters under this same lock (Decision 5).
    with _locked_accounts(path):
        data = _read_accounts_data(path)
        users = data["users"]
        record = users.get(username)
        now = _now()

        if record is None:
            # Decision 6.2 -- an unknown username never gets its own per-account throttle
            # record; instead it touches a single, bounded, shared sentinel counter under the
            # SAME lock, with the SAME read+increment+write shape as the known-locked branch
            # below, so the two stay indistinguishable by cost/filesystem side effect.
            data[_DUMMY_THROTTLE_KEY] = data.get(_DUMMY_THROTTLE_KEY, 0) + 1
            _write_accounts_data(path, data)
            hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
            raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

        if not isinstance(record, dict):
            # A record that is not even a dict (e.g. a hand-edited store where "alice" maps to a
            # bare string) is folded into InvalidCredentials exactly like every other malformed
            # shape below. There is no dict here to safely track failed_attempts/locked_until ON
            # (correcting this comment's own earlier claim, live in code review, issue #308
            # [E18.S3]: an EARLIER version of this branch skipped the throttle write entirely for
            # that reason -- that was itself Decision 5's exact bug, an "apparently-harmless"
            # skipped write breaking write-parity with every sibling failure branch below.
            # `_write_accounts_data(path, data)` still runs a few lines down, on `data` unchanged
            # from what was just read, purely so this branch's lock+read+write SHAPE stays
            # indistinguishable from the unknown-user/locked-account branches' own writes -- see
            # that call's own comment).
            print(
                "insight accounts: corrupt record for user %r in store %s (not an object) -- "
                "treating as invalid credentials" % (username, path),
                file=sys.stderr,
            )
            # issue #308 [E18.S3], .sdlc/plans/308.md Decision 5's write-parity invariant (code
            # review finding): every OTHER failure branch below performs a write under this same
            # lock (the unknown-user branch bumps the shared dummy counter, the locked branch
            # bumps failed_attempts, the wrong-password branch bumps failed_attempts) -- this
            # branch has no per-record field to safely mutate (there is no dict here), but it must
            # still WRITE, even though `data` is otherwise unchanged, so this shape stays
            # indistinguishable by filesystem side effect from every sibling branch. Skipping the
            # write here would be exactly the "apparently-harmless efficiency tweak" Decision 5
            # warns against for the locked-account branch, just for a different malformed shape.
            _write_accounts_data(path, data)
            hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
            raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

        locked_until = _parse_iso(record.get("locked_until"))
        if locked_until is not None and locked_until > now:
            # issue #308 [E18.S3], .sdlc/plans/308.md Decision 5's "no extension" bullet: the
            # VALUE of locked_until does NOT change here -- but the write still happens
            # (failed_attempts still increments) so this branch keeps the SAME lock+read+write
            # shape as every other branch, including the unknown-user one above. Do NOT
            # "optimise" this into a read-only early return -- see this function's own docstring
            # and Decision 5's explicit warning: that would make a locked account distinguishable
            # from an unknown one by cost and by filesystem side effect, reopening the exact
            # enumeration signal PR #461 closed. The real hash is never touched while locked --
            # the outcome is fixed regardless of whether the submitted password is correct.
            record["failed_attempts"] = _coerce_int(record.get("failed_attempts")) + 1
            _write_accounts_data(path, data)
            hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
            raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

        if locked_until is not None and locked_until <= now:
            # Decision 5's decay: a full reset once the window has passed, not a sliding/partial
            # window -- the very next attempt is evaluated as if failed_attempts were 0.
            record["failed_attempts"] = 0
            record["locked_until"] = None

        try:
            ok = hashing.verify_password(password, record["password_hash"])
        except hashing.KDFUnavailableError:
            # Not a malformed-record concern -- argon2 itself is unavailable, identically for
            # every call site in this module. Re-raised, never folded into InvalidCredentials
            # (see docstring). No throttle-state write happens on this path either: an infra
            # failure is not a "failed attempt."
            raise
        except Exception as e:
            # ANY OTHER exception raised while touching this known user's record: a single
            # defensive boundary, deliberately not an allowlist of specific types (see docstring
            # -- that is what broke twice already, pre-#306).
            print(
                "insight accounts: corrupt record for user %r in store %s (%s: %s) -- "
                "treating as invalid credentials" % (username, path, type(e).__name__, e),
                file=sys.stderr,
            )
            # Pay the same KDF-sized cost as every other failure path (see docstring above) --
            # never a fast path that itself distinguishes a malformed record from a wrong
            # password by timing.
            hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
            ok = False

        if ok:
            # issue #308 [E18.S3], .sdlc/plans/308.md Decision 5 -- a successful login ALWAYS
            # resets both throttle fields, the only way failed_attempts decreases before the
            # window elapses on its own.
            record["failed_attempts"] = 0
            record["locked_until"] = None
            _write_accounts_data(path, data)
            return record["role"]

        # Wrong password, or a malformed record folded into the same outcome above: increment
        # the throttle counter under the lock; set locked_until ONLY the moment it first reaches
        # the threshold (never recomputed/extended on a later attempt -- see the "no extension"
        # branch above, which this attempt would have taken instead had the account already been
        # locked at the top of this call).
        record["failed_attempts"] = _coerce_int(record.get("failed_attempts")) + 1
        if record["failed_attempts"] >= _THROTTLE_THRESHOLD:
            record["locked_until"] = (now + _THROTTLE_LOCKOUT).isoformat()
        _write_accounts_data(path, data)
        raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)
