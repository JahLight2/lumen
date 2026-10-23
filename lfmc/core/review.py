"""Review data: timestamped comments, accountless review links, selects, versions.

Design constraints from the blueprint (§4E/§4F, acceptance criteria):

* Comments carry **asset id + timecode + frame rate + reviewed version**. Never a
  filename alone, and never silently re-pointed at a newer cut.
* Guest review links are **scoped to one asset/version**, accountless, expiring,
  revocable, optionally passphrase-protected, and must not expose the folder tree.
* Guest tokens are stored **hashed** (sha256); the raw token exists only in the URL
  handed to the client.
* Selects (Keep/Maybe/Reject) bind to the stable asset id and source timecode.
* Nothing here uploads or copies original media.

Honesty note: `fps` is recorded per comment so timecode stays frame-accurate when
the media is later probed. Until ffprobe is wired up, assets have no probed fps and
callers pass the project's assumed fps explicitly.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from .db import now_iso

DEFAULT_FPS = 25.0
DECISIONS = ("keep", "maybe", "reject")
VISIBILITY = ("internal", "shared")


class ReviewError(ValueError):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_passphrase(passphrase: str, salt: str) -> str:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt.encode("utf-8"),
                          n=2 ** 14, r=8, p=1, dklen=32).hex()


def timecode(ms: int, fps: float = DEFAULT_FPS) -> str:
    """Render HH:MM:SS:FF (frame-accurate), the format editors expect."""
    if ms is None:
        return ""
    ms = max(0, int(ms))
    fps = fps or DEFAULT_FPS
    total_frames = int(round((ms / 1000.0) * fps))
    frames = total_frames % int(round(fps))
    total_seconds = total_frames // int(round(fps))
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def parse_timecode(value: str, fps: float = DEFAULT_FPS) -> int:
    """Accept HH:MM:SS:FF, HH:MM:SS, MM:SS or plain seconds. Returns milliseconds."""
    if not value:
        raise ReviewError("empty timecode")
    parts = str(value).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ReviewError(f"invalid timecode: {value!r}") from exc
    if len(parts) == 4:
        h, m, s, f = nums
        secs = h * 3600 + m * 60 + s + (f / (fps or DEFAULT_FPS))
    elif len(parts) == 3:
        h, m, s = nums
        secs = h * 3600 + m * 60 + s
    elif len(parts) == 2:
        m, s = nums
        secs = m * 60 + s
    elif len(parts) == 1:
        secs = nums[0]
    else:
        raise ReviewError(f"invalid timecode: {value!r}")
    return int(round(secs * 1000))


# ---------------------------------------------------------------------------
# comments
# ---------------------------------------------------------------------------

def add_comment(conn, *, asset_id: str, body: str, author: str,
                author_kind: str = "member", timecode_ms: int | None = None,
                timecode_end_ms: int | None = None, fps: float = DEFAULT_FPS,
                parent_id: str | None = None, version_label: str = "v1",
                visibility: str = "internal") -> dict:
    if not (body or "").strip():
        raise ReviewError("comment body is required")
    if visibility not in VISIBILITY:
        raise ReviewError(f"visibility must be one of {VISIBILITY}")
    asset = conn.execute("SELECT id FROM assets WHERE id=? AND deleted_at IS NULL",
                         (asset_id,)).fetchone()
    if asset is None:
        raise ReviewError("unknown or deleted asset")
    if parent_id:
        parent = conn.execute("SELECT asset_id, version_label FROM asset_comments WHERE id=?",
                              (parent_id,)).fetchone()
        if parent is None:
            raise ReviewError("parent comment not found")
        if parent["asset_id"] != asset_id:
            raise ReviewError("reply must target a comment on the same asset")
        # A reply must not move to a different cut than the comment it answers.
        if parent["version_label"] != version_label:
            raise ReviewError(
                f"parent comment belongs to {parent['version_label']}; "
                f"refusing to attach a reply in {version_label}")
    if timecode_ms is not None and timecode_ms < 0:
        raise ReviewError("timecode must be >= 0")
    if timecode_end_ms is not None and timecode_ms is not None and timecode_end_ms < timecode_ms:
        raise ReviewError("range end must be after start")

    cid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO asset_comments(id, asset_id, version_label, parent_id, author,
                                      author_kind, body, timecode_ms, timecode_end_ms, fps,
                                      visibility, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, asset_id, version_label, parent_id, author, author_kind, body.strip(),
         timecode_ms, timecode_end_ms, fps, visibility, now_iso()),
    )
    conn.commit()
    return get_comment(conn, cid)


def get_comment(conn, comment_id: str) -> dict:
    row = conn.execute("SELECT * FROM asset_comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise ReviewError("comment not found")
    d = dict(row)
    d["timecode"] = timecode(d["timecode_ms"], d["fps"]) if d["timecode_ms"] is not None else None
    d["timecode_end"] = (timecode(d["timecode_end_ms"], d["fps"])
                         if d["timecode_end_ms"] is not None else None)
    d["reactions"] = [dict(r) for r in conn.execute(
        "SELECT emoji, actor, created_at FROM comment_reactions WHERE comment_id=? ORDER BY created_at",
        (comment_id,))]
    return d


def list_comments(conn, asset_id: str, version_label: str | None = None,
                  include_internal: bool = True) -> list[dict]:
    sql = "SELECT id FROM asset_comments WHERE asset_id=?"
    args: list = [asset_id]
    if version_label:
        sql += " AND version_label=?"
        args.append(version_label)
    if not include_internal:
        sql += " AND visibility='shared'"
    sql += " ORDER BY COALESCE(timecode_ms, -1), created_at"
    ids = [r["id"] for r in conn.execute(sql, args)]
    return [get_comment(conn, cid) for cid in ids]


def resolve_comment(conn, comment_id: str, resolved: bool = True, actor: str = "member") -> dict:
    row = conn.execute("SELECT id FROM asset_comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise ReviewError("comment not found")
    conn.execute("UPDATE asset_comments SET resolved_at=? WHERE id=?",
                 (now_iso() if resolved else None, comment_id))
    conn.execute("INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
                 (now_iso(), "comment.resolved" if resolved else "comment.reopened",
                  actor, comment_id, asset_id_of(conn, comment_id)))
    conn.commit()
    return get_comment(conn, comment_id)


def asset_id_of(conn, comment_id: str) -> str | None:
    row = conn.execute("SELECT asset_id FROM asset_comments WHERE id=?", (comment_id,)).fetchone()
    return row["asset_id"] if row else None


def react(conn, comment_id: str, emoji: str, actor: str) -> dict:
    if not emoji or len(emoji) > 8:
        raise ReviewError("emoji is required")
    row = conn.execute("SELECT id FROM asset_comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise ReviewError("comment not found")
    existing = conn.execute(
        "SELECT 1 FROM comment_reactions WHERE comment_id=? AND emoji=? AND actor=?",
        (comment_id, emoji, actor)).fetchone()
    if existing:
        conn.execute("DELETE FROM comment_reactions WHERE comment_id=? AND emoji=? AND actor=?",
                     (comment_id, emoji, actor))
    else:
        conn.execute(
            "INSERT INTO comment_reactions(comment_id, emoji, actor, created_at) VALUES(?,?,?,?)",
            (comment_id, emoji, actor, now_iso()))
    conn.commit()
    return get_comment(conn, comment_id)


# ---------------------------------------------------------------------------
# review links (accountless guest access)
# ---------------------------------------------------------------------------

def create_review_link(conn, *, asset_id: str, version_label: str = "v1",
                       created_by: str = "owner", expires_in_hours: int | None = 168,
                       passphrase: str | None = None, allow_comments: bool = True,
                       allow_download: bool = False, watermark: bool = False,
                       note: str | None = None) -> dict:
    asset = conn.execute("SELECT id, name FROM assets WHERE id=? AND deleted_at IS NULL",
                         (asset_id,)).fetchone()
    if asset is None:
        raise ReviewError("unknown or deleted asset")
    if expires_in_hours is not None and expires_in_hours <= 0:
        raise ReviewError("expiry must be positive or null (never expires)")
    token = secrets.token_urlsafe(24)
    salt = secrets.token_hex(8)
    expires_at = None
    if expires_in_hours:
        expires_at = (datetime.now(timezone.utc)
                      + timedelta(hours=int(expires_in_hours))).isoformat(timespec="seconds")
    link_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO review_links(id, token_hash, asset_id, version_label, scope,
                                    allow_comments, allow_download, watermark, passphrase_salt,
                                    passphrase_hash, expires_at, created_by, created_at, note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (link_id, _hash_token(token), asset_id, version_label, "asset",
         1 if allow_comments else 0, 1 if allow_download else 0, 1 if watermark else 0,
         salt if passphrase else None,
         _hash_passphrase(passphrase, salt) if passphrase else None,
         expires_at, created_by, now_iso(), note),
    )
    conn.execute("INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
                 (now_iso(), "review_link.created", created_by, link_id,
                  json.dumps({"asset_id": asset_id, "version": version_label,
                              "expires_at": expires_at, "passphrase": bool(passphrase)})))
    conn.commit()
    return {"id": link_id, "token": token, "url_path": f"/r/{token}",
            "asset_id": asset_id, "asset_name": asset["name"],
            "version_label": version_label, "expires_at": expires_at,
            "passphrase_required": bool(passphrase),
            "allow_comments": allow_comments, "allow_download": allow_download}


def _link_row(conn, link_id: str):
    return conn.execute("SELECT * FROM review_links WHERE id=?", (link_id,)).fetchone()


def _evaluate_link(row, passphrase: str | None) -> tuple[bool, str]:
    if row is None:
        return False, "unknown link"
    if row["revoked_at"]:
        return False, "revoked"
    if row["expires_at"]:
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                return False, "expired"
        except ValueError:
            return False, "invalid expiry"
    if row["passphrase_hash"]:
        if not passphrase:
            return False, "passphrase_required"
        digest = _hash_passphrase(passphrase, row["passphrase_salt"] or "")
        if not secrets.compare_digest(digest, row["passphrase_hash"]):
            return False, "passphrase_invalid"
    return True, "ok"


def resolve_link(conn, token: str, passphrase: str | None = None) -> dict:
    """Validate a guest token. Raises ReviewError with the reason otherwise."""
    row = conn.execute("SELECT * FROM review_links WHERE token_hash=?",
                       (_hash_token(token),)).fetchone()
    ok, reason = _evaluate_link(row, passphrase)
    if not ok:
        raise ReviewError(reason)
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (row["asset_id"],)).fetchone()
    if asset is None or asset["deleted_at"]:
        raise ReviewError("asset unavailable")
    conn.execute("UPDATE review_links SET last_used_at=? WHERE id=?", (now_iso(), row["id"]))
    conn.execute("INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
                 (now_iso(), "review_link.opened", "guest", row["id"], row["asset_id"]))
    conn.commit()
    return {"link": dict(row), "asset": dict(asset)}


def revoke_link(conn, link_id: str, actor: str = "owner") -> dict:
    if _link_row(conn, link_id) is None:
        raise ReviewError("link not found")
    conn.execute("UPDATE review_links SET revoked_at=? WHERE id=?", (now_iso(), link_id))
    conn.execute("INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
                 (now_iso(), "review_link.revoked", actor, link_id, None))
    conn.commit()
    return list_links(conn, link_id=link_id)[0]


def list_links(conn, asset_id: str | None = None, link_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM review_links"
    args: list = []
    if link_id:
        sql += " WHERE id=?"
        args.append(link_id)
    elif asset_id:
        sql += " WHERE asset_id=?"
        args.append(asset_id)
    sql += " ORDER BY created_at DESC"
    out = []
    for row in conn.execute(sql, args):
        d = dict(row)
        d.pop("passphrase_hash", None)
        d.pop("passphrase_salt", None)
        d.pop("token_hash", None)  # never echo the token or its hash
        d["passphrase_required"] = bool(row["passphrase_hash"])
        d["status"] = _derive_status(row)
        out.append(d)
    return out


def _derive_status(row) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"]:
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                return "expired"
        except ValueError:
            return "invalid"
    return "active"


# ---------------------------------------------------------------------------
# selects (mobile footage review)
# ---------------------------------------------------------------------------

def set_select(conn, *, asset_id: str, reviewer: str, decision: str,
               note: str | None = None, timecode_ms: int | None = None) -> dict:
    if decision not in DECISIONS:
        raise ReviewError(f"decision must be one of {DECISIONS}")
    asset = conn.execute("SELECT id FROM assets WHERE id=? AND deleted_at IS NULL",
                         (asset_id,)).fetchone()
    if asset is None:
        raise ReviewError("unknown or deleted asset")
    ts = now_iso()
    conn.execute(
        """INSERT INTO selects(asset_id, reviewer, decision, note, timecode_ms, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(asset_id, reviewer) DO UPDATE SET decision=excluded.decision,
                note=excluded.note, timecode_ms=excluded.timecode_ms,
                updated_at=excluded.updated_at""",
        (asset_id, reviewer, decision, note, timecode_ms, ts, ts),
    )
    conn.commit()
    return get_select(conn, asset_id, reviewer)


def get_select(conn, asset_id: str, reviewer: str) -> dict:
    row = conn.execute("SELECT * FROM selects WHERE asset_id=? AND reviewer=?",
                       (asset_id, reviewer)).fetchone()
    if row is None:
        raise ReviewError("no select for that reviewer")
    return dict(row)


def list_selects(conn, decision: str | None = None) -> list[dict]:
    sql = """SELECT s.*, a.name, a.rel_path, a.root_id, a.media_kind, a.size_bytes
             FROM selects s JOIN assets a ON a.id = s.asset_id
             WHERE a.deleted_at IS NULL"""
    args: list = []
    if decision:
        sql += " AND s.decision=?"
        args.append(decision)
    sql += " ORDER BY s.updated_at DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def clear_select(conn, asset_id: str, reviewer: str) -> None:
    conn.execute("DELETE FROM selects WHERE asset_id=? AND reviewer=?", (asset_id, reviewer))
    conn.commit()


# ---------------------------------------------------------------------------
# marker export (feeds Gate D: notes -> NLE timeline markers)
# ---------------------------------------------------------------------------

def markers_for(conn, asset_id: str, version_label: str = "v1",
                include_internal: bool = True) -> list[dict]:
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if asset is None:
        raise ReviewError("unknown asset")
    fps = DEFAULT_FPS
    out = []
    for c in list_comments(conn, asset_id, version_label, include_internal):
        if c["timecode_ms"] is None:
            continue
        out.append({
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "comment_id": c["id"],
            "author": c["author"],
            "body": c["body"],
            "timecode": timecode(c["timecode_ms"], c["fps"]),
            "timecode_end": (timecode(c["timecode_end_ms"], c["fps"])
                             if c["timecode_end_ms"] is not None else None),
            "frame": int(round((c["timecode_ms"] / 1000.0) * (c["fps"] or fps))),
            "fps": c["fps"],
            "resolved": bool(c["resolved_at"]),
        })
    return out


def markers_csv(conn, asset_id: str, version_label: str = "v1") -> str:
    import csv
    import io
    rows = markers_for(conn, asset_id, version_label)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Timecode", "Frame", "FPS", "Description", "Author", "Resolved"])
    for r in rows:
        w.writerow([r["asset_name"], r["timecode"], r["frame"], r["fps"],
                    r["body"].replace("\n", " "), r["author"],
                    "yes" if r["resolved"] else "no"])
    return buf.getvalue()
