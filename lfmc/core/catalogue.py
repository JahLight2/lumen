"""Read-side catalogue: availability states, browsing, and search.

Availability is always *derived*, never stored as a single flag, because the
same asset can be simultaneously: catalogued, preview-cached, and
original-unreachable. The UI must be able to say exactly that.

States (blueprint P0-A):
  original_available  host online, drive reachable, file present
  preview_only        host offline (or file absent) but a cached preview exists
  catalogue_only      metadata/searchable only; no preview, no original
  deleted             confirmed absent during a scan of a reachable root
  indexing            reserved for the AI/index phase (not yet produced)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

STATE_LABELS = {
    "original_available": "Original available (host online)",
    "preview_only": "Preview cached — original offline",
    "catalogue_only": "Catalogue only — original offline",
    "deleted": "Removed from source",
    "indexing": "Indexing",
}


def _preview_rows(conn, asset_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, location, path, bytes FROM cached_previews WHERE asset_id=? ORDER BY created_at DESC",
        (asset_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def preview_bytes_available(conn, asset_id: str) -> str | None:
    """Return the best locally-readable preview path (host cache or remote cache)."""
    for row in _preview_rows(conn, asset_id):
        if row["path"] and Path(row["path"]).exists():
            return row["path"]
    return None


def asset_state(conn, asset_row, verify_now: bool = False) -> dict:
    """Compute the full availability picture for one asset row.

    `verify_now=True` performs a live stat() (used when serving media, not when
    listing thousands of rows).
    """
    row = dict(asset_row)
    asset_id = row["id"]
    root = conn.execute(
        "SELECT id, label, abs_path, online, share_enabled, last_seen_at FROM roots WHERE id=?",
        (row["root_id"],),
    ).fetchone()
    host_online = bool(root["online"]) if root else False
    drive_reachable = False
    file_present = False
    if root:
        base = Path(root["abs_path"])
        drive_reachable = base.is_dir()
        if drive_reachable:
            full = base / row["rel_path"]
            file_present = full.exists() if verify_now else True
    previews = _preview_rows(conn, asset_id)
    has_preview = bool(previews)

    if row.get("deleted_at"):
        state = "deleted"
    elif host_online and drive_reachable and file_present:
        state = "original_available"
    elif has_preview:
        state = "preview_only"
    else:
        state = "catalogue_only"

    return {
        "asset_id": asset_id,
        "state": state,
        "state_label": STATE_LABELS[state],
        "host_online": bool(host_online),
        "share_enabled": bool(root["share_enabled"]) if root else False,
        "drive_reachable": drive_reachable,
        "original_available": state == "original_available",
        "catalogue_available": state != "deleted",
        "preview_available": has_preview,
        "previews": previews,
        "root_id": row["root_id"],
        "root_label": root["label"] if root else None,
        "root_last_seen": root["last_seen_at"] if root else None,
    }


def get_asset(conn, asset_id: str):
    return conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()


def list_assets(conn, root_id: str | None = None, rel_prefix: str | None = None,
                include_deleted: bool = False, limit: int = 200, offset: int = 0) -> list[dict]:
    sql = ["SELECT * FROM assets WHERE 1=1"]
    args: list = []
    if root_id:
        sql.append("AND root_id=?")
        args.append(root_id)
    if rel_prefix:
        sql.append("AND rel_path LIKE ?")
        args.append(rel_prefix.rstrip("/") + "/%")
    if not include_deleted:
        sql.append("AND deleted_at IS NULL")
    sql.append("ORDER BY rel_path LIMIT ? OFFSET ?")
    args += [limit, offset]
    rows = conn.execute(" ".join(sql), args).fetchall()
    return [dict(r) for r in rows]


def browse(conn, root_id: str, dir_path: str = "", limit: int = 500) -> dict:
    """Directory listing derived from the catalogue only.

    Works with the drive unplugged: this is the offline browse requirement.
    Child folders are synthesised from rel_path values, so folder structure is
    preserved without touching the filesystem.
    """
    dir_path = dir_path.strip("/")
    prefix = f"{dir_path}/" if dir_path else ""
    rows = conn.execute(
        "SELECT * FROM assets WHERE root_id=? AND deleted_at IS NULL AND rel_path LIKE ? LIMIT ?",
        (root_id, prefix + "%", limit * 20),
    ).fetchall()
    folders: dict[str, int] = {}
    files: list[dict] = []
    for r in rows:
        rel = r["rel_path"]
        if dir_path and not rel.startswith(prefix):
            continue
        rest = rel[len(prefix):]
        if "/" in rest:
            top = rest.split("/", 1)[0]
            folders[top] = folders.get(top, 0) + 1
        elif rest:
            files.append(dict(r))
    root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
    return {
        "root_id": root_id,
        "root_label": root["label"] if root else None,
        "root_online": bool(root["online"]) if root else False,
        "path": dir_path,
        "folders": [{"name": k, "file_count": v, "path": f"{prefix}{k}".rstrip("/")}
                    for k, v in sorted(folders.items())][:limit],
        "files": sorted(files, key=lambda x: x["name"])[:limit],
    }


_TOKEN = re.compile(r"[0-9A-Za-z\u1200-\u137F_]+")


def search(conn, query: str, root_id: str | None = None, limit: int = 50) -> list[dict]:
    """Offline-capable search over name/path/tags/transcript (FTS5, BM25 ranked).

    Synthesises a safe prefix query from user input so arbitrary punctuation
    cannot produce an FTS5 syntax error.
    """
    tokens = _TOKEN.findall(query or "")
    if not tokens:
        return []
    match = " AND ".join(f'"{t}"*' for t in tokens[:8])
    sql = """
        SELECT a.*, s.name AS s_name, bm25(asset_search, 5.0, 1.0, 2.0, 1.0) AS rank
        FROM asset_search s
        JOIN assets a ON a.id = s.asset_id
        WHERE asset_search MATCH ? AND a.deleted_at IS NULL
    """
    args: list = [match]
    if root_id:
        sql += " AND a.root_id = ?"
        args.append(root_id)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:
        # Last-resort fallback keeps search working even on odd input.
        like = f"%{tokens[0]}%"
        rows = conn.execute(
            "SELECT * FROM assets WHERE deleted_at IS NULL AND name LIKE ? LIMIT ?", (like, limit)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rank"] = d.pop("rank", None)
        out.append(d)
    return out


def root_summary(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM assets a WHERE a.root_id=r.id AND a.deleted_at IS NULL) AS live_assets,
                  (SELECT COUNT(*) FROM assets a WHERE a.root_id=r.id AND a.deleted_at IS NULL
                     AND a.media_kind='video') AS video_assets
           FROM roots r ORDER BY r.created_at"""
    ).fetchall()
    return [dict(r) for r in rows]


def scan_history(conn, root_id: str | None = None, limit: int = 20) -> list[dict]:
    if root_id:
        rows = conn.execute(
            "SELECT * FROM scan_runs WHERE root_id=? ORDER BY started_at DESC LIMIT ?",
            (root_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
