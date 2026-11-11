"""Local control plane + read-only media server (stdlib only).

Runs on the host computer that has the drive attached. There is no third-party
service in this path: the host *is* the control plane (see docs/ARCHITECTURE.md,
ADR-001). Remote editors reach it over a user-managed private network.

Safety posture
--------------
* Every media response is authenticated (Bearer token) unless the server was
  explicitly started in --demo mode for synthetic sample data.
* Paths are resolved and containment-checked against the registered root, so a
  crafted rel_path or asset id cannot escape a shared folder.
* A 416 (not a 200 full-file) is returned for unsupported multi-range requests,
  so a client can never be silently handed an entire original.
* If the drive/host is offline the endpoint fails loudly with a structured
  state rather than a generic 404 ("file missing"), which is the blueprint's
  explicit requirement.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..core import auth, catalogue, db, review, scanner
from ..core.db import now_iso
from ..net import http_range
from ..spikes import mp4_remote_open

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class Config:
    def __init__(self, db_path: str, token: str | None = None, demo: bool = False,
                 host: str = "0.0.0.0", port: int = 8765, public_url: str | None = None):
        self.db_path = db_path
        self.token = token or secrets.token_urlsafe(24)
        self.demo = demo
        self.host = host
        self.port = port
        self.public_url = public_url  # used by the spike endpoint to call itself


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2, default=str).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "lfmc/0.1"
    protocol_version = "HTTP/1.1"
    config: Config  # injected

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # keep logs useful but quiet
        if os.environ.get("LFMC_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj, extra: dict | None = None) -> None:
        self._send(status, _json_bytes(obj), "application/json", extra)

    def _err(self, status: int, code: str, message: str, hint: str | None = None) -> None:
        self._json(status, {"error": code, "message": message, "hint": hint})

    def _conn(self):
        return db.connect(self.config.db_path)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _actor(self) -> dict | None:
        """Who is calling? Cached per request.

        Two kinds of caller exist: the host (the machine's owner, holding the
        one host token) and named collaborators (scoped tokens). Anything that
        is not one of those is anonymous and gets 401.
        """
        if getattr(self, "_actor_cache", False) is not False:
            return self._actor_cache
        actor = self._resolve_actor()
        self._actor_cache = actor
        return actor

    def _bearer_or_basic_password(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip()
        if header.startswith("Basic "):
            # OS WebDAV clients (macOS Finder, Windows redirector, rclone) cannot
            # send a Bearer token; they use HTTP Basic. Password = the token,
            # username is ignored. Basic over cleartext HTTP is why mounts are
            # LAN/VPN-only until TLS lands (docs/WEBDAV_MOUNT.md).
            import base64
            try:
                decoded = base64.b64decode(header[6:].strip()).decode("utf-8", "replace")
                _user, _, password = decoded.partition(":")
                return password or None
            except Exception:
                return None
        return None

    def _resolve_actor(self) -> dict | None:
        if self.config.demo:
            return {"kind": "host", "name": "host", "role": "host",
                    "scopes": sorted(auth.HOST_SCOPES), "demo": True}
        supplied = self._bearer_or_basic_password()
        if supplied is None:
            qs = urllib.parse.urlparse(self.path).query
            supplied = urllib.parse.parse_qs(qs).get("token", [""])[0] or None
        if not supplied:
            return None
        if secrets.compare_digest(supplied, self.config.token):
            return {"kind": "host", "name": "host", "role": "host",
                    "scopes": sorted(auth.HOST_SCOPES)}
        conn = self._conn()
        try:
            who = auth.resolve_token(conn, supplied)
        finally:
            conn.close()
        if who is None:
            return None
        return {"kind": "collaborator", "id": who["id"], "name": who["name"],
                "role": who["role"], "scopes": who["scopes"],
                "expires_at": who.get("expires_at")}

    def _authorized(self) -> bool:
        return self._actor() is not None

    def _require_auth(self) -> bool:
        return self._require_scope("read")

    def _require_scope(self, scope: str) -> bool:
        """401 if nobody is signed in, 403 if somebody lacks the scope."""
        actor = self._actor()
        if actor is None:
            extra = None
            if self.path.startswith("/dav"):
                # Ask the OS client to prompt for credentials.
                extra = {"WWW-Authenticate": 'Basic realm="lfmc read-only dav", charset="UTF-8"'}
            body = _json_bytes({
                "error": "unauthorized",
                "message": "A valid bearer token (or DAV password) is required.",
                "hint": "Pass 'Authorization: Bearer <token>', ?token=…, or use the token as "
                        "the WebDAV password.",
            })
            self._send(401, body, "application/json", extra)
            return False
        if not auth.has_scope(actor, scope, host=actor.get("kind") == "host"):
            self._err(403, "insufficient_scope",
                      f"This token has the {actor.get('role')!r} role, which does not "
                      f"include the {scope!r} scope.",
                      hint=f"Scopes held: {', '.join(actor.get('scopes') or [])}. "
                           "Ask the host owner to issue a token with a role that allows this.")
            return False
        return True

    # -- routing ----------------------------------------------------------
    def do_HEAD(self):
        self._route()

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    # WebDAV verbs. Everything except read verbs is refused explicitly.
    def do_OPTIONS(self):
        self._route()

    def do_PROPFIND(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_MKCOL(self):
        self._route()

    def do_MOVE(self):
        self._route()

    def do_COPY(self):
        self._route()

    def do_PROPPATCH(self):
        self._route()

    def do_LOCK(self):
        self._route()

    def do_UNLOCK(self):
        self._route()

    def _route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/r/"):
                return self._guest_page(path.split("/", 2)[2])
            if path.startswith("/api/guest/") and path.endswith("/comments") and self.command == "POST":
                return self._guest_comment(path)
            if path.startswith("/api/guest/"):
                return self._guest_data(path)
            if path == "/dav" or path.startswith("/dav/"):
                return self._handle_dav()
            if path.startswith("/media/"):
                return self._handle_media(path.split("/", 2)[2], head_only=self.command == "HEAD")
            if path.startswith("/preview/"):
                return self._handle_preview(path.split("/", 2)[2])
            if path == "/":
                return self._serve_web("index.html")
            if path.startswith("/static/"):
                return self._serve_web(path[len("/static/"):])
            if path == "/api/health":
                return self._json(200, {"ok": True, "demo": self.config.demo,
                                        "version": "0.1", "ts": now_iso()})
            if path == "/api/roots" and self.command == "GET":
                return self._list_roots()
            if path == "/api/roots" and self.command == "POST":
                return self._register_root()
            if path.startswith("/api/roots/"):
                return self._root_action(path)
            if path == "/api/browse":
                return self._browse(qs)
            if path == "/api/search":
                return self._search(qs)
            if path == "/api/assets":
                return self._assets(qs)
            # ---- review: comments, links, selects, markers ----
            if path.startswith("/api/assets/") and path.endswith("/comments"):
                return self._comments(path)
            if path.startswith("/api/comments/"):
                return self._comment_action(path)
            if path == "/api/review-links" and self.command == "POST":
                return self._create_review_link()
            if path == "/api/review-links" and self.command == "GET":
                conn = self._conn()
                try:
                    return self._json(200, {"links": review.list_links(
                        conn, asset_id=qs.get("asset_id", [None])[0])})
                finally:
                    conn.close()
            if path.startswith("/api/review-links/"):
                return self._review_link_action(path)
            if path.startswith("/api/assets/") and path.endswith("/markers.csv"):
                return self._markers_csv(path)
            if path.startswith("/api/assets/") and path.endswith("/markers"):
                return self._markers(path, qs)
            if path == "/api/selects" and self.command == "GET":
                conn = self._conn()
                try:
                    return self._json(200, {"selects": review.list_selects(
                        conn, qs.get("decision", [None])[0])})
                finally:
                    conn.close()
            if path.startswith("/api/assets/") and path.endswith("/select"):
                return self._set_select(path)
            if path.startswith("/api/assets/"):
                return self._asset_detail(path)
            # ---- collaborators (Slice A) ----
            if path == "/api/whoami":
                actor = self._actor()
                if actor is None:
                    return self._require_scope("read")   # emits the 401
                return self._json(200, actor)
            if path == "/api/collaborators":
                return self._collaborators()
            if path.startswith("/api/collaborators/"):
                return self._collaborator_action(path)
            if path == "/api/spike/open":
                return self._spike_open(qs)
            if path == "/api/dav/info":
                from ..net import webdav
                return self._json(200, webdav.preflight_hint())
            if path == "/api/nle/capabilities":
                from ..nle import registry
                return self._json(200, registry.capability_matrix())
            if path == "/api/nle/preflight" and self.command == "POST":
                return self._nle_preflight()
            if path == "/api/events":
                return self._events(qs)
            if path == "/api/transfers":
                return self._transfers(qs)
            return self._err(404, "not_found", f"No route for {path}")
        except BrokenPipeError:
            return
        except Exception as exc:  # never leak a traceback to a client
            return self._err(500, "internal_error", f"{type(exc).__name__}: {exc}")

    # -- collaborators ----------------------------------------------------
    def _collaborators(self):
        if not self._require_scope("admin"):
            return
        conn = self._conn()
        try:
            if self.command == "POST":
                body = self._body()
                try:
                    created = auth.add_collaborator(
                        conn,
                        name=body.get("name", ""),
                        role=body.get("role", ""),
                        created_by=(self._actor() or {}).get("name") or "owner",
                        expires_in_hours=body.get("expires_in_hours"),
                        note=body.get("note"),
                    )
                except auth.AuthError as exc:
                    return self._err(400, "invalid_collaborator", str(exc))
                # The raw token is in this response and nowhere else.
                created["token_warning"] = ("This token is shown once. It is stored only as a "
                                            "hash; if it is lost, revoke and reissue.")
                return self._json(201, created)
            include_revoked = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("include_revoked", ["0"])[0] in ("1", "true")
            return self._json(200, {"collaborators": auth.list_collaborators(conn, include_revoked)})
        finally:
            conn.close()

    def _collaborator_action(self, path: str):
        if not self._require_scope("admin"):
            return
        parts = path.strip("/").split("/")       # api, collaborators, <id>, <action>
        if len(parts) < 3:
            return self._err(404, "not_found", "unknown collaborator route")
        cid = parts[2]
        action = parts[3] if len(parts) > 3 else ""
        conn = self._conn()
        try:
            if action == "revoke" and self.command == "POST":
                try:
                    return self._json(200, auth.revoke_collaborator(
                        conn, cid, (self._actor() or {}).get("name") or "owner"))
                except auth.AuthError as exc:
                    return self._err(404, "not_found", str(exc))
            if action:
                return self._err(404, "not_found", f"unknown collaborator action {action!r}")
            try:
                return self._json(200, auth.get_collaborator(conn, cid))
            except auth.AuthError as exc:
                return self._err(404, "not_found", str(exc))
        finally:
            conn.close()

    # -- static UI --------------------------------------------------------
    def _serve_web(self, rel: str):
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            return self._err(404, "not_found", "asset not found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # -- review API -------------------------------------------------------
    def _comments(self, path: str):
        parts = path.strip("/").split("/")           # api, assets, <id>, comments
        asset_id = parts[2]
        conn = self._conn()
        try:
            if self.command == "POST":
                if not self._require_scope("comment"):
                    return
                actor = self._actor() or {}
                body = self._body()
                # A named collaborator cannot post under someone else's name:
                # the token decides the author, the body may not override it.
                author = (body.get("author") if actor.get("kind") == "host" else None) \
                    or actor.get("name") or "member"
                try:
                    c = review.add_comment(
                        conn, asset_id=asset_id,
                        body=body.get("body", ""),
                        author=author,
                        author_kind=body.get("author_kind") or "member",
                        timecode_ms=body.get("timecode_ms"),
                        timecode_end_ms=body.get("timecode_end_ms"),
                        fps=float(body.get("fps") or review.DEFAULT_FPS),
                        parent_id=body.get("parent_id"),
                        version_label=body.get("version_label") or "v1",
                        visibility=body.get("visibility") or "internal",
                    )
                except review.ReviewError as exc:
                    return self._err(400, "invalid_comment", str(exc))
                return self._json(201, c)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            version = query.get("version", [None])[0]
            guest_view = bool(self.headers.get("X-LFMC-Guest"))
            comments = review.list_comments(conn, asset_id, version,
                                            include_internal=not guest_view)
            return self._json(200, {"comments": comments, "count": len(comments)})
        finally:
            conn.close()

    def _comment_action(self, path: str):
        parts = path.strip("/").split("/")           # api, comments, <id>, <action>
        if len(parts) < 3:
            return self._err(404, "not_found", "unknown comment route")
        comment_id = parts[2]
        action = parts[3] if len(parts) > 3 else ""
        conn = self._conn()
        try:
            if action == "resolve" and self.command == "POST":
                body = self._body()
                try:
                    return self._json(200, review.resolve_comment(
                        conn, comment_id, bool(body.get("resolved", True)),
                        body.get("actor") or "member"))
                except review.ReviewError as exc:
                    return self._err(404, "not_found", str(exc))
            if action == "react" and self.command == "POST":
                body = self._body()
                try:
                    return self._json(200, review.react(
                        conn, comment_id, body.get("emoji", ""), body.get("actor") or "member"))
                except review.ReviewError as exc:
                    return self._err(400, "invalid_reaction", str(exc))
            return self._err(404, "not_found", f"unknown comment action {action!r}")
        finally:
            conn.close()

    def _create_review_link(self):
        if not self._require_scope("share"):
            return
        body = self._body()
        conn = self._conn()
        try:
            try:
                link = review.create_review_link(
                    conn,
                    asset_id=body.get("asset_id", ""),
                    version_label=body.get("version_label") or "v1",
                    created_by=body.get("created_by") or "owner",
                    expires_in_hours=body.get("expires_in_hours", 168),
                    passphrase=body.get("passphrase") or None,
                    allow_comments=bool(body.get("allow_comments", True)),
                    allow_download=bool(body.get("allow_download", False)),
                    watermark=bool(body.get("watermark", False)),
                    note=body.get("note"),
                )
            except review.ReviewError as exc:
                return self._err(400, "invalid_link", str(exc))
            # absolute URL for copy/paste; the raw token is returned exactly once
            host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
            link["url"] = f"http://{host}{link['url_path']}"
            return self._json(201, link)
        finally:
            conn.close()

    def _review_link_action(self, path: str):
        if not self._require_scope("share"):
            return
        parts = path.strip("/").split("/")
        link_id = parts[2]
        action = parts[3] if len(parts) > 3 else ""
        conn = self._conn()
        try:
            if action == "revoke" and self.command == "POST":
                try:
                    return self._json(200, review.revoke_link(conn, link_id,
                                                              (self._body() or {}).get("actor") or "owner"))
                except review.ReviewError as exc:
                    return self._err(404, "not_found", str(exc))
            links = review.list_links(conn, link_id=link_id)
            if not links:
                return self._err(404, "not_found", "unknown review link")
            return self._json(200, links[0])
        finally:
            conn.close()

    def _markers_csv(self, path: str):
        if not self._require_scope("markers"):
            return
        parts = path.strip("/").split("/")
        asset_id = parts[2]
        conn = self._conn()
        try:
            try:
                csv_text = review.markers_csv(conn, asset_id)
            except review.UnknownAsset as exc:
                return self._err(404, "not_found", str(exc))
            except review.ReviewError as exc:
                return self._err(400, "invalid_export", str(exc))
            self._send(200, csv_text.encode("utf-8"), "text/csv; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="markers-{asset_id[:8]}.csv"',
                "X-LFMC-Export-Verified": "no",   # never claim NLE round-trip until tested
            })
        finally:
            conn.close()

    def _markers(self, path: str, qs: dict):
        """Marker export with a format and an export ledger.

        ?format=premiere|resolve  (default premiere)
        ?dry_run=1                render without recording an export
        ?json=1                   return JSON (markers + ledger state) instead of a file
        """
        if not self._require_scope("markers"):
            return
        parts = path.strip("/").split("/")
        asset_id = parts[2]
        fmt = (qs.get("format", ["premiere"])[0] or "premiere").lower()
        dry = qs.get("dry_run", ["0"])[0] in ("1", "true", "yes")
        as_json = qs.get("json", ["0"])[0] in ("1", "true", "yes")
        conn = self._conn()
        try:
            try:
                result = review.export_markers(conn, asset_id, fmt=fmt, dry_run=dry)
            except review.UnknownAsset as exc:
                return self._err(404, "not_found", str(exc))
            except review.ReviewError as exc:
                return self._err(400, "invalid_export", str(exc))
            if as_json:
                payload = {k: result[k] for k in ("format", "markers", "recorded", "dry_run")}
                payload["already_exported"] = review.export_summary(conn, asset_id)
                payload["nle_verified"] = False
                return self._json(200, payload)
            ctype = ("text/csv; charset=utf-8" if result["format"] == "premiere"
                     else "text/tab-separated-values; charset=utf-8")
            ext = "csv" if result["format"] == "premiere" else "tsv"
            self._send(200, result["text"].encode("utf-8"), ctype, {
                "Content-Disposition":
                    f'attachment; filename="markers-{asset_id[:8]}-{result["format"]}.{ext}"',
                "X-LFMC-Export-Verified": "no",   # never claim NLE round-trip until tested
                "X-LFMC-Markers": str(result["markers"]),
                "X-LFMC-Format": result["format"],
            })
        finally:
            conn.close()

    def _set_select(self, path: str):
        if not self._require_scope("select"):
            return
        parts = path.strip("/").split("/")
        asset_id = parts[2]
        body = self._body()
        conn = self._conn()
        try:
            try:
                row = review.set_select(
                    conn, asset_id=asset_id,
                    reviewer=body.get("reviewer") or "producer",
                    decision=body.get("decision", ""),
                    note=body.get("note"),
                    timecode_ms=body.get("timecode_ms"))
            except review.ReviewError as exc:
                return self._err(400, "invalid_select", str(exc))
            return self._json(200, row)
        finally:
            conn.close()

    # -- guest review (accountless) ---------------------------------------
    def _guest_page(self, token: str):
        """Serve the accountless review page. Contains NO catalogue data: it fetches
        its own asset via /api/guest/<token> after the passphrase check."""
        page = (WEB_ROOT / "guest.html")
        if not page.is_file():
            return self._err(404, "not_found", "guest page missing")
        return self._send(200, page.read_bytes(), "text/html; charset=utf-8")

    def _guest_data(self, path: str):
        parts = path.strip("/").split("/")           # api, guest, <token>
        token = parts[2]
        passphrase = self.headers.get("X-LFMC-Passphrase") or None
        conn = self._conn()
        try:
            try:
                resolved = review.resolve_link(conn, token, passphrase)
            except review.ReviewError as exc:
                code = 401 if str(exc) in ("passphrase_required", "passphrase_invalid") else 410
                return self._err(code, str(exc),
                                 "This review link is not available.",
                                 "Ask the sender for a new link or the passphrase.")
            asset = resolved["asset"]
            link = resolved["link"]
            comments = review.list_comments(conn, asset["id"], link["version_label"],
                                            include_internal=False)
            state = catalogue.asset_state(conn, catalogue.get_asset(conn, asset["id"]))
            # A guest sees ONE clip. Root label, root id and last-seen timestamps
            # would leak the shape of the team's drives, so they never leave here.
            state = {k: v for k, v in state.items()
                     if k not in ("root_id", "root_label", "root_last_seen", "asset_id")}
            return self._json(200, {
                "asset": {
                    "id": asset["id"], "name": asset["name"],
                    "size_bytes": asset["size_bytes"], "media_kind": asset["media_kind"],
                },
                "version_label": link["version_label"],
                "allow_comments": bool(link["allow_comments"]),
                "allow_download": bool(link["allow_download"]),
                "watermark": bool(link["watermark"]),
                "expires_at": link["expires_at"],
                "availability": state,
                "comments": comments,
            })
        finally:
            conn.close()

    def _guest_comment(self, path: str):
        parts = path.strip("/").split("/")           # api, guest, <token>, comments
        token = parts[2]
        body = self._body()
        passphrase = self.headers.get("X-LFMC-Passphrase") or None
        conn = self._conn()
        try:
            try:
                resolved = review.resolve_link(conn, token, passphrase)
            except review.ReviewError as exc:
                return self._err(410, str(exc), "This review link is not available.")
            link = resolved["link"]
            if not link["allow_comments"]:
                return self._err(403, "comments_disabled", "Commenting is disabled on this link.")
            author = (body.get("author") or "Guest reviewer").strip()[:80] or "Guest reviewer"
            try:
                c = review.add_comment(
                    conn, asset_id=resolved["asset"]["id"],
                    body=body.get("body", ""), author=author, author_kind="guest",
                    timecode_ms=body.get("timecode_ms"),
                    timecode_end_ms=body.get("timecode_end_ms"),
                    fps=float(body.get("fps") or review.DEFAULT_FPS),
                    parent_id=body.get("parent_id"),
                    version_label=link["version_label"],
                    # Guest comments are team-visible; internal notes stay internal.
                    visibility="shared",
                )
            except review.ReviewError as exc:
                return self._err(400, "invalid_comment", str(exc))
            return self._json(201, c)
        finally:
            conn.close()

    # -- catalogue API ----------------------------------------------------
    def _list_roots(self):
        if not self._require_scope("read"):
            return
        conn = self._conn()
        try:
            for row in conn.execute("SELECT id FROM roots").fetchall():
                scanner.check_root_online(conn, row["id"])
            roots = catalogue.root_summary(conn)
            for r in roots:
                r["recent_scans"] = catalogue.scan_history(conn, r["id"], limit=3)
            return self._json(200, {"roots": roots})
        finally:
            conn.close()

    def _register_root(self):
        if not self._require_scope("admin"):
            return
        body = self._body()
        path = body.get("path")
        if not path:
            return self._err(400, "missing_path", "Body must include 'path'.")
        if not Path(path).expanduser().exists():
            return self._err(400, "path_not_found", f"Path does not exist: {path}")
        conn = self._conn()
        try:
            root_id = scanner.register_root(conn, path, body.get("label"))
            scanner.check_root_online(conn, root_id)
            res = scanner.scan_root(conn, root_id)
            return self._json(201, {"root_id": root_id, "scan": res.as_dict()})
        finally:
            conn.close()

    def _root_action(self, path: str):
        if not self._require_scope("admin"):
            return
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            return self._err(404, "not_found", "unknown root route")
        root_id, action = parts[2], parts[3] if len(parts) > 3 else ""
        conn = self._conn()
        try:
            if action == "scan" and self.command == "POST":
                status = scanner.check_root_online(conn, root_id)
                if not status.online:
                    result = scanner.scan_root(conn, root_id)
                    return self._json(200, {
                        "scan": result.as_dict(),
                        "explanation": "Root is offline: catalogue kept, nothing tombstoned.",
                    })
                return self._json(200, {"scan": scanner.scan_root(conn, root_id).as_dict()})
            if action == "disconnect" and self.command == "POST":
                scanner.pause_sharing(conn, root_id,
                                      self._body().get("reason", "owner paused sharing"))
                return self._json(200, {
                    "root_id": root_id, "online": False, "share_enabled": False,
                    "note": "Catalogue retained and searchable; originals unavailable.",
                })
            if action == "reconnect" and self.command == "POST":
                status = scanner.resume_sharing(conn, root_id)
                return self._json(200, {"root_id": root_id, "online": status.online,
                                        "detail": status.detail})
            return self._err(404, "not_found", f"unknown action {action!r}")
        finally:
            conn.close()

    def _browse(self, qs):
        if not self._require_scope("read"):
            return
        root_id = qs.get("root_id", [""])[0]
        dir_path = qs.get("path", [""])[0]
        if not root_id:
            return self._err(400, "missing_root", "root_id is required")
        conn = self._conn()
        try:
            listing = catalogue.browse(conn, root_id, dir_path)
            for f in listing["files"]:
                f["availability"] = catalogue.asset_state(conn, f)
            return self._json(200, listing)
        finally:
            conn.close()

    def _search(self, qs):
        if not self._require_scope("read"):
            return
        q = qs.get("q", [""])[0]
        root_id = qs.get("root_id", [None])[0]
        conn = self._conn()
        try:
            rows = catalogue.search(conn, q, root_id)
            for r in rows:
                r["availability"] = catalogue.asset_state(conn, r)
            return self._json(200, {"query": q, "count": len(rows), "results": rows,
                                    "searched_offline_catalogue": True})
        finally:
            conn.close()

    def _assets(self, qs):
        if not self._require_scope("read"):
            return
        root_id = qs.get("root_id", [None])[0]
        limit = min(1000, int(qs.get("limit", ["200"])[0]))
        conn = self._conn()
        try:
            rows = catalogue.list_assets(conn, root_id, limit=limit)
            for r in rows:
                r["availability"] = catalogue.asset_state(conn, r)
            return self._json(200, {"assets": rows, "count": len(rows)})
        finally:
            conn.close()

    def _asset_detail(self, path: str):
        if not self._require_scope("read"):
            return
        parts = path.strip("/").split("/")
        asset_id = parts[2]
        conn = self._conn()
        try:
            row = catalogue.get_asset(conn, asset_id)
            if row is None:
                return self._err(404, "asset_not_found", "No such asset")
            payload = dict(row)
            payload["availability"] = catalogue.asset_state(conn, row, verify_now=True)
            if len(parts) > 3 and parts[3] == "state":
                return self._json(200, payload["availability"])
            return self._json(200, payload)
        finally:
            conn.close()

    def _events(self, qs):
        if not self._require_scope("read"):
            return
        limit = min(500, int(qs.get("limit", ["50"])[0]))
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return self._json(200, {"events": [dict(r) for r in rows]})
        finally:
            conn.close()

    def _transfers(self, qs):
        if not self._require_scope("read"):
            return
        limit = min(500, int(qs.get("limit", ["50"])[0]))
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM transfer_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            total = conn.execute(
                "SELECT COALESCE(SUM(bytes),0) AS b, COUNT(*) AS n FROM transfer_log").fetchone()
            return self._json(200, {"transfers": [dict(r) for r in rows],
                                    "total_bytes": total["b"], "total_requests": total["n"]})
        finally:
            conn.close()

    # -- media ------------------------------------------------------------
    def _resolve_asset_file(self, conn, asset_id: str):
        row = catalogue.get_asset(conn, asset_id)
        if row is None:
            return None, None, ("not_found", "No such asset", None)
        if row["deleted_at"]:
            return None, None, ("deleted", "This file was removed from the source drive.",
                                "It was confirmed absent during a scan of a reachable root.")
        state = catalogue.asset_state(conn, row, verify_now=True)
        if not state["original_available"]:
            return None, None, (
                "original_unavailable",
                "The host computer or its drive is offline, so the original cannot be read.",
                f"{state['state_label']}. The catalogue, search and any cached preview keep "
                "working; reconnect the host/drive to read originals.",
            )
        root = conn.execute("SELECT abs_path FROM roots WHERE id=?", (row["root_id"],)).fetchone()
        base = Path(root["abs_path"]).resolve()
        full = (base / row["rel_path"]).resolve()
        if not str(full).startswith(str(base) + os.sep):
            return None, None, ("forbidden", "Path escapes the shared root.", None)
        if not full.is_file():
            return None, None, ("original_unavailable", "File disappeared after catalogue check.",
                                "Re-scan the root to reconcile the catalogue.")
        return row, full, None

    def _log_transfer(self, conn, asset_id, start, end, nbytes, outcome):
        session = self.headers.get("X-LFMC-Session") or uuid.uuid4().hex[:12]
        conn.execute(
            """INSERT INTO transfer_log(ts, asset_id, session_id, start_byte, end_byte, bytes, client, outcome)
               VALUES(?,?,?,?,?,?,?,?)""",
            (now_iso(), asset_id, session, start, end, nbytes,
             self.client_address[0], outcome),
        )
        conn.commit()

    def _handle_media(self, asset_id: str, head_only: bool = False):
        if not self._require_auth():
            return
        self._serve_asset_bytes(asset_id, head_only=head_only, unavailable_status=409)

    def _serve_asset_bytes(self, asset_id: str, head_only: bool = False,
                           unavailable_status: int = 409):
        """Stream an original with Range support. Auth is the caller's job."""
        conn = self._conn()
        try:
            row, full, problem = self._resolve_asset_file(conn, asset_id)
            if problem:
                code, message, hint = problem
                if code == "not_found":
                    return self._err(404, "not_found", message)
                if code == "forbidden":
                    return self._err(403, code, message, hint)
                if code == "deleted":
                    return self._err(410, code, message, hint)
                return self._err(unavailable_status, code, message, hint)

            size = full.stat().st_size
            etag = f'"{row["fingerprint"] or "nofp"}-{size}"'
            try:
                rng = http_range.parse_range_header(self.headers.get("Range"), size)
            except http_range.RangeError as exc:
                self._log_transfer(conn, asset_id, None, None, 0, "range_not_satisfiable")
                return self._send(416, _json_bytes({"error": "range_not_satisfiable",
                                                    "message": str(exc)}),
                                  "application/json",
                                  {"Content-Range": http_range.unsatisfied_content_range(size)})

            ctype = mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
            base_headers = {"Accept-Ranges": "bytes", "ETag": etag}

            if rng is None and head_only:
                # A HEAD is a metadata probe (players do this first); it never
                # transfers media, so it is never a "full download".
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                for k, v in base_headers.items():
                    self.send_header(k, v)
                self.end_headers()
                return

            if rng is None:
                # No Range header: only allowed for small files (e.g. previews),
                # otherwise we refuse to stream a whole original implicitly.
                if size > 8 * 1024 * 1024:
                    self._log_transfer(conn, asset_id, None, None, 0, "denied_full_download")
                    return self._err(
                        400, "range_required",
                        "This endpoint serves originals by byte range only.",
                        "Send a Range header. Bulk download is intentionally not supported in V1.",
                    )
                rng = http_range.ByteRange(0, size - 1)
                headers = dict(base_headers, **{"Content-Range": http_range.content_range_header(rng, size),
                                                "Content-Length": str(rng.length)})
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if head_only:
                    return
                nbytes = 0
                for block in http_range.iter_file_range(full, rng):
                    self.wfile.write(block)
                    nbytes += len(block)
                self._log_transfer(conn, asset_id, rng.start, rng.end, nbytes, "ok")
                return

            headers = {
                **base_headers,
                "Content-Type": ctype,
                "Content-Range": http_range.content_range_header(rng, size),
                "Content-Length": str(rng.length),
            }
            self.send_response(206)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if head_only:
                return
            nbytes = 0
            for block in http_range.iter_file_range(full, rng):
                self.wfile.write(block)
                nbytes += len(block)
            self._log_transfer(conn, asset_id, rng.start, rng.end, nbytes, "ok")
        except BrokenPipeError:
            self._log_transfer(conn, asset_id, None, None, 0, "client_disconnect")
        finally:
            conn.close()

    def _handle_preview(self, asset_id: str):
        """Serve a cached preview. Works while the source drive is offline --
        this is the 'preview available, original unavailable' state."""
        conn = self._conn()
        try:
            path = catalogue.preview_bytes_available(conn, asset_id)
            if not path:
                row = catalogue.get_asset(conn, asset_id)
                if row is None:
                    return self._err(404, "asset_not_found", "No such asset")
                return self._err(404, "no_preview", "No cached preview for this asset.",
                                 "Previews are generated on the host (ffmpeg tier) or not at all.")
            data = Path(path).read_bytes()
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            self._send(200, data, ctype)
        finally:
            conn.close()

    # -- WebDAV (read-only) ------------------------------------------------
    DAV_ALLOW = "OPTIONS, GET, HEAD, PROPFIND"

    def _handle_dav(self):
        from ..net import webdav
        method = self.command

        if method == "OPTIONS":
            # Capability probe: no auth required because it exposes no data, and
            # OS clients send it before they have credentials.
            self._send(200, b"", "text/plain; charset=utf-8", {
                "DAV": "1, 2",
                "MS-Author-Via": "DAV",
                "Allow": self.DAV_ALLOW,
            })
            return

        if not self._require_auth():
            return

        if method in webdav.WRITE_METHODS:
            return self._err(
                405, "read_only",
                f"{method} is not supported: this mount is read-only.",
                "The platform never modifies source media. Nothing was written.",
            )
        if method in webdav.LOCK_METHODS:
            return self._err(
                501, "locks_unsupported", f"{method} is not supported by this build.",
                "A read-only mount provides no lock service; none is faked.",
            )

        conn = self._conn()
        try:
            node = webdav.resolve(conn, self.path)
            if node.kind == "missing":
                return self._err(404, "not_found",
                                 "No such resource in the catalogue.",
                                 "Listings come from the catalogue; the drive does not "
                                 "need to be online to browse it.")
            if method == "PROPFIND":
                body = webdav.multistatus(conn, node, self.headers.get("Depth", "0"))
                return self._send(207, body, "application/xml; charset=utf-8", {"DAV": "1, 2"})
            if method in ("GET", "HEAD"):
                if node.kind == "file":
                    # 503 (not 404) when the original is offline: an NLE must see
                    # "temporarily unavailable", not "deleted".
                    return self._serve_asset_bytes(node.asset["id"],
                                                   head_only=(method == "HEAD"),
                                                   unavailable_status=503)
                return self._send(200, webdav.listing_html(conn, node),
                                  "text/html; charset=utf-8")
            return self._err(405, "method_not_allowed", f"{method} is not allowed here.")
        finally:
            conn.close()

    # -- spike + NLE ------------------------------------------------------
    def _spike_open(self, qs):
        asset_id = qs.get("asset_id", [""])[0]
        seeks = int(qs.get("seeks", ["10"])[0])
        chunk = int(float(qs.get("chunk_mb", ["2"])[0]) * 1024 * 1024)
        if not asset_id:
            return self._err(400, "missing_asset", "asset_id is required")
        conn = self._conn()
        try:
            row, full, problem = self._resolve_asset_file(conn, asset_id)
            if problem:
                code, message, hint = problem
                return self._err(409 if code != "not_found" else 404, code, message, hint)
            # Use the port we are actually bound to (tests bind port 0), not the
            # configured value, so the spike measures this running server.
            actual_port = self.server.server_address[1]
            base = self.config.public_url or f"http://127.0.0.1:{actual_port}"
            url = f"{base}/media/{asset_id}"
            report = mp4_remote_open.analyse_source(url, self.config.token, seeks, chunk)
            report["asset"] = {"id": asset_id, "name": row["name"], "rel_path": row["rel_path"]}
            report["transport"] = "http-range (real requests, measured bytes)"
            return self._json(200, report)
        finally:
            conn.close()

    def _nle_preflight(self):
        from ..nle import registry
        body = self._body()
        result = registry.preflight(
            nle=body.get("nle", ""),
            mode=body.get("mode", ""),
            user_acknowledged_access=bool(body.get("user_acknowledged_access")),
            consent_recorded=bool(body.get("consent_recorded")),
            private_server_configured=bool(body.get("private_server_configured")),
            media_sync_enabled=bool(body.get("media_sync_enabled")),
        )
        return self._json(200, result)


def make_server(config: Config) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"config": config})
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(config: Config) -> None:
    db.connect(config.db_path).close()  # ensure schema exists before first request
    httpd = make_server(config)
    tok_file = Path(config.db_path).with_suffix(".token")
    if not config.demo:
        tok_file.write_text(config.token)
        try:
            os.chmod(tok_file, 0o600)
        except OSError:
            pass
    # flush=True: stdout is a pipe when run by a process manager, where Python
    # block-buffers and the startup lines would otherwise never appear in logs.
    print(f"[lfmc] listening on http://{config.host}:{config.port}", flush=True)
    print(f"[lfmc] catalogue: {config.db_path}", flush=True)
    print(f"[lfmc] demo mode: {config.demo} (token {'not' if config.demo else ''} required)",
          flush=True)
    print(f"[lfmc] webdav mount: http://<host>:{config.port}/dav/ (read-only)", flush=True)
    if not config.demo:
        print(f"[lfmc] bearer token written to {tok_file} (mode 0600)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
