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
already a total-compromise scenario this module was never designed to survive."""
import datetime
import errno
import json
import os
import pathlib
import secrets
import sys

from insight.accounts import hashing

#: Resolved relative to CWD at call time (see resolve_accounts_path), never at import time --
#: mirrors insight/ingest/store.py's DEFAULT_DB_PATH exactly.
DEFAULT_ACCOUNTS_PATH = pathlib.Path(".sdlc") / "insight-accounts.json"

#: The single message string used for BOTH "wrong password" and "unknown user" -- done-when 3's
#: message-indistinguishability requirement. No second, more specific message exists anywhere in
#: this module.
_INVALID_CREDENTIALS_MESSAGE = "invalid username or password"

_FORMAT_VERSION = 1


class InvalidCredentials(Exception):
    """Raised by `verify_user` for both a wrong password and an unknown username -- the SAME
    exception type, with the SAME message, in both cases (done-when 3)."""


class AccountsStoreCorruptError(Exception):
    """Raised when the accounts file exists but cannot be parsed as a valid accounts store.
    The message names only the file PATH -- never raw file bytes, so a corrupt store's contents
    (which could themselves be a botched write containing partial secret material) never surface
    through an exception message."""


class UsernameExistsError(Exception):
    """Raised by `add_user` when the given username already has an account."""


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


def _read_accounts(path):
    """Return the parsed `users` dict for `path`. `{}` if the file does not exist yet -- a
    missing store is NOT a corrupt one, it is simply "no accounts exist yet" (treated identically
    to an unknown user by `verify_user`). Raises `AccountsStoreCorruptError` for unparseable JSON,
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
    accounts path (never a shape `_write_accounts` itself produces) ends up refused as
    `AccountsStoreCorruptError`, not hung forever."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        return {}
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
    return data["users"]


def _write_accounts(path, users):
    """Write `users` to `path` atomically, with owner-only (0600) permissions -- never a plain
    `write_text()`, which would briefly (or permanently, depending on umask) leave the file
    group/world-readable. Refuses loudly if `path` itself is already a symlink
    (`_refuse_if_symlink`, PR #461 review BLOCKING 1) rather than clobbering whatever it points at.
    Writes to a same-directory, freshly and unpredictably named temp file (`_open_fresh_temp_file`
    -- `O_EXCL`/`O_NOFOLLOW`, never the old fixed `.<name>.tmp` path a symlink could be pre-planted
    at) with the identical explicit mode, then `os.replace()`s it into place, so the file is never
    briefly at default permissions and a mid-write crash never leaves a half-written store at the
    real path. `os.replace()`'s destination semantics matter here too: POSIX `rename()` never
    dereferences an existing destination -- if something raced a symlink into place at `path`
    between the check above and this call, `replace()` still only swaps the dentry, so the new file
    always lands as a REAL file at `path`, never written through a race-planted link; the check
    above exists to refuse loudly on the ALREADY-symlinked case, not because `replace()` itself is
    unsafe without it."""
    _refuse_if_symlink(path)
    payload = json.dumps({"version": _FORMAT_VERSION, "users": users}, indent=2)
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


def add_user(username, password, role, accounts_path=None):
    """Create a new account. `username`/`role` are stripped and must be non-empty (`ValueError`
    otherwise, no file written). Raises `UsernameExistsError` if the username already has an
    account -- the existing record is never overwritten. Raises `AccountsStoreCorruptError`
    rather than silently paving over a corrupt existing store -- a corrupt store must be fixed
    deliberately, since paving over it could silently destroy other admins' accounts.

    NOT SAFE AGAINST CONCURRENT INVOCATIONS (PR #461 review, first pass, SHOULD-FIX 4 -- distinct
    from the SECOND pass's own SHOULD-FIX 4, about a symlinked ancestor directory; see the module
    docstring's TRUST BOUNDARY note -- documented rather than fixed, deliberately). Two
    `insight users add` processes running at the same time each read the same pre-write `users`
    dict, independently hash, and `os.replace()` -- last writer wins silently: no error, no lock,
    no detection that the file changed underneath. The second pass's reviewer MEASURED this for
    real rather than reasoning about it in the abstract: 10 concurrent `add_user` calls, each for a
    distinct username against the same store, and 8 of the 10 accounts were silently lost --
    overwritten by a later racing write that never saw them -- with zero errors, zero exceptions,
    and no signal to the caller that anything was wrong. This is judged low severity and left
    undocumented-but-unfixed rather than gold-plated with a lock, because (a)
    `insight users add` is a single-operator local CLI, not a server handling concurrent requests --
    there is no realistic scenario where two invocations race by accident, only by a deliberate
    double-run; (b) the failure mode is "re-run the second `add` and it succeeds" -- an account is
    never corrupted or partially written (`_write_accounts` is still atomic), only silently not
    created; and (c) a real fix needs an OS-level exclusive lock (`fcntl.flock` on POSIX,
    `msvcrt.locking` on Windows) with its own edge cases (stale locks, timeouts) that are
    disproportionate machinery for a low-severity, single-operator race. If `insight users add` is
    ever driven by anything other than an interactive human at a terminal (automation, a setup
    script run in parallel), revisit this."""
    username = (username or "").strip()
    role = (role or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if not role:
        raise ValueError("role must not be empty")

    path = resolve_accounts_path(accounts_path)
    users = _read_accounts(path)
    if username in users:
        raise UsernameExistsError("account %r already exists" % username)

    password_hash = hashing.hash_password(password)
    users[username] = {
        "password_hash": password_hash,
        "role": role,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _write_accounts(path, users)


def verify_user(username, password, accounts_path=None):
    """Verify `username`/`password` and return the stored role on success. Raises
    `InvalidCredentials` (identical message/type for a wrong password, an unknown username, OR ANY
    way a known user's own record turns out to be malformed -- see below, PR #461 review, second
    pass BLOCKING 1) or `AccountsStoreCorruptError` (the store AS A WHOLE is
    unparseable/malformed/a symlink, distinct from "no such user" or "this one user's record is
    bad") or `hashing.KDFUnavailableError` (argon2-cffi not installed -- propagated unchanged,
    from every branch, including the malformed-record one; see below for why that one exception
    type is deliberately NOT folded in).

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
    users = _read_accounts(path)
    record = users.get(username)

    if record is None:
        hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
        raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

    try:
        if hashing.verify_password(password, record["password_hash"]):
            return record["role"]
    except hashing.KDFUnavailableError:
        # Not a malformed-record concern -- argon2 itself is unavailable, identically for every
        # call site in this module. Re-raised, never folded into InvalidCredentials (see docstring).
        raise
    except Exception as e:
        # ANY OTHER exception raised while touching this known user's record: a single defensive
        # boundary, deliberately not an allowlist of specific types (see docstring -- that is what
        # broke twice already).
        print(
            "insight accounts: corrupt record for user %r in store %s (%s: %s) -- "
            "treating as invalid credentials" % (username, path, type(e).__name__, e),
            file=sys.stderr,
        )
        # Pay the same KDF-sized cost as every other failure path (see docstring above) -- never a
        # fast path that itself distinguishes a malformed record from a wrong password by timing.
        hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))

    raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)
