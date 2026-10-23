"""Local catalogue database (SQLite, stdlib only).

Design notes
------------
* One file-backed SQLite database per host agent install. No server, no service.
* WAL mode so the HTTP layer can read while a scan writes.
* FTS5 provides offline full-text search over the catalogue. Search works with
  zero network and zero source drive, which is the blueprint's
  "catalogue persists when the drive is disconnected" requirement.
* Asset identity is a random stable ID, NOT a path. Paths are attributes that
  can change (rename, remount, drive letter change, macOS volume rename).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- A storage root is a user-selected folder/drive that we are allowed to index.
-- Nothing outside a registered root is ever scanned or served.
CREATE TABLE IF NOT EXISTS roots (
    id             TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    abs_path       TEXT NOT NULL,
    volume_hint    TEXT,
    created_at     TEXT NOT NULL,
    online         INTEGER NOT NULL DEFAULT 0,
    last_online_at TEXT,
    last_seen_at   TEXT,
    last_scan_id   TEXT,
    read_only      INTEGER NOT NULL DEFAULT 1,
    share_enabled  INTEGER NOT NULL DEFAULT 1,   -- owner intent; 0 = do not serve
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id               TEXT PRIMARY KEY,
    root_id          TEXT NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    rel_path         TEXT NOT NULL,          -- POSIX-style, relative to root
    name             TEXT NOT NULL,
    ext              TEXT,
    size_bytes       INTEGER,
    mtime            TEXT,
    fingerprint      TEXT,                   -- sampled content hash, not full-file
    fingerprint_algo TEXT,
    media_kind       TEXT,                   -- video | audio | image | other
    duration_s       REAL,
    fps              REAL,
    width            INTEGER,
    height           INTEGER,
    codec            TEXT,
    probe_state      TEXT NOT NULL DEFAULT 'unprobed',
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    deleted_at       TEXT,                   -- ONLY set when root is online and
                                             -- absence was confirmed by a scan
    deleted_reason   TEXT,
    updated_at       TEXT NOT NULL
);

-- A live file path is unique per root. Deleted (tombstoned) rows are allowed to
-- share a path with a newer asset, so this is a partial index.
CREATE UNIQUE INDEX IF NOT EXISTS assets_live_path
    ON assets(root_id, rel_path) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS assets_root_idx ON assets(root_id);
CREATE INDEX IF NOT EXISTS assets_deleted_idx ON assets(deleted_at);
CREATE INDEX IF NOT EXISTS assets_fingerprint_idx ON assets(fingerprint);

-- Path history keeps provenance across renames/moves so a moved file keeps its
-- comments, selects and AI metadata (stable asset ID is canonical).
CREATE TABLE IF NOT EXISTS asset_path_history (
    asset_id      TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (asset_id, rel_path)
);

-- Derived searchable text. Kept separate from `assets` so AI text (tags,
-- transcripts) can be deleted independently of the catalogue entry.
CREATE VIRTUAL TABLE IF NOT EXISTS asset_search USING fts5(
    asset_id UNINDEXED,
    name,
    rel_path,
    tags,
    transcript
);

-- Cached previews/thumbnails/proxies. `location` says where the bytes live so
-- the UI can distinguish "original reachable" from "only a cached preview".
CREATE TABLE IF NOT EXISTS cached_previews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,       -- thumbnail | proxy | none
    location   TEXT NOT NULL,       -- host | remote-cache
    path       TEXT NOT NULL,
    bytes      INTEGER,
    created_at TEXT NOT NULL,
    source     TEXT
);
CREATE INDEX IF NOT EXISTS cached_previews_asset_idx ON cached_previews(asset_id);

-- Byte accounting for the "no full-file staging" proof. Every ranged read is
-- logged so we can show bytes-on-wire vs file size per open/seek session.
CREATE TABLE IF NOT EXISTS transfer_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    asset_id   TEXT,
    session_id TEXT,
    start_byte INTEGER,
    end_byte   INTEGER,
    bytes      INTEGER NOT NULL,
    client     TEXT,
    outcome    TEXT NOT NULL          -- ok | denied | not_found | range_not_satisfiable
);
CREATE INDEX IF NOT EXISTS transfer_log_session_idx ON transfer_log(session_id);

CREATE TABLE IF NOT EXISTS scan_runs (
    id          TEXT PRIMARY KEY,
    root_id     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,       -- running | ok | aborted | error
    added       INTEGER NOT NULL DEFAULT 0,
    changed     INTEGER NOT NULL DEFAULT 0,
    moved       INTEGER NOT NULL DEFAULT 0,
    removed     INTEGER NOT NULL DEFAULT 0,
    unchanged   INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    hashed      INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

-- Append-only audit trail (who touched what, when). No client PII beyond an
-- optional display label supplied by the reviewer.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    actor   TEXT,
    subject TEXT,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS events_ts_idx ON events(ts);

-- ---------------------------------------------------------------------------
-- NLE project collaboration (references only -- never a copy of project files)
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Review: comments, links, selects, versions (see core/review.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_comments (
    id              TEXT PRIMARY KEY,
    asset_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    version_label   TEXT NOT NULL DEFAULT 'v1',
    parent_id       TEXT REFERENCES asset_comments(id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    author_kind     TEXT NOT NULL DEFAULT 'member',   -- member | guest
    body            TEXT NOT NULL,
    timecode_ms     INTEGER,
    timecode_end_ms INTEGER,
    fps             REAL NOT NULL DEFAULT 25.0,
    visibility      TEXT NOT NULL DEFAULT 'internal', -- internal | shared
    resolved_at     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS asset_comments_asset_idx ON asset_comments(asset_id, version_label);

CREATE TABLE IF NOT EXISTS comment_reactions (
    comment_id TEXT NOT NULL REFERENCES asset_comments(id) ON DELETE CASCADE,
    emoji      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (comment_id, emoji, actor)
);

-- Scoped accountless review links. Only the hash of the token is stored.
CREATE TABLE IF NOT EXISTS review_links (
    id              TEXT PRIMARY KEY,
    token_hash      TEXT NOT NULL UNIQUE,
    asset_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    version_label   TEXT NOT NULL DEFAULT 'v1',
    scope           TEXT NOT NULL DEFAULT 'asset',
    allow_comments  INTEGER NOT NULL DEFAULT 1,
    allow_download  INTEGER NOT NULL DEFAULT 0,
    watermark       INTEGER NOT NULL DEFAULT 0,
    passphrase_salt TEXT,
    passphrase_hash TEXT,
    expires_at      TEXT,
    revoked_at      TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    last_used_at    TEXT,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS review_links_asset_idx ON review_links(asset_id);

-- Footage selects (mobile Keep/Maybe/Reject), bound to stable asset id + timecode.
CREATE TABLE IF NOT EXISTS selects (
    asset_id    TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    reviewer    TEXT NOT NULL,
    decision    TEXT NOT NULL CHECK (decision IN ('keep','maybe','reject')),
    note        TEXT,
    timecode_ms INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (asset_id, reviewer)
);

-- Version stacking: comments/links bind to a label, never silently to "latest".
CREATE TABLE IF NOT EXISTS asset_versions (
    asset_id    TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (asset_id, label)
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nle_bindings (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    nle                 TEXT NOT NULL,      -- premiere | resolve
    mode                TEXT NOT NULL CHECK (mode IN ('vendor_hosted','private_controlled')),
    adapter             TEXT NOT NULL,
    authoritative_state TEXT NOT NULL,      -- team_projects | productions_shared_storage | blackmagic_cloud_library | private_project_server
    service_location    TEXT,
    account_required    INTEGER NOT NULL DEFAULT 1,
    media_sync_enabled  INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'unverified',
    consent_recorded_at TEXT,
    consent_by          TEXT,
    disclosure          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (and initialise if needed) the catalogue database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Additive-only migrations. SQLite cannot drop/alter types, and we never
    rewrite user data to upgrade a schema."""
    wanted = {
        "assets": {"version_label": "TEXT NOT NULL DEFAULT 'v1'"},
    }
    for table, columns in wanted.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _add_missing_columns(conn)
    cur = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


def log_event(conn: sqlite3.Connection, kind: str, actor: str | None = None,
              subject: str | None = None, detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
        (now_iso(), kind, actor, subject, detail),
    )
