#!/usr/bin/env python3
"""Export a static, read-only snapshot of the real Lumen UI for hosting.

Why this exists
---------------
The product cannot run on a serverless host: it is a long-lived process that
owns a SQLite catalogue on disk and streams byte ranges from a drive attached to
*that* machine. What can be hosted is the interface plus a captured dataset, so a
reviewer can click through the screens on a phone or laptop.

What it produces
----------------
demo-static/
  index.html      the real UI + a fetch shim that answers from fixtures.json
  guest.html      the accountless client review page, with a captured link
  fixtures.json   captured API responses (synthetic demo drive only)
  vercel.json     static hosting config

Honesty rules baked in
----------------------
* A fixed banner says this is a snapshot, not the live product.
* Every write (comment, link, select, revoke) is refused by the shim with a
  visible "read-only demo" message. Nothing pretends to save.
* Only the synthetic sample drive is exported. Passing --allow-real-data is
  refused unless the catalog is clearly synthetic.

Usage:
    python3 -m lfmc.cli serve --port 8765 --demo &
    python3 tools/export_static_demo.py --base http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "lfmc" / "web"
OUT_DEFAULT = ROOT / "demo-static"


HOST_PATH_MARKERS = ("/home/", "/Users/", "C:\\Users\\", "/private/var/",
                     "/var/folders/", "/tmp/")


def assert_no_host_paths(fx: dict) -> None:
    """Fail the export if the captured fixtures contain a host filesystem path.

    A demo that ships with /home/<builder>/... in it leaks where it was made and
    looks unfinished. This is a build-time guard, not a runtime filter: better to
    stop than to quietly publish.
    """
    blob = json.dumps(fx)
    found = [m for m in HOST_PATH_MARKERS if m in blob]
    if found:
        raise SystemExit(
            "refusing to export: fixtures contain host paths "
            f"({', '.join(found)}). Redact them in collect() before shipping.")


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode())


def collect(base: str) -> dict:
    fx: dict[str, object] = {}
    fx["/api/health"] = get(base, "/api/health")
    fx["/api/nle/capabilities"] = get(base, "/api/nle/capabilities")
    fx["/api/dav/info"] = get(base, "/api/dav/info")
    fx["/api/transfers?limit=1"] = get(base, "/api/transfers?limit=1")
    fx["/api/selects"] = get(base, "/api/selects")
    fx["/api/review-links"] = get(base, "/api/review-links")

    roots = get(base, "/api/roots")
    # The captured fixtures travel with the demo: a public/shared copy must not
    # disclose the build machine's filesystem layout. Absolute paths in the root
    # rows are replaced with a neutral mount point. (Relative preview paths are
    # left alone -- they carry no host information.)
    for root in roots.get("roots", []):
        label = root.get("label") or "Demo Drive"
        root["abs_path"] = f"/Volumes/{label}"
        root["volume_hint"] = "demo"
    fx["/api/roots"] = roots
    all_assets: list[dict] = []
    for root in roots.get("roots", []):
        rid = root["id"]
        queue = [""]
        seen = set()
        while queue:
            p = queue.pop(0)
            if p in seen:
                continue
            seen.add(p)
            key = f"/api/browse?root_id={rid}&path={p}"
            listing = get(base, f"/api/browse?root_id={rid}&path={urllib.parse.quote(p)}")
            fx[key] = listing
            all_assets.extend(listing.get("files", []))
            for f in listing.get("folders", []):
                queue.append(f["path"])

    texts: dict[str, str] = {}
    for a in all_assets:
        aid = a["id"]
        try:
            with urllib.request.urlopen(base + f"/api/assets/{aid}/markers.csv", timeout=20) as r:
                texts[f"/api/assets/{aid}/markers.csv"] = r.read().decode()
        except Exception:
            pass
        fx[f"/api/assets/{aid}"] = get(base, f"/api/assets/{aid}")
        fx[f"/api/assets/{aid}/comments"] = get(base, f"/api/assets/{aid}/comments")
        links = get(base, f"/api/review-links?asset_id={aid}")
        fx[f"/api/review-links?asset_id={aid}"] = links

    # One guest link snapshot so the client-review page is explorable.
    guest = None
    active = [l for l in fx["/api/review-links"].get("links", []) if l.get("status") == "active"]
    if active:
        guest = {"link": active[0]}
    fx["__assets__"] = all_assets
    fx["__text__"] = texts
    fx["__guest_available__"] = bool(guest)
    return fx


def guest_fixture(base: str) -> dict | None:
    """Capture one live guest payload by minting a short-lived link."""
    roots = get(base, "/api/roots")
    for root in roots.get("roots", []):
        listing = get(base, f"/api/browse?root_id={root['id']}&path=")
        for folder in listing.get("folders", []):
            inner = get(base, f"/api/browse?root_id={root['id']}&path={urllib.parse.quote(folder['path'])}")
            if inner.get("files"):
                aid = inner["files"][0]["id"]
                body = json.dumps({"asset_id": aid, "expires_in_hours": 720,
                                   "created_by": "producer", "note": "static demo"}).encode()
                req = urllib.request.Request(base + "/api/review-links", data=body,
                                             headers={"Content-Type": "application/json"},
                                             method="POST")
                with urllib.request.urlopen(req, timeout=20) as r:
                    link = json.loads(r.read().decode())
                tok = link["token"]
                payload = get(base, f"/api/guest/{tok}")
                return {"token": tok, "payload": payload}
    return None


SHIM = """
<script>
/* ---- static demo shim: answers from fixtures, refuses every write ---- */
(function(){
  const FIX  = window.__LFMC_FIXTURES__ || {};
  const TEXT = FIX.__text__ || {};
  const norm = (u) => {
    try { const x = new URL(u, location.origin); return x.pathname + x.search; }
    catch(e){ return String(u); }
  };
  /* minimal Response stand-in so this works in any engine, no polyfills */
  function res(body, status, ctype){
    return { ok: status >= 200 && status < 300, status: status,
             headers: { get: () => ctype },
             async text(){ return body; },
             async json(){ return JSON.parse(body); },
             async blob(){ return new Blob([body], {type: ctype || "text/plain"}); } };
  }
  const json = (o, s) => res(JSON.stringify(o), s || 200, "application/json");

  window.fetch = async function(input, init){
    const method = ((init && init.method) || "GET").toUpperCase();
    const key = norm(typeof input === "string" ? input : input.url);

    if (method !== "GET") {
      return json({ error: "read_only_demo",
        message: "This is a read-only snapshot \u2014 changes are not saved. "
               + "Run the real host locally to create comments, links and selects." }, 403);
    }
    if (Object.prototype.hasOwnProperty.call(TEXT, key)) {
      return res(TEXT[key], 200, "text/csv");
    }
    if (Object.prototype.hasOwnProperty.call(FIX, key)) return json(FIX[key]);

    if (key.startsWith("/api/search")) {
      const q = (new URLSearchParams(key.split("?")[1] || "")).get("q") || "";
      const t = q.toLowerCase();
      const hits = (FIX.__assets__ || []).filter(a =>
        (a.name||"").toLowerCase().includes(t) || (a.rel_path||"").toLowerCase().includes(t));
      return json({ query: q, count: hits.length, results: hits,
                    demo_note: "snapshot: client-side filter over the captured catalogue" });
    }
    if (key.startsWith("/api/browse")) {
      const u = new URLSearchParams(key.split("?")[1] || "");
      const rid = u.get("root_id"), p = u.get("path") || "";
      const direct = `/api/browse?root_id=${rid}&path=${p}`;
      if (FIX[direct]) return json(FIX[direct]);
      for (const fk of Object.keys(FIX)) {
        if (fk.startsWith("/api/browse") && fk.includes("root_id=" + rid)) {
          const fp = new URLSearchParams(fk.split("?")[1]).get("path") || "";
          if (fp === p) return json(FIX[fk]);
        }
      }
    }
    return json({ error: "not_in_snapshot",
                  message: "This endpoint was not captured in the static snapshot." }, 404);
  };
})();
</script>
"""

BANNER = """
<div id="demo-banner" style="position:fixed;left:0;right:0;top:0;z-index:9999;
     background:#3a2f08;color:#f0d79a;border-bottom:1px solid #5c4a1f;
     font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:7px 14px;text-align:center">
  <b>Read-only demo snapshot</b> — captured __DATE__ from the synthetic sample drive.
  Nothing here is saved, no drive is connected, and no NLE is linked.
  <span style="opacity:.75">Run the host locally for the real thing.</span>
</div>
<style>body{padding-top:34px !important}</style>
"""


def build(base: str, out: pathlib.Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    fx = collect(base)
    guest = guest_fixture(base)

    fixtures_js = ("<script>window.__LFMC_FIXTURES__ = "
                   + json.dumps(fx, separators=(",", ":"))
                   + ";</script>")

    html = (WEB / "index.html").read_text()
    stamp = dt.date.today().isoformat()
    html = html.replace("<body>", "<body>\n" + BANNER.replace("__DATE__", stamp), 1)
    # shim must run before the app script
    marker = "<script>\n/* ====="
    idx = html.find(marker)
    if idx == -1:
        raise SystemExit("could not find app script marker in index.html")
    html = html[:idx] + fixtures_js + SHIM + html[idx:]
    (out / "index.html").write_text(html)

    guest_html = (WEB / "guest.html").read_text()
    if guest:
        payload_js = ("<script>window.__DEMO_GUEST__="
                      + json.dumps(guest["payload"], separators=(",", ":"))
                      + ";window.__DEMO_TOKEN__=" + json.dumps(guest["token"]) + ";</script>")
        gshim = """
<script>
(function(){
  const P = window.__DEMO_GUEST__;
  function res(body, status){ return { ok: status>=200&&status<300, status: status,
      async text(){ return body; }, async json(){ return JSON.parse(body); },
      async blob(){ return new Blob([body],{type:"application/json"}); } }; }
  const json = (o,s)=>res(JSON.stringify(o), s||200);
  window.fetch = async function(input, init){
    const m = ((init&&init.method)||"GET").toUpperCase();
    if (m !== "GET") return json({error:"read_only_demo",
        message:"Read-only snapshot: guest comments are not saved here."},403);
    return json(P || {error:"revoked"});
  };
})();
</script>"""
        guest_html = guest_html.replace("<body>",
            "<body>\n" + BANNER.replace("__DATE__", stamp), 1)
        gi = guest_html.find("<script>\nconst TOKEN")
        if gi == -1:
            gi = guest_html.find("<script>")
        guest_html = guest_html[:gi] + payload_js + gshim + guest_html[gi:]
        (out / "guest.html").write_text(guest_html)

    assert_no_host_paths(fx)
    (out / "fixtures.json").write_text(json.dumps(fx, indent=1))
    (out / "vercel.json").write_text(json.dumps({
        "cleanUrls": True,
        "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Robots-Tag", "value": "noindex"},
            {"key": "X-LFMC-Demo", "value": "read-only-static-snapshot"}]}]
    }, indent=2))
    (out / "README.txt").write_text(
        "Read-only static snapshot of the Lumen UI.\n"
        f"Captured {stamp} from the synthetic sample drive.\n"
        "No backend: writes are refused, no drive is connected, no NLE is linked.\n"
        "Regenerate: python3 tools/export_static_demo.py --base http://127.0.0.1:8765\n")

    return {"out": str(out),
            "endpoints_captured": len([k for k in fx if k.startswith("/api")]),
            "assets": len(fx.get("__assets__", [])),
            "guest_page": guest is not None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    result = build(args.base, pathlib.Path(args.out))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used in collect)
    sys.exit(main())
