"""Read-only WebDAV façade over the local catalogue and range server.

Why WebDAV
----------
The blueprint's Gate A needs a *mounted/browsed* location that Premiere Pro and
Resolve can open media from. Building our own filesystem would require a kernel
driver: WinFsp needs a US$6,000/3yr commercial licence for closed-source
distribution and macFUSE requires a paid licence to bundle with commercial
software (see docs/COSTS.md E1/E2). WebDAV avoids both: macOS Finder and the
Windows WebDAV redirector can mount it natively, at $0, with no third-party
component.

Design rules
------------
* **Read-only.** PUT/DELETE/MKCOL/MOVE/COPY/PROPPATCH are refused with 405 and
  never touch the user's drive. LOCK/UNLOCK return 501 (unsupported, not faked).
* **Offline is not missing.** If the drive/host is offline, a GET returns
  **503** with an explanation instead of 404. A 404 would tell the NLE the clip
  was deleted and invite a relink/cache-invalidation dance; 503 is the honest
  "temporarily unavailable" that the protocol provides for exactly this case.
* **Catalogue-derived listings.** PROPFIND works with the drive unplugged, which
  is how a remote editor can still browse structure and see what exists.
* **Stable namespace.** `<root label>/<relative path>`; labels are made unique so
  two drives called "Camera" cannot collide.
"""

from __future__ import annotations

import email.utils
import os
import posixpath
import urllib.parse
from dataclasses import dataclass
from xml.sax.saxutils import escape

from ..core import catalogue

DAV_NS = "DAV:"
READ_ONLY_METHODS = ("OPTIONS", "GET", "HEAD", "PROPFIND")
WRITE_METHODS = ("PUT", "DELETE", "MKCOL", "MOVE", "COPY", "PROPPATCH")
LOCK_METHODS = ("LOCK", "UNLOCK")


@dataclass
class DavNode:
    kind: str                    # "collection" | "file" | "missing"
    dav_path: str                # canonical, unquoted, leading "/"
    root_id: str | None = None
    rel_path: str = ""           # "" for the top collection / a root collection
    asset: dict | None = None
    top_level: bool = False


def root_display_names(conn) -> dict[str, str]:
    """root_id -> unique display name used as the first WebDAV path segment."""
    rows = conn.execute("SELECT id, label FROM roots ORDER BY created_at").fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    out: dict[str, str] = {}
    for r in rows:
        label = r["label"] or r["id"][:8]
        out[r["id"]] = label if counts[label] == 1 else f"{label} [{r['id'][:8]}]"
    return out


def _name_to_root(conn) -> dict[str, str]:
    return {name: rid for rid, name in root_display_names(conn).items()}


def _segments(raw_path: str) -> list[str]:
    """Split a URL path into decoded segments, rejecting traversal attempts."""
    path = urllib.parse.unquote(raw_path or "/")
    if not path.startswith("/dav"):
        return []
    rest = path[len("/dav"):]
    raw_segments = [s for s in rest.split("/") if s not in ("",)]
    cleaned: list[str] = []
    for seg in raw_segments:
        if seg in (".", "..") or "/" in seg or "\\" in seg or "\x00" in seg:
            raise ValueError("invalid path segment")
        cleaned.append(seg)
    return cleaned


def resolve(conn, raw_path: str) -> DavNode:
    """Map a request path to a catalogue node. Never touches the filesystem."""
    try:
        segments = _segments(raw_path)
    except ValueError:
        return DavNode(kind="missing", dav_path="/dav/")
    if not segments:
        return DavNode(kind="collection", dav_path="/dav/", top_level=True)

    by_name = _name_to_root(conn)
    root_id = by_name.get(segments[0])
    if root_id is None:
        return DavNode(kind="missing", dav_path="/dav/")
    rel = "/".join(segments[1:])
    canonical = "/dav/" + "/".join(segments) + ("/" if not rel else "")

    if not rel:
        return DavNode(kind="collection", dav_path=canonical, root_id=root_id, rel_path="")

    row = conn.execute(
        "SELECT * FROM assets WHERE root_id=? AND rel_path=? AND deleted_at IS NULL",
        (root_id, rel),
    ).fetchone()
    if row is not None:
        return DavNode(kind="file", dav_path=canonical, root_id=root_id,
                       rel_path=rel, asset=dict(row))

    prefix = rel.rstrip("/") + "/"
    exists = conn.execute(
        "SELECT 1 FROM assets WHERE root_id=? AND deleted_at IS NULL AND rel_path LIKE ? LIMIT 1",
        (root_id, prefix + "%"),
    ).fetchone()
    if exists is not None:
        return DavNode(kind="collection", dav_path=canonical.rstrip("/") + "/",
                       root_id=root_id, rel_path=rel)
    return DavNode(kind="missing", dav_path=canonical)


def _href(*segments: str) -> str:
    """Raw (unencoded) canonical path. Encoding happens exactly once, at render."""
    return "/dav/" + "/".join(s for s in segments if s != "")


def _encode_href(path: str) -> str:
    """Percent-encode a raw canonical path for XML/HTML output."""
    return urllib.parse.quote(path, safe="/")


def _rfc1123(mtime_ns: str | int | None, fallback: str | None = None) -> str:
    try:
        return email.utils.formatdate(int(mtime_ns) / 1e9, usegmt=True)
    except (TypeError, ValueError):
        if fallback:
            try:
                import datetime as _dt
                dt = _dt.datetime.fromisoformat(fallback)
                return email.utils.formatdate(dt.timestamp(), usegmt=True)
            except ValueError:
                pass
    return email.utils.formatdate(usegmt=True)


def _propstat(props_xml: str) -> str:
    return (
        "<D:propstat><D:prop>" + props_xml + "</D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
    )


def _collection_response(dav_path: str, displayname: str, mtime: str | None = None) -> str:
    props = (
        f"<D:displayname>{escape(displayname)}</D:displayname>"
        "<D:resourcetype><D:collection/></D:resourcetype>"
        f"<D:getlastmodified>{_rfc1123(None, mtime)}</D:getlastmodified>"
    )
    return (
        f"<D:response><D:href>{escape(_encode_href(dav_path))}</D:href>"
        f"{_propstat(props)}</D:response>"
    )


def _file_response(node: DavNode) -> str:
    asset = node.asset or {}
    ctype = _content_type(asset.get("name", ""))
    etag = f'"{asset.get("fingerprint") or "nofp"}-{asset.get("size_bytes") or 0}"'
    props = (
        f"<D:displayname>{escape(asset.get('name') or '')}</D:displayname>"
        "<D:resourcetype/>"
        f"<D:getcontentlength>{int(asset.get('size_bytes') or 0)}</D:getcontentlength>"
        f"<D:getcontenttype>{escape(ctype)}</D:getcontenttype>"
        f"<D:getlastmodified>{_rfc1123(asset.get('mtime'))}</D:getlastmodified>"
        f"<D:getetag>{escape(etag)}</D:getetag>"
        "<D:supportedlock/>"
    )
    return (
        f"<D:response><D:href>{escape(_encode_href(node.dav_path))}</D:href>"
        f"{_propstat(props)}</D:response>"
    )


def _content_type(name: str) -> str:
    import mimetypes
    guessed = mimetypes.guess_type(name)[0]
    if guessed:
        return guessed
    ext = os.path.splitext(name)[1].lower()
    return {
        ".mxf": "application/mxf", ".braw": "application/octet-stream",
        ".r3d": "application/octet-stream", ".prproj": "application/xml",
        ".drp": "application/octet-stream", ".wav": "audio/wav",
    }.get(ext, "application/octet-stream")


def children(conn, node: DavNode) -> list[DavNode]:
    """Immediate children of a collection, derived from the catalogue only."""
    out: list[DavNode] = []
    if node.top_level:
        for rid, name in root_display_names(conn).items():
            out.append(DavNode(kind="collection", dav_path=_href(name) + "/",
                               root_id=rid, rel_path=""))
        return out
    if node.root_id is None:
        return out
    # Every child path must stay anchored at the root's display name; dropping it
    # produces hrefs that do not resolve back to the same resource.
    root_name = root_display_names(conn).get(node.root_id)
    if root_name is None:
        return out
    listing = catalogue.browse(conn, node.root_id, node.rel_path, limit=5000)
    for folder in listing["folders"]:
        segments = [root_name] + [p for p in folder["path"].split("/") if p]
        out.append(DavNode(
            kind="collection",
            dav_path=_href(*segments) + "/",
            root_id=node.root_id,
            rel_path=folder["path"],
        ))
    for row in listing["files"]:
        segments = [root_name] + row["rel_path"].split("/")
        out.append(DavNode(
            kind="file",
            dav_path=_href(*segments),
            root_id=node.root_id,
            rel_path=row["rel_path"],
            asset=row,
        ))
    return out


def multistatus(conn, node: DavNode, depth: str = "0") -> bytes:
    """Build a 207 Multi-Status body for PROPFIND."""
    depth = (depth or "0").strip().lower()
    parts: list[str] = []
    if node.kind == "file":
        parts.append(_file_response(node))
    elif node.kind == "collection":
        display = "dav" if node.top_level else posixpath.basename(node.dav_path.rstrip("/"))
        parts.append(_collection_response(node.dav_path, display))
        if depth != "0":
            # Depth: infinity is deliberately capped at 1: a recursive walk of a
            # 4 TB drive is a denial-of-service on ourselves and on the client.
            for child in children(conn, node):
                if child.kind == "collection":
                    parts.append(_collection_response(
                        child.dav_path, posixpath.basename(child.dav_path.rstrip("/"))))
                else:
                    parts.append(_file_response(child))
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<D:multistatus xmlns:D="{DAV_NS}">' + "".join(parts) + "</D:multistatus>"
    )
    return body.encode("utf-8")


def listing_html(conn, node: DavNode) -> bytes:
    """Human-friendly view of a collection (used when a browser GETs /dav/...)."""
    items = []
    if node.top_level:
        rows = [(n.dav_path, posixpath.basename(n.dav_path.rstrip("/")) + "/", "collection")
                for n in children(conn, node)]
    else:
        rows = []
        for child in children(conn, node):
            if child.kind == "collection":
                rows.append((child.dav_path, posixpath.basename(child.dav_path.rstrip("/")) + "/",
                             "collection"))
            else:
                size = int((child.asset or {}).get("size_bytes") or 0)
                rows.append((child.dav_path, (child.asset or {}).get("name", "?"), f"{size} bytes"))
    for href, name, meta in rows:
        items.append(f'<li><a href="{escape(_encode_href(href))}">{escape(name)}</a> '
                     f'<span>{escape(meta)}</span></li>')
    parent = ""
    if not node.top_level and node.dav_path != "/dav/":
        parent_path = posixpath.dirname(node.dav_path.rstrip("/")) or "/dav"
        parent = f'<li><a href="{escape(parent_path)}/">↑ up</a></li>'
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>lfmc dav</title>"
        "<style>body{font:14px -apple-system,Segoe UI,sans-serif;background:#0d1117;color:#e6edf3;"
        "padding:24px}a{color:#58a6ff;text-decoration:none}li{margin:4px 0}"
        "span{color:#8b98a5;font-size:12px;margin-left:8px}</style></head><body>"
        f"<h3>Read-only WebDAV: {escape(node.dav_path)}</h3>"
        "<p style='color:#8b98a5;font-size:12px'>Originals are served on demand from the "
        "host drive. A 503 means the host or drive is offline; the file is not deleted.</p>"
        f"<ul>{parent}{''.join(items)}</ul></body></html>"
    )
    return html.encode("utf-8")


def preflight_hint() -> dict:
    """Facts a user needs before trusting a mount (surfaced in docs + UI)."""
    return {
        "protocol": "WebDAV, read-only",
        "auth": "HTTP Basic (password = the host bearer token)",
        "transport_security": "none yet — LAN/trusted VPN only until TLS lands",
        "offline_semantics": "503 Service Unavailable, never 404, so NLEs do not treat "
                             "an offline host as deleted media",
        "depth": "0 and 1 supported; infinity is capped at 1",
        "write_operations": "refused with 405; LOCK/UNLOCK unsupported (501)",
    }
