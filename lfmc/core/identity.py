"""Stable asset identity and cheap content fingerprints.

Why not use the path as identity?
---------------------------------
The blueprint requires that a drive rename, remount, drive-letter change or a
file move must not orphan comments, selects or AI metadata. So:

* asset id  = random UUID4, generated once, never derived from a path
* fingerprint = sampled content hash, used to recognise the *same bytes*
                arriving at a *new* path (move/rename detection)

Fingerprint caveat (deliberate, documented): this is a sampled hash (size +
first/middle/last block), not a full-file cryptographic digest. It is fast
enough to run over a 4 TB drive and strong enough to recognise a moved camera
file. It is NOT proof of identity against an adversary who crafts files with
matching head/middle/tail. `full_fingerprint()` exists for spot verification.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

FINGERPRINT_ALGO = "blake2b-sampled-v1"
CHUNK = 1 << 20  # 1 MiB

VIDEO_EXT = {
    ".mp4", ".mov", ".mxf", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".braw",
    ".r3d", ".ari", ".crm", ".mxf", ".dv", ".webm", ".wmv", ".mpg", ".mpeg",
}
AUDIO_EXT = {".wav", ".bwf", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".exr", ".psd"}
NLE_PROJECT_EXT = {".prproj", ".drp", ".aep", ".fcpbundle", ".fcpxml", ".otio", ".edl", ".xml"}
SIDECAR_EXT = {".srt", ".vtt", ".scc", ".txt", ".md5", ".json", ".ale", ".cdl"}

# Extensions that we must never treat as ordinary documents to sync/merge.
PROPRIETARY_PROJECT_EXT = {".prproj", ".drp", ".aep"}


def new_asset_id() -> str:
    return str(uuid.uuid4())


def guess_media_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    if ext in NLE_PROJECT_EXT:
        return "nle_project"
    if ext in SIDECAR_EXT:
        return "sidecar"
    return "other"


def is_proprietary_project(name: str) -> bool:
    """True for files we must never byte-merge or sync as documents."""
    return Path(name).suffix.lower() in PROPRIETARY_PROJECT_EXT


def sampled_fingerprint(path: str | Path, size: int | None = None) -> str:
    """blake2b over size + up to three 1 MiB blocks (head, middle, tail).

    Cost is bounded (<= 3 MiB read per file) so a first scan of a large drive
    stays interactive. Deterministic for a given file size/content.
    """
    p = Path(path)
    if size is None:
        size = p.stat().st_size
    h = hashlib.blake2b(digest_size=32)
    h.update(b"v1|")
    h.update(str(size).encode())

    if size <= 3 * CHUNK:
        # Small file: just hash the whole thing.
        with p.open("rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest()

    offsets = (0, max(0, size // 2 - CHUNK // 2), max(0, size - CHUNK))
    with p.open("rb") as fh:
        for off in offsets:
            fh.seek(off)
            h.update(fh.read(CHUNK))
    return h.hexdigest()


def full_fingerprint(path: str | Path) -> str:
    """Full-file blake2b. Only for verification/repair, never in the hot path."""
    h = hashlib.blake2b(digest_size=32)
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(4 * CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def stat_signature(st: os.stat_result) -> tuple[int, int]:
    """Fast change-detection signature: (size, mtime_ns).

    Deliberately cheap: the scanner only hashes when this changes or when a
    path is new. mtime granularity across SMB/exFAT can be coarse; a changed
    size always wins, and a content hash is used to confirm real changes.
    """
    return (st.st_size, st.st_mtime_ns)
