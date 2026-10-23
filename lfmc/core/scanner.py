"""Incremental folder/drive scanner and catalogue maintenance.

Non-negotiable behaviour implemented here (blueprint P0-A):

1. Nothing outside a registered root is ever read.
2. A scan of an OFFLINE root is a no-op. It must never tombstone assets.
   "Disconnected" is a state, not a deletion.
3. Only a scan that actually completed over a reachable root may mark files as
   removed. This is what keeps the catalogue trustworthy offline.
4. Moves/renames are detected by sampled content fingerprint and keep the same
   stable asset id, so history follows the file.
5. Source media is never modified. We only stat() and read().
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import identity
from .db import log_event, now_iso

IGNORE_DIR_NAMES = {
    ".spotlight-v100", ".trashes", ".fseventsd", ".temporaryitems", "$recycle.bin",
    "system volume information", "recycler", "@eadir", ".git", "node_modules",
    "__pycache__", ".documentrevisions-v100",
}
IGNORE_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db"}
# Files larger than this are catalogued but never fingerprinted by default
# (configurable): prevents multi-GB single files from stalling a first scan.
DEFAULT_MAX_HASH_BYTES = 64 * 1024 ** 3


@dataclass
class RootStatus:
    root_id: str
    online: bool
    detail: str
    path_exists: bool


@dataclass
class ScanResult:
    scan_id: str
    root_id: str
    status: str
    added: int = 0
    changed: int = 0
    moved: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: int = 0
    hashed: int = 0
    error: str | None = None
    duration_s: float = 0.0
    _moved_detail: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d.pop("_moved_detail", None)
        return d


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

def register_root(conn, abs_path: str, label: str | None = None,
                  volume_hint: str | None = None) -> str:
    """Register a folder/drive as an indexable root. Idempotent per path."""
    resolved = str(Path(abs_path).expanduser().resolve())
    row = conn.execute("SELECT id FROM roots WHERE abs_path=?", (resolved,)).fetchone()
    if row:
        return row["id"]
    root_id = str(uuid.uuid4())
    ts = now_iso()
    conn.execute(
        """INSERT INTO roots(id, label, abs_path, volume_hint, created_at, online,
                             last_online_at, read_only)
           VALUES(?,?,?,?,?,0,NULL,1)""",
        (root_id, label or Path(resolved).name or resolved, resolved, volume_hint, ts),
    )
    log_event(conn, "root.registered", subject=root_id, detail=resolved)
    conn.commit()
    return root_id


def root_path(conn, root_id: str) -> str:
    row = conn.execute("SELECT abs_path FROM roots WHERE id=?", (root_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown root {root_id}")
    return row["abs_path"]


def check_root_online(conn, root_id: str, persist: bool = True) -> RootStatus:
    """Is this root currently usable for serving originals?

    Two independent things decide that:
      * owner intent  (`share_enabled`) -- the owner may pause sharing even while
        the drive is physically attached;
      * reachability  (does the path exist and can we list it).

    Owner intent wins: pausing sharing is never silently undone by a probe.
    A genuinely unplugged drive keeps share_enabled=1 and simply fails the
    probe, so it recovers by itself when the drive comes back.

    V1 scope is directly attached drives (blueprint default). A NAS/SMB path
    behaves the same way once the OS has it mounted; that combination is
    UNTESTED and listed in docs/RISKS.md.
    """
    row = conn.execute(
        "SELECT abs_path, online, share_enabled FROM roots WHERE id=?", (root_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown root {root_id}")
    path = row["abs_path"]
    if not int(row["share_enabled"]):
        detail = "sharing paused by owner"
        exists = Path(path).exists()
        if persist:
            conn.execute("UPDATE roots SET online=0, last_seen_at=? WHERE id=?",
                         (now_iso(), root_id))
            conn.commit()
        return RootStatus(root_id=root_id, online=False, detail=detail, path_exists=exists)

    p = Path(path)
    exists = p.exists()
    is_dir = exists and p.is_dir()
    readable = False
    detail = "path missing"
    if is_dir:
        try:
            with os.scandir(path) as it:
                next(it, None)
            readable = True
            detail = "reachable"
        except PermissionError:
            detail = "permission denied"
        except OSError as exc:
            detail = f"io error: {exc.__class__.__name__}"
    online = bool(is_dir and readable)
    if persist:
        ts = now_iso()
        prev_online = bool(row["online"])
        conn.execute(
            "UPDATE roots SET online=?, last_seen_at=?, "
            "last_online_at=CASE WHEN ? THEN ? ELSE last_online_at END WHERE id=?",
            (1 if online else 0, ts, 1 if online else 0, ts, root_id),
        )
        if prev_online != online:
            log_event(conn, "root.online" if online else "root.offline",
                      subject=root_id, detail=detail)
        conn.commit()
    return RootStatus(root_id=root_id, online=online, detail=detail, path_exists=exists)


def pause_sharing(conn, root_id: str, reason: str = "disconnect drill") -> None:
    """Owner-initiated: stop serving originals from this root.

    Catalogue, previews and search keep working; originals become unavailable.
    This is what the "Disconnect drive" drill does, and it is also the primitive
    behind revoking a collaborator's access to a drive.
    """
    conn.execute("UPDATE roots SET online=0, share_enabled=0, last_seen_at=? WHERE id=?",
                 (now_iso(), root_id))
    log_event(conn, "root.sharing_paused", subject=root_id, detail=reason)
    conn.commit()


def resume_sharing(conn, root_id: str) -> RootStatus:
    """Owner-initiated: allow serving again, then re-probe reachability."""
    conn.execute("UPDATE roots SET share_enabled=1 WHERE id=?", (root_id,))
    conn.commit()
    log_event(conn, "root.sharing_resumed", subject=root_id)
    return check_root_online(conn, root_id)


# Kept for call sites/tests that simulate an unplug: same as pause_sharing.
mark_root_offline = pause_sharing


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _iter_files(base: Path):
    """Yield (rel_posix_path, os.stat_result) for regular files, never following
    symlinks and never leaving `base`."""
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except (PermissionError, OSError):
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() in IGNORE_DIR_NAMES or entry.name.startswith("."):
                        continue
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    if entry.name.lower() in IGNORE_FILE_NAMES:
                        continue
                    st = entry.stat(follow_symlinks=False)
                    rel = os.path.relpath(entry.path, base).replace(os.sep, "/")
                    yield rel, st
            except (FileNotFoundError, PermissionError, OSError):
                continue


def _fts_upsert(conn, asset_id: str, name: str, rel_path: str) -> None:
    row = conn.execute(
        "SELECT tags, transcript FROM asset_search WHERE asset_id=? LIMIT 1", (asset_id,)
    ).fetchone()
    tags = row["tags"] if row else None
    transcript = row["transcript"] if row else None
    conn.execute("DELETE FROM asset_search WHERE asset_id=?", (asset_id,))
    conn.execute(
        "INSERT INTO asset_search(asset_id, name, rel_path, tags, transcript) VALUES(?,?,?,?,?)",
        (asset_id, name, rel_path, tags, transcript),
    )


def scan_root(conn, root_id: str, *, max_hash_bytes: int = DEFAULT_MAX_HASH_BYTES,
              force_hash: bool = False, ignore_offline: bool = False) -> ScanResult:
    """Incrementally reconcile the catalogue with the filesystem."""
    import time

    started = time.monotonic()
    scan_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO scan_runs(id, root_id, started_at, status) VALUES(?,?,?,'running')",
        (scan_id, root_id, now_iso()),
    )
    conn.commit()

    status = check_root_online(conn, root_id)
    if not status.online and not ignore_offline:
        # CRITICAL: never infer deletion from an unreachable drive.
        conn.execute(
            "UPDATE scan_runs SET status='skipped_offline', finished_at=? WHERE id=?",
            (now_iso(), scan_id),
        )
        conn.commit()
        log_event(conn, "scan.skipped_offline", subject=root_id,
                  detail=f"scan {scan_id}: {status.detail}")
        return ScanResult(scan_id, root_id, "skipped_offline", error=status.detail)

    base = Path(root_path(conn, root_id))
    result = ScanResult(scan_id, root_id, "ok")

    live = {
        r["rel_path"]: r
        for r in conn.execute(
            "SELECT * FROM assets WHERE root_id=? AND deleted_at IS NULL", (root_id,)
        )
    }
    # fingerprint -> asset_id for assets not yet seen in this pass (move candidates)
    by_fp: dict[str, list] = {}
    for row in live.values():
        if row["fingerprint"]:
            by_fp.setdefault(row["fingerprint"], []).append(row)

    seen_paths: set[str] = set()
    ts = now_iso()

    for rel, st in _iter_files(base):
        seen_paths.add(rel)
        name = os.path.basename(rel)
        ext = os.path.splitext(name)[1].lower()
        media_kind = identity.guess_media_kind(name)
        sig_changed: bool
        row = live.get(rel)

        if row is not None:
            same_size = row["size_bytes"] == st.st_size
            same_mtime = row["mtime"] == str(st.st_mtime_ns)
            if same_size and same_mtime and not force_hash:
                result.unchanged += 1
                conn.execute("UPDATE assets SET last_seen_at=? WHERE id=?", (ts, row["id"]))
                continue
            # Possible modification: confirm with a content fingerprint.
            fp = None
            if st.st_size <= max_hash_bytes:
                try:
                    fp = identity.sampled_fingerprint(base / rel, st.st_size)
                    result.hashed += 1
                except OSError:
                    fp = None
            if fp and row["fingerprint"] == fp:
                # touch-only change (mtime bumped, bytes identical)
                result.unchanged += 1
                conn.execute(
                    "UPDATE assets SET mtime=?, size_bytes=?, last_seen_at=?, updated_at=? WHERE id=?",
                    (str(st.st_mtime_ns), st.st_size, ts, ts, row["id"]),
                )
                continue
            conn.execute(
                """UPDATE assets SET size_bytes=?, mtime=?, fingerprint=?, fingerprint_algo=?,
                          media_kind=?, ext=?, last_seen_at=?, updated_at=?,
                          probe_state=CASE WHEN ? THEN 'unprobed' ELSE probe_state END
                   WHERE id=?""",
                (st.st_size, str(st.st_mtime_ns), fp, identity.FINGERPRINT_ALGO, media_kind,
                 ext, ts, ts, 1 if fp else 0, row["id"]),
            )
            _fts_upsert(conn, row["id"], name, rel)
            result.changed += 1
            continue

        # New path. Hash it (bounded) to see whether it is actually a moved file.
        fp = None
        if st.st_size <= max_hash_bytes:
            try:
                fp = identity.sampled_fingerprint(base / rel, st.st_size)
                result.hashed += 1
            except OSError:
                fp = None

        moved_from = None
        if fp:
            candidates = [
                c for c in by_fp.get(fp, [])
                if c["rel_path"] not in seen_paths and c["deleted_at"] is None
                # The old path must genuinely be gone from the filesystem.
                # Identical-content duplicates (very common on camera cards)
                # must NOT be mistaken for a move and steal an asset id.
                and not (base / c["rel_path"]).exists()
            ]
            if candidates:
                moved_from = candidates[0]

        if moved_from is not None:
            old_rel = moved_from["rel_path"]
            conn.execute(
                "UPDATE assets SET rel_path=?, name=?, ext=?, media_kind=?, size_bytes=?, mtime=?, last_seen_at=?, updated_at=? WHERE id=?",
                (rel, name, ext, media_kind, st.st_size, str(st.st_mtime_ns), ts, ts, moved_from["id"]),
            )
            conn.execute(
                """INSERT INTO asset_path_history(asset_id, rel_path, first_seen_at, last_seen_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(asset_id, rel_path) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (moved_from["id"], old_rel, moved_from["first_seen_at"], ts),
            )
            conn.execute(
                """INSERT INTO asset_path_history(asset_id, rel_path, first_seen_at, last_seen_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(asset_id, rel_path) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (moved_from["id"], rel, ts, ts),
            )
            _fts_upsert(conn, moved_from["id"], name, rel)
            result.moved += 1
            result._moved_detail.append({"asset_id": moved_from["id"], "from": old_rel, "to": rel})
            by_fp.pop(fp, None)
            continue

        asset_id = identity.new_asset_id()
        conn.execute(
            """INSERT INTO assets(id, root_id, rel_path, name, ext, size_bytes, mtime,
                                  fingerprint, fingerprint_algo, media_kind, probe_state,
                                  first_seen_at, last_seen_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,'unprobed',?,?,?)""",
            (asset_id, root_id, rel, name, ext, st.st_size, str(st.st_mtime_ns), fp,
             identity.FINGERPRINT_ALGO, media_kind, ts, ts, ts),
        )
        conn.execute(
            """INSERT INTO asset_path_history(asset_id, rel_path, first_seen_at, last_seen_at)
               VALUES(?,?,?,?)""",
            (asset_id, rel, ts, ts),
        )
        _fts_upsert(conn, asset_id, name, rel)
        result.added += 1
        if fp:
            by_fp.setdefault(fp, []).append(
                {"id": asset_id, "rel_path": rel, "deleted_at": None, "first_seen_at": ts}
            )

    # Tombstone only now that we know the root was actually walked, and never
    # tombstone a row we relocated during this pass (its path changed).
    for rel, row in live.items():
        current = conn.execute("SELECT rel_path, deleted_at FROM assets WHERE id=?",
                               (row["id"],)).fetchone()
        if current is None or current["deleted_at"] is not None:
            continue
        if current["rel_path"] in seen_paths or current["rel_path"] != rel:
            continue
        if rel in seen_paths:
            continue
        conn.execute(
            "UPDATE assets SET deleted_at=?, deleted_reason='absent_on_reachable_root', updated_at=? WHERE id=?",
            (ts, ts, row["id"]),
        )
        conn.execute("DELETE FROM asset_search WHERE asset_id=?", (row["id"],))
        result.removed += 1

    result.duration_s = round(time.monotonic() - started, 4)
    finished = now_iso()
    conn.execute(
        """UPDATE scan_runs SET finished_at=?, status=?, added=?, changed=?, moved=?,
                  removed=?, unchanged=?, skipped=?, hashed=? WHERE id=?""",
        (finished, result.status, result.added, result.changed, result.moved,
         result.removed, result.unchanged, result.skipped, result.hashed, scan_id),
    )
    conn.execute(
        "UPDATE roots SET last_scan_id=?, online=1, last_seen_at=?, last_online_at=? WHERE id=?",
        (scan_id, finished, finished, root_id),
    )
    log_event(conn, "scan.completed", subject=root_id,
              detail=f"{result.added}+ {result.changed}~ {result.moved}> {result.removed}- "
                     f"{result.unchanged}= in {result.duration_s}s")
    conn.commit()
    return result
