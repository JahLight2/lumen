"""Project hub: locks, version registry, conflict recovery.

Scope, stated narrowly and deliberately
---------------------------------------
This module manages *references and status* for an NLE project that lives on a
drive the team already owns. It does not store project contents, does not sync
project files, and does not merge anything.

The blueprint's hard rule is that a `.prproj` or `.drp` is never treated as a
document to be byte-merged. That rule is enforced structurally here:

* `register_version()` calls `os.stat()` and nothing else. It never opens the
  file for reading. The fingerprint it records is a *stat signature*
  (size + mtime_ns), not a content hash, precisely because a content hash would
  mean reading the bytes.
* There is no code path in this module that opens, copies, uploads or rewrites a
  project file. The only thing stored is the path string and a stat signature.
* Recovery cannot restore anything. It tells a human which version exists, where
  it is, and what to do in the NLE. Restoring is an editor action in Premiere or
  Resolve, not a platform action.

If a future change wants to read project bytes, it has to argue with this
docstring first, and it will be visible in review because `open()` does not
appear anywhere below.

Lock semantics
--------------
A lock is a *courtesy flag*, not a mutex. It tells a collaborator "someone is in
this project right now". It cannot stop an editor who ignores it, because the
platform has no authority over the filesystem. This is the same model the
blueprint accepts for shared drives, and it is why locks expire: a crashed
laptop must not block a team forever.

Expiry behaviour: an expired lock is *reclaimable*, not fatal. The caller gets
the lock and a `reclaimed_from` field naming the previous holder, so the conflict
is still visible in the event log rather than silently swallowed.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from .db import log_event, now_iso

DEFAULT_LOCK_TTL_MINUTES = 120
MAX_LOCK_TTL_MINUTES = 24 * 60

# Extensions we accept as project references. Anything else is probably a
# mistake (someone pointing at a folder or a proxy) and is rejected early.
PROJECT_EXTENSIONS = {".prproj", ".drp", ".aep", ".fcpxml", ".otio"}


class HubError(ValueError):
    """Invalid hub operation (maps to HTTP 400)."""


class ProjectNotFound(HubError):
    """Unknown project id (maps to HTTP 404)."""


class LockHeld(HubError):
    """Another collaborator holds a live lock (maps to HTTP 409)."""

    def __init__(self, message: str, lock: dict):
        super().__init__(message)
        self.lock = lock


def _parse(ts: str) -> datetime:
    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _iso(delta_minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat(
        timespec="seconds")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def create_project(conn, name: str, *, created_by: str = "owner") -> dict:
    name = (name or "").strip()
    if not name:
        raise HubError("project name is required")
    pid = str(uuid.uuid4())
    conn.execute("INSERT INTO projects(id, name, created_at) VALUES(?,?,?)",
                 (pid, name, now_iso()))
    log_event(conn, "project_created", created_by, pid, name)
    conn.commit()
    return get_project(conn, pid)


def get_project(conn, project_id: str) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise ProjectNotFound("unknown project")
    p = dict(row)
    p["lock"] = lock_state(conn, project_id)
    p["versions"] = len(list_versions(conn, project_id))
    return p


def list_projects(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        p = dict(r)
        p["lock"] = lock_state(conn, r["id"])
        versions = list_versions(conn, r["id"])
        p["versions"] = len(versions)
        p["latest_version"] = versions[-1]["label"] if versions else None
        out.append(p)
    return out


def delete_project(conn, project_id: str, *, actor: str = "owner") -> None:
    get_project(conn, project_id)
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    log_event(conn, "project_deleted", actor, project_id, None)
    conn.commit()


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

def lock_state(conn, project_id: str) -> dict | None:
    """Current lock if one is held, else None. Expired locks are reported with
    `expired: True` so callers can show "held by X, expired N minutes ago"."""
    row = conn.execute("SELECT * FROM project_locks WHERE project_id=?",
                       (project_id,)).fetchone()
    if row is None or row["released_at"] is not None:
        return None
    lock = dict(row)
    expired = _parse(lock["expires_at"]) <= datetime.now(timezone.utc)
    lock["expired"] = expired
    return lock


def acquire_lock(conn, project_id: str, *, holder: str,
                 ttl_minutes: float = DEFAULT_LOCK_TTL_MINUTES,
                 note: str | None = None) -> dict:
    """Take the project lock, or raise LockHeld naming the current holder.

    An expired lock owned by someone else is reclaimed rather than refused, and
    the response records who it was taken from.
    """
    get_project(conn, project_id)
    holder = (holder or "").strip()
    if not holder:
        raise HubError("holder is required")
    ttl = float(ttl_minutes or DEFAULT_LOCK_TTL_MINUTES)
    if ttl <= 0 or ttl > MAX_LOCK_TTL_MINUTES:
        raise HubError(f"ttl_minutes must be between 0 and {MAX_LOCK_TTL_MINUTES}")

    current = lock_state(conn, project_id)
    reclaimed_from = None
    if current is not None and not current["expired"]:
        if current["holder"] == holder:
            # Re-taking your own lock extends it instead of fighting yourself.
            conn.execute("UPDATE project_locks SET expires_at=?, note=? WHERE project_id=?",
                         (_iso(ttl), note or current.get("note"), project_id))
            conn.commit()
            return lock_state(conn, project_id) | {"extended": True}
        raise LockHeld(
            f"project is locked by {current['holder']} until {current['expires_at']}",
            current)
    if current is not None and current["expired"]:
        reclaimed_from = {"holder": current["holder"], "expired_at": current["expires_at"]}

    conn.execute(
        """INSERT INTO project_locks(project_id, holder, acquired_at, expires_at,
                                     released_at, note)
           VALUES(?,?,?,?,NULL,?)
           ON CONFLICT(project_id) DO UPDATE SET
             holder=excluded.holder, acquired_at=excluded.acquired_at,
             expires_at=excluded.expires_at, released_at=NULL, note=excluded.note""",
        (project_id, holder, now_iso(), _iso(ttl), note))
    log_event(conn, "project_locked", holder, project_id,
              f"reclaimed from {reclaimed_from['holder']}" if reclaimed_from else note)
    conn.commit()
    lock = lock_state(conn, project_id)
    lock["reclaimed_from"] = reclaimed_from
    return lock


def release_lock(conn, project_id: str, *, holder: str,
                 force: bool = False) -> dict:
    """Release the lock. Only the holder may release it, unless the host forces."""
    get_project(conn, project_id)
    current = conn.execute("SELECT * FROM project_locks WHERE project_id=?",
                           (project_id,)).fetchone()
    if current is None or current["released_at"] is not None:
        return {"project_id": project_id, "released": False,
                "reason": "no lock was held"}
    if current["holder"] != holder and not force:
        raise LockHeld(
            f"lock is held by {current['holder']}, not {holder}; "
            "ask them to release it or wait for expiry", dict(current))
    conn.execute("UPDATE project_locks SET released_at=? WHERE project_id=?",
                 (now_iso(), project_id))
    log_event(conn, "project_unlocked", holder, project_id,
              "forced" if (force and current["holder"] != holder) else None)
    conn.commit()
    return {"project_id": project_id, "released": True,
            "released_from": current["holder"]}


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def _stat_reference(source_path: str) -> dict:
    """Path + stat signature. No bytes are read from the project file.

    Deliberately NOT a content hash: hashing would mean reading a .prproj, which
    is the exact thing this module refuses to do. The trade-off is honest --
    this detects "the file changed on disk" (size/mtime), not "the contents are
    identical". For a project hub that is the right trade: the authoritative
    project lives in the NLE, not here.
    """
    p = source_path
    if not p:
        raise HubError("source_path is required")
    ext = os.path.splitext(p)[1].lower()
    if ext and ext not in PROJECT_EXTENSIONS:
        raise HubError(
            f"{ext} is not a recognised NLE project extension "
            f"({', '.join(sorted(PROJECT_EXTENSIONS))}); this hub stores references "
            "to project files, not arbitrary media")
    try:
        st = os.stat(p)          # stat only -- no open(), no read()
    except OSError as exc:
        raise HubError(f"cannot stat project file: {exc.strerror}") from exc
    return {"size_bytes": st.st_size,
            "fingerprint": f"stat:v1:{st.st_size}:{st.st_mtime_ns}",
            "mtime_ns": st.st_mtime_ns}


def register_version(conn, project_id: str, *, label: str, author: str,
                     source_path: str, note: str | None = None) -> dict:
    """Register a checkpoint: a path, a label, a stat signature.

    Refuses to proceed if the same label already exists for this project --
    silently overwriting a version label would make recovery lie.
    """
    get_project(conn, project_id)
    label = (label or "").strip()
    if not label:
        raise HubError("label is required")
    existing = conn.execute(
        "SELECT id FROM project_versions WHERE project_id=? AND label=?",
        (project_id, label)).fetchone()
    if existing is not None:
        raise HubError(f"version label {label!r} already exists for this project")

    ref = _stat_reference(source_path)
    vid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO project_versions(id, project_id, label, author, created_at,
                                        source_path, fingerprint, size_bytes, note)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (vid, project_id, label, author or "unknown", now_iso(), source_path,
         ref["fingerprint"], ref["size_bytes"], note))
    log_event(conn, "project_version_registered", author, project_id, label)
    conn.commit()
    return get_version(conn, vid)


def get_version(conn, version_id: str) -> dict:
    row = conn.execute("SELECT * FROM project_versions WHERE id=?",
                       (version_id,)).fetchone()
    if row is None:
        raise ProjectNotFound("unknown project version")
    return dict(row)


def list_versions(conn, project_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM project_versions WHERE project_id=?
           ORDER BY created_at, label""", (project_id,)).fetchall()
    out = []
    for r in rows:
        v = dict(r)
        # Report whether the file is *currently* where the registry says it is.
        # This is a stat, not a read, and it is the only live-filesystem check
        # this module performs after registration.
        v["present_on_disk"] = os.path.exists(v["source_path"])
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------

_NLE_INSTRUCTIONS = {
    ".prproj": ("Open the newest .prproj from the path below in Premiere Pro. "
                "Do NOT overwrite your local working copy with it; save a new "
                "sequence version and compare before committing."),
    ".drp": ("Import the newest .drp below in DaVinci Resolve via File > Import "
             "Project. Import does not replace an open project; verify the "
             "timeline before saving over anything."),
}
_GENERIC_INSTRUCTIONS = (
    "Open the newest project file below in its native application. Do not "
    "byte-merge project files, and do not let a sync client overwrite the newer "
    "copy. Save-as a new version, then reconcile manually.")


def recover(conn, project_id: str, *, actor: str = "owner") -> dict:
    """Conflict recovery report.

    Returns the newest registered version plus explicit human instructions. It
    deliberately performs NO restore: restoring a project file is an action in
    Premiere or Resolve, taken by a person who can see the timeline. A platform
    that silently swapped project files underneath an editor would be the
    hazard, not the fix.
    """
    project = get_project(conn, project_id)
    versions = list_versions(conn, project_id)
    lock = lock_state(conn, project_id)
    if not versions:
        result = {
            "project": {"id": project["id"], "name": project["name"]},
            "recoverable": False,
            "reason": "no versions have been registered for this project",
            "lock": lock,
            "instructions": ("Register a version from the editor's machine first. "
                             "This hub stores references only; it cannot reconstruct "
                             "a project that was never registered."),
            "byte_restore_available": False,
        }
        log_event(conn, "project_recover", actor, project_id, "no versions")
        conn.commit()
        return result

    newest = versions[-1]
    ext = os.path.splitext(newest["source_path"])[1].lower()
    instructions = _NLE_INSTRUCTIONS.get(ext, _GENERIC_INSTRUCTIONS)
    if not newest["present_on_disk"]:
        instructions = ("The registered path is NOT reachable right now. Connect the "
                        "drive or remount the share, then reopen the project. Do not "
                        "create a new empty project at that path.\n\n") + instructions

    result = {
        "project": {"id": project["id"], "name": project["name"]},
        "recoverable": bool(newest["present_on_disk"]),
        "newest": {k: newest[k] for k in
                   ("id", "label", "author", "created_at", "source_path",
                    "fingerprint", "size_bytes", "note", "present_on_disk")},
        "version_chain": [{"label": v["label"], "author": v["author"],
                           "created_at": v["created_at"],
                           "present_on_disk": v["present_on_disk"]}
                          for v in versions],
        "lock": lock,
        "instructions": instructions,
        "byte_restore_available": False,
        "note": ("This report references project files only. Nothing was copied, "
                 "merged or restored."),
    }
    log_event(conn, "project_recover", actor, project_id, newest["label"])
    conn.commit()
    return result
