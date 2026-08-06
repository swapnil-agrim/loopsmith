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

SYMLINK/TOCTOU HARDENING (PR #461 review, BLOCKING 1). Both the read and the write path refuse to
follow a symlink at the accounts-store path itself (`_refuse_if_symlink`) -- a store path that is a
symlink is refused loudly (`AccountsStoreCorruptError`), never silently followed, whether that would
mean reading an attacker-controlled "accounts store" or writing through to an arbitrary
process-writable file. The write path's temp file (`_open_fresh_temp_file`) is opened with a fresh,
unpredictable, per-call random name via `O_CREAT | O_EXCL` (never a fixed, guessable name like
`.accounts.json.tmp`) plus `O_NOFOLLOW` where the platform supports it, so a symlink pre-planted at
a predictable temp path can no longer be written through, nor `os.replace()`d into place at the real
store path (the exact two-step exploit the review proved live). See
insight/tests/test_accounts_store.py's symlink-hijack tests, which plant the attack for real and
must go RED against the pre-hardening code.

CORRUPT-PER-RECORD-HASH HANDLING (PR #461 review, BLOCKING 2). A single user's `password_hash`
field can be malformed independently of the rest of the store (a partial write, a bad migration, a
hand-edit) while the store's JSON stays perfectly valid -- this is NOT the same as
`AccountsStoreCorruptError` (which covers the store shape as a whole) and must not surface as some
third, caller-distinguishable exception type either. `verify_user` folds a corrupt per-record hash
into the exact same `InvalidCredentials`/message as a wrong password or an unknown username --
see `hashing.CorruptHashError` and `verify_user`'s own docstring for exactly how and why, including
the extra dummy-hash verification that keeps this path from becoming a timing fast-path.

Done-when 3 (message and timing indistinguishability): `verify_user` raises the SAME exception
type, `InvalidCredentials`, with the IDENTICAL message string, for both "user exists, wrong
password" and "user does not exist" -- no second exception type for a caller to `except`
selectively. `verify_user` always calls `hashing.verify_password` EXACTLY ONCE on every failure
path -- for a known user, against their real stored hash; for an unknown user, against the
precomputed dummy hash (`hashing.dummy_hash_for`, a value lookup, never an inline KDF operation)
-- the standard mitigation for user-enumeration-by-timing (see hashing.py's own docstring for why
the dummy hash is a constant, not computed lazily)."""
import datetime
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
    """Refuse loudly if `path` is itself a symlink, rather than silently reading or writing
    through it (PR #461 review, BLOCKING 1). `Path.is_symlink()` never follows the link -- it asks
    about the dentry at `path` itself -- so this is safe to call even when the link's target does
    not exist (a dangling symlink must be refused too, not misread as "no accounts yet"). Reading
    through a symlinked store path would let an attacker's fully-controlled file be trusted as the
    accounts store (a planted admin account with a known password); writing through it would
    clobber whatever process-writable file the link points at. Raises
    `AccountsStoreCorruptError` -- the existing "this store cannot be trusted as-is" exception,
    not a new caller-visible type -- naming only the path, never a target."""
    if path.is_symlink():
        raise AccountsStoreCorruptError(
            "accounts store at %s is a symlink -- refusing to read or write through it" % path
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
    a shape missing the required `users`/`version` keys, or `path` itself being a symlink (see
    `_refuse_if_symlink` -- checked FIRST, before the existence check, because a dangling symlink
    would otherwise read as "does not exist" and be silently treated as an empty store)."""
    _refuse_if_symlink(path)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
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

    NOT SAFE AGAINST CONCURRENT INVOCATIONS (PR #461 review, SHOULD-FIX 4, documented rather than
    fixed -- deliberately). Two `insight users add` processes running at the same time each read
    the same pre-write `users` dict, independently hash, and `os.replace()` -- last writer wins
    silently: no error, no lock, no detection that the file changed underneath. This is judged low
    severity and left undocumented-but-unfixed rather than gold-plated with a lock, because (a)
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
    `InvalidCredentials` (identical message/type for a wrong password, an unknown username, OR a
    corrupt per-record hash -- see below, PR #461 review BLOCKING 2) or `AccountsStoreCorruptError`
    (the store as a whole is unparseable/malformed/a symlink, distinct from "no such user") or
    `hashing.KDFUnavailableError` (argon2-cffi not installed).

    Calls `hashing.verify_password` EXACTLY ONCE on every failure path in the common case -- for a
    known user, against their real stored hash; for an unknown user, against the precomputed dummy
    hash (`hashing.dummy_hash_for`, a constant lookup, zero KDF cost) -- so an unknown-user lookup
    costs the same KDF work as a wrong-password lookup, at every point including immediately after
    process start (done-when 3's timing half; see hashing.py's own docstring). The ONE exception is
    a known user whose stored `password_hash` is itself corrupt/malformed
    (`hashing.CorruptHashError`): that path calls `verify_password` a SECOND time, against the
    dummy hash, specifically so it still pays a real KDF-sized cost rather than becoming a fast
    path that would itself be a timing side-channel distinguishing "corrupt record" from "wrong
    password" -- both now cost one real KDF verification's worth of wall-clock time, same as every
    other failure path. The corrupt-record case is logged to stderr (naming the username and store
    path, never the password or the malformed hash's raw bytes) so an operator has a way to learn
    about it -- that signal deliberately does NOT reach the caller's exception, which stays
    identical to every other failure (done-when 3)."""
    path = resolve_accounts_path(accounts_path)
    users = _read_accounts(path)
    record = users.get(username)

    if record is None:
        hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
        raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

    try:
        if hashing.verify_password(password, record["password_hash"]):
            return record["role"]
    except hashing.CorruptHashError as e:
        print(
            "insight accounts: corrupt password_hash for user %r in store %s (%s) -- "
            "treating as invalid credentials" % (username, path, e),
            file=sys.stderr,
        )
        # Pay the same KDF-sized cost as every other failure path (see docstring above) -- never a
        # fast path that itself distinguishes a corrupt record from a wrong password by timing.
        hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))

    raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)
