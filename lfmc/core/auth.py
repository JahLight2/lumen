"""Collaborators, scoped tokens, and the role model.

Why this exists
---------------
Until now the host had exactly one token. That works for one person and breaks
for a team in two specific ways:

* every comment is attributed to "member", so a review thread is a pile of
  anonymous notes -- useless when two editors disagree;
* revoking one person's access means changing the password for everyone.

So: one token per person, hashed at rest, with a role that bounds what the token
may do. The host token is unchanged and still means "the person who runs the
machine" (full rights). Collaborator tokens are a narrowing, never a widening.

Roles
-----
producer  read + comment + share links + selects + project hub
editor    read + comment + selects + project hub + marker export
reviewer  read + comment (no sharing, no hub, no marker export, no root admin)

What this is NOT
----------------
* Not an account system: no email verification, no password reset, no SSO.
  A token is a bearer secret handed over out of band. Treat it like a WiFi
  password.
* Not an identity proof: anyone holding the token is that collaborator.
* Not multi-tenant. There is one catalogue and one host.

Threat model, stated plainly
----------------------------
Tokens are stored as SHA-256 hashes. A stolen catalogue file therefore does not
hand over live tokens. SHA-256 without a salt is deliberate: this is a bearer
secret with ~192 bits of entropy from `secrets.token_urlsafe`, not a
human-chosen password, so brute force is not the attack and a per-row salt adds
nothing. If tokens ever become user-chosen, this must move to scrypt.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from .db import now_iso

ROLES = ("producer", "editor", "reviewer")

# What each role may do. The host token implicitly has every scope.
ROLE_SCOPES = {
    "producer": frozenset({
        "read", "comment", "share", "select", "hub", "markers", "admin",
    }),
    "editor": frozenset({
        "read", "comment", "select", "hub", "markers",
    }),
    "reviewer": frozenset({
        "read", "comment",
    }),
}

HOST_SCOPES = frozenset({"read", "comment", "share", "select", "hub", "markers", "admin"})

# Token length: 24 bytes -> 32 url-safe chars. Same shape as the host token.
TOKEN_BYTES = 24


class AuthError(ValueError):
    """Raised for invalid collaborator operations (caller turns this into 400)."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def scopes_for_role(role: str) -> frozenset:
    try:
        return ROLE_SCOPES[role]
    except KeyError:
        raise AuthError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")


def _iso_in_hours(hours: float | None) -> str | None:
    if hours is None:
        return None
    return (datetime.now(timezone.utc) + timedelta(hours=float(hours))).isoformat(
        timespec="seconds")


def add_collaborator(conn, *, name: str, role: str, created_by: str = "owner",
                     expires_in_hours: float | None = None,
                     note: str | None = None) -> dict:
    """Create a collaborator. Returns the ONLY copy of the raw token.

    The raw token is returned exactly once and never stored; losing it means
    revoking and reissuing.
    """
    name = (name or "").strip()
    if not name:
        raise AuthError("name is required")
    scopes_for_role(role)  # validates
    token = secrets.token_urlsafe(TOKEN_BYTES)
    cid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO collaborators(id, name, role, token_hash, created_at,
                                     created_by, expires_at, note)
           VALUES(?,?,?,?,?,?,?,?)""",
        (cid, name, role, hash_token(token), now_iso(), created_by,
         _iso_in_hours(expires_in_hours), note),
    )
    conn.commit()
    row = get_collaborator(conn, cid)
    row["token"] = token            # shown once, never persisted
    row["scopes"] = sorted(scopes_for_role(role))
    return row


def get_collaborator(conn, collaborator_id: str) -> dict:
    row = conn.execute("SELECT * FROM collaborators WHERE id=?",
                       (collaborator_id,)).fetchone()
    if row is None:
        raise AuthError("unknown collaborator")
    return _public(row)


def _public(row) -> dict:
    """Strip the hash. Never let it out of the module by accident."""
    d = dict(row)
    d.pop("token_hash", None)
    d["scopes"] = sorted(ROLE_SCOPES.get(d.get("role"), ()))
    d["revoked"] = bool(d.get("revoked_at"))
    return d


def list_collaborators(conn, include_revoked: bool = False) -> list[dict]:
    sql = "SELECT * FROM collaborators"
    if not include_revoked:
        sql += " WHERE revoked_at IS NULL"
    sql += " ORDER BY created_at"
    return [_public(r) for r in conn.execute(sql)]


def revoke_collaborator(conn, collaborator_id: str, actor: str = "owner") -> dict:
    row = conn.execute("SELECT * FROM collaborators WHERE id=?",
                       (collaborator_id,)).fetchone()
    if row is None:
        raise AuthError("unknown collaborator")
    if row["revoked_at"] is None:
        conn.execute("UPDATE collaborators SET revoked_at=? WHERE id=?",
                     (now_iso(), collaborator_id))
        from .db import log_event
        log_event(conn, "collaborator_revoked", actor, collaborator_id, row["name"])
        conn.commit()
    return get_collaborator(conn, collaborator_id)


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        when = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True          # unparseable expiry fails closed
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= datetime.now(timezone.utc)


def resolve_token(conn, token: str) -> dict | None:
    """Map a raw bearer token to a collaborator, or None if it is not valid.

    Invalid includes: unknown, revoked, expired. Unknown and revoked are
    deliberately indistinguishable to the caller (both 401) so a probe cannot
    tell a revoked token apart from a never-existing one.
    """
    if not token:
        return None
    row = conn.execute("SELECT * FROM collaborators WHERE token_hash=?",
                       (hash_token(token),)).fetchone()
    if row is None:
        return None
    if row["revoked_at"] is not None or _expired(row["expires_at"]):
        return None
    # Touch last_used_at at most once a minute to avoid a write per request.
    try:
        last = row["last_used_at"]
        stale = True
        if last:
            prev = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            stale = (datetime.now(timezone.utc) - prev) > timedelta(minutes=1)
        if stale:
            conn.execute("UPDATE collaborators SET last_used_at=? WHERE id=?",
                         (now_iso(), row["id"]))
            conn.commit()
    except Exception:
        pass                 # bookkeeping must never break a request
    return _public(row)


def has_scope(actor: dict | None, scope: str, *, host: bool = False) -> bool:
    """Does this actor hold `scope`? Host token always does."""
    if host:
        return True
    if actor is None:
        return False
    return scope in ROLE_SCOPES.get(actor.get("role"), frozenset())
