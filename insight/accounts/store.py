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


def _read_accounts(path):
    """Return the parsed `users` dict for `path`. `{}` if the file does not exist yet -- a
    missing store is NOT a corrupt one, it is simply "no accounts exist yet" (treated identically
    to an unknown user by `verify_user`). Raises `AccountsStoreCorruptError` for unparseable JSON
    or a shape missing the required `users`/`version` keys."""
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
    group/world-readable. Writes to a same-directory temp file with the identical explicit mode,
    then `os.replace()`s it into place, so the file is never briefly at default permissions and a
    mid-write crash never leaves a half-written store at the real path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": _FORMAT_VERSION, "users": users}, indent=2)
    tmp_path = path.parent / (".%s.tmp" % path.name)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    os.replace(str(tmp_path), str(path))


def add_user(username, password, role, accounts_path=None):
    """Create a new account. `username`/`role` are stripped and must be non-empty (`ValueError`
    otherwise, no file written). Raises `UsernameExistsError` if the username already has an
    account -- the existing record is never overwritten. Raises `AccountsStoreCorruptError`
    rather than silently paving over a corrupt existing store -- a corrupt store must be fixed
    deliberately, since paving over it could silently destroy other admins' accounts."""
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
    `InvalidCredentials` (identical message/type for a wrong password OR an unknown username) or
    `AccountsStoreCorruptError` (a genuinely corrupt store, distinct from "no such user") or
    `hashing.KDFUnavailableError` (argon2-cffi not installed).

    Calls `hashing.verify_password` EXACTLY ONCE on every failure path, always -- for a known
    user, against their real stored hash; for an unknown user, against the precomputed dummy hash
    (`hashing.dummy_hash_for`, a constant lookup, zero KDF cost) -- so an unknown-user lookup
    costs the same KDF work as a wrong-password lookup, at every point including immediately after
    process start (done-when 3's timing half; see hashing.py's own docstring)."""
    path = resolve_accounts_path(accounts_path)
    users = _read_accounts(path)
    record = users.get(username)

    if record is None:
        hashing.verify_password(password, hashing.dummy_hash_for(hashing.PRODUCTION_PARAMS))
        raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)

    if hashing.verify_password(password, record["password_hash"]):
        return record["role"]
    raise InvalidCredentials(_INVALID_CREDENTIALS_MESSAGE)
