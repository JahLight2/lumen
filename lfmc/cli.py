"""Command-line entry point (stdlib only).

    python -m lfmc.cli init
    python -m lfmc.cli seed-demo
    python -m lfmc.cli add-root /path/to/drive --label "Studio Drive"
    python -m lfmc.cli scan <root_id>
    python -m lfmc.cli status
    python -m lfmc.cli search "day one"
    python -m lfmc.cli serve --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import catalogue, db, scanner
from .core.db import now_iso
from .nle import registry

DEFAULT_DB = os.environ.get("LFMC_DB", "data/catalogue.db")
DEFAULT_SAMPLE = os.environ.get("LFMC_SAMPLE_DIR", "data/sample_drive")


def _conn(args):
    return db.connect(args.db)


def cmd_init(args):
    conn = _conn(args)
    conn.close()
    print(f"catalogue initialised at {Path(args.db).resolve()}")


def cmd_add_root(args):
    conn = _conn(args)
    try:
        root_id = scanner.register_root(conn, args.path, args.label)
        status = scanner.check_root_online(conn, root_id)
        print(json.dumps({"root_id": root_id, "online": status.online, "detail": status.detail}, indent=2))
        if args.scan and status.online:
            print(json.dumps(scanner.scan_root(conn, root_id).as_dict(), indent=2))
    finally:
        conn.close()


def cmd_scan(args):
    conn = _conn(args)
    try:
        print(json.dumps(scanner.scan_root(conn, args.root_id, force_hash=args.force).as_dict(), indent=2))
    finally:
        conn.close()


def cmd_status(args):
    conn = _conn(args)
    try:
        roots = catalogue.root_summary(conn)
        for r in roots:
            scanner.check_root_online(conn, r["id"])
        roots = catalogue.root_summary(conn)
        for r in roots:
            print(f"[{r['id'][:8]}] {r['label']:<24} online={bool(r['online']):<5} "
                  f"assets={r['live_assets']:<6} video={r['video_assets']:<5} {r['abs_path']}")
        if not roots:
            print("no roots registered yet")
    finally:
        conn.close()


def cmd_search(args):
    conn = _conn(args)
    try:
        rows = catalogue.search(conn, args.query, args.root_id, limit=args.limit)
        for r in rows:
            state = catalogue.asset_state(conn, r)["state"]
            print(f"{state:<20} {r['rel_path']}")
        print(f"-- {len(rows)} result(s) from the local catalogue (works offline)")
    finally:
        conn.close()


def cmd_seed_demo(args):
    """Create a synthetic 'camera drive' so the spike is runnable with no data."""
    from .spikes.mp4_remote_open import write_synthetic_mp4

    base = Path(args.dir)
    layout = [
        ("01_DAY1_INTERVIEW/A001_C003.mp4", 48),
        ("01_DAY1_INTERVIEW/A001_C004.mp4", 32),
        ("01_DAY1_INTERVIEW/notes_interview.txt", 0),
        ("02_DRONE/DJI_0007.mp4", 64),
        ("02_DRONE/thumbs/DJI_0007.svg", 0),
        ("03_AUDIO/interview_01.wav", 0),
        ("Client_Delivery/final_cut_v3.mp4", 24),
    ]
    base.mkdir(parents=True, exist_ok=True)
    for rel, size_mb in layout:
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        if rel.endswith((".mp4", ".mov")):
            write_synthetic_mp4(str(target), media_bytes=size_mb * 1024 * 1024, sparse=True)
        elif rel.endswith(".svg"):
            target.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">'
                '<rect width="320" height="180" fill="#1f2a37"/>'
                '<text x="16" y="96" fill="#8fd6ff" font-family="sans-serif" font-size="20">'
                'DJI_0007 — cached preview</text></svg>')
        elif rel.endswith(".wav"):
            target.write_bytes(b"RIFF" + b"\x00" * 64)
        else:
            target.write_text("Interview notes: MC 2, lav 1, 2 takes.\n")

    conn = _conn(args)
    try:
        root_id = scanner.register_root(conn, str(base.resolve()), "Demo Camera Drive")
        result = scanner.scan_root(conn, root_id)
        # Register a cached preview for the drone clip to demonstrate the
        # "preview available / original offline" state.
        drone = conn.execute("SELECT id FROM assets WHERE name='DJI_0007.mp4'").fetchone()
        if drone:
            exists = conn.execute(
                "SELECT 1 FROM cached_previews WHERE asset_id=?", (drone["id"],)).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO cached_previews(asset_id, kind, location, path, bytes, created_at, source)
                       VALUES(?,?,?,?,?,?,?)""",
                    (drone["id"], "thumbnail", "host",
                     str(base / "02_DRONE/thumbs/DJI_0007.svg"),
                     (base / "02_DRONE/thumbs/DJI_0007.svg").stat().st_size,
                     now_iso(), "demo-seed"))
                conn.commit()
        db.log_event(conn, "demo.seeded", subject=root_id, detail=str(base))
        conn.commit()
        print(json.dumps({"sample_dir": str(base.resolve()), "root_id": root_id,
                          "scan": result.as_dict()}, indent=2))
    finally:
        conn.close()


def cmd_seed_reviews(args):
    """Populate the demo drive with a realistic review session.

    Labelled as demo data in the audit log. Safe to re-run: it skips anything
    that already exists, and it never touches real media.
    """
    from .core import review
    from .core.db import log_event

    conn = _conn(args)
    try:
        root = conn.execute("SELECT id, label FROM roots ORDER BY created_at LIMIT 1").fetchone()
        if root is None:
            print(json.dumps({"error": "no roots registered — run seed-demo first"}))
            return
        assets = {r["name"]: dict(r) for r in conn.execute(
            "SELECT * FROM assets WHERE root_id=? AND deleted_at IS NULL", (root["id"],))}

        def pick(*names):
            for n in names:
                if n in assets:
                    return assets[n]
            return None

        hero = pick("A001_C003.mp4", "DJI_0007.mp4", "final_cut_v3.mp4")
        second = pick("DJI_0007.mp4", "A001_C004.mp4")
        if hero is None:
            print(json.dumps({"error": "demo assets missing — run seed-demo first"}))
            return

        existing = conn.execute("SELECT COUNT(*) c FROM asset_comments WHERE asset_id=?",
                                (hero["id"],)).fetchone()["c"]
        created = {"comments": 0, "selects": 0, "links": 0}
        if existing == 0:
            c1 = review.add_comment(conn, asset_id=hero["id"], author="Jonathan Fern",
                                    body="Still grading the other footage, but here's the sneak peek. "
                                         "The rest lands on the drive within the hour.",
                                    timecode_ms=120000, fps=25, visibility="shared")
            review.add_comment(conn, asset_id=hero["id"], author="Kai Nguyen",
                               body="I love it already.", parent_id=c1["id"],
                               timecode_ms=120000, fps=25, visibility="shared")
            c3 = review.add_comment(conn, asset_id=hero["id"], author="Client Jane",
                                    author_kind="guest",
                                    body="Perfect for socials — can we hold this frame a beat longer?",
                                    timecode_ms=32000, fps=25, visibility="shared")
            review.add_comment(conn, asset_id=hero["id"], author="Simon Greene",
                               body="We'll need to play with the aspect ratio but agreed.",
                               parent_id=c3["id"], timecode_ms=32000, fps=25,
                               visibility="shared")
            review.add_comment(conn, asset_id=hero["id"], author="producer",
                               body="Internal: audio bed needs a licence check before delivery.",
                               timecode_ms=45000, fps=25, visibility="internal")
            created["comments"] = 5

        if second:
            review.set_select(conn, asset_id=hero["id"], reviewer="producer",
                              decision="keep", note="Hero shot for the open")
            review.set_select(conn, asset_id=second["id"], reviewer="producer",
                              decision="maybe", note="Great movement, check the horizon")
            created["selects"] = 2

        if not review.list_links(conn, asset_id=hero["id"]):
            link = review.create_review_link(conn, asset_id=hero["id"],
                                             expires_in_hours=168,
                                             created_by="producer",
                                             note="demo client link")
            created["links"] = 1
        else:
            link = None

        log_event(conn, "demo.reviews_seeded", subject=hero["id"], detail=json.dumps(created))
        conn.commit()
        out = {"asset": hero["name"], **created}
        if link:
            out["guest_link_path"] = link["url_path"]
            out["note"] = "open http://localhost:8765" + link["url_path"] + " for the guest view"
        print(json.dumps(out, indent=2))
    finally:
        conn.close()


def cmd_spike_open(args):
    from .spikes.mp4_remote_open import analyse_source
    report = analyse_source(args.source, args.token, args.seeks,
                            int(args.chunk_mb * 1024 * 1024))
    print(json.dumps(report, indent=2))


def cmd_nle_matrix(args):
    print(json.dumps(registry.capability_matrix(), indent=2))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lfmc", description="Local-first media collaboration (MVP scaffold)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"catalogue database path (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create/upgrade the catalogue").set_defaults(func=cmd_init)

    p = sub.add_parser("add-root", help="register a drive/folder as indexable")
    p.add_argument("path")
    p.add_argument("--label")
    p.add_argument("--scan", action="store_true", help="scan immediately")
    p.set_defaults(func=cmd_add_root)

    p = sub.add_parser("scan", help="incremental scan of a root")
    p.add_argument("root_id")
    p.add_argument("--force", action="store_true", help="re-hash every file")
    p.set_defaults(func=cmd_scan)

    sub.add_parser("status", help="roots and asset counts").set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="search the local catalogue")
    p.add_argument("query")
    p.add_argument("--root-id")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("seed-demo", help="create a synthetic sample drive and catalogue it")
    p.add_argument("--dir", default=DEFAULT_SAMPLE)
    p.set_defaults(func=cmd_seed_demo)

    sub.add_parser("seed-reviews",
                   help="add a demo comment thread, selects and a guest link").set_defaults(
        func=cmd_seed_reviews)

    p = sub.add_parser("spike-open", help="measure ranged-read cost of opening an MP4/MOV")
    p.add_argument("source", help="local path or http(s) URL")
    p.add_argument("--token", default=os.environ.get("LFMC_TOKEN"))
    p.add_argument("--seeks", type=int, default=10)
    p.add_argument("--chunk-mb", type=float, default=2.0)
    p.set_defaults(func=cmd_spike_open)

    sub.add_parser("nle-matrix", help="print the NLE capability matrix").set_defaults(func=cmd_nle_matrix)

    p = sub.add_parser("serve", help="run the local control plane + media server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default=os.environ.get("LFMC_TOKEN"))
    p.add_argument("--demo", action="store_true",
                   help="disable token checks (DEMO ONLY, synthetic data only)")
    p.set_defaults(func=cmd_serve)
    return ap


def cmd_serve(args):
    from .net.server import Config, serve
    serve(Config(db_path=args.db, token=args.token, demo=args.demo,
                 host=args.host, port=args.port,
                 public_url=f"http://127.0.0.1:{args.port}"))


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
