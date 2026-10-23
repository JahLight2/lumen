"""Spike: open and inspect a remote MP4/MOV using ranged reads only.

What this actually proves
-------------------------
Editing software and media decoders do not need the whole file up front; they
need *structure* (ftyp + moov headers, sample tables) and then scattered media
chunks. This module parses the ISO-BMFF box tree of a remote file by issuing
HTTP Range requests, and reports exactly how many bytes crossed the wire.

What this does NOT prove
------------------------
It is not evidence that Premiere Pro or Resolve can open a file through our
mount. That requires the real filesystem layer (FUSE on macOS, WinFsp on
Windows) plus real NLEs on real machines. See docs/RISKS.md (gate A).

The parser is real code (not a mock) and is unit-tested against synthetic files
with known box layouts. The seek-cost number is explicitly a MODEL.
"""

from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field

CONTAINER_BOXES = {"moov", "trak", "mdia", "minf", "stbl", "mvex", "moof", "traf",
                   "edts", "dinf", "udta", "meta", "ilst", "wave"}
HEADER_SIZES = (8, 16, 20)  # 32-bit, 64-bit (largesize), and uuid variants


@dataclass
class Box:
    type: str
    offset: int
    header_size: int
    size: int

    @property
    def payload_offset(self) -> int:
        return self.offset + self.header_size

    @property
    def payload_size(self) -> int:
        return max(0, self.size - self.header_size)

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass
class RemoteStats:
    requests: int = 0
    bytes_fetched: int = 0
    ranges: list = field(default_factory=list)

    def note(self, offset: int, length: int) -> None:
        self.requests += 1
        self.bytes_fetched += length
        self.ranges.append((offset, length))


class FileRangeReader:
    """Random-access reader over a local file (used for tests/baseline)."""

    def __init__(self, path: str):
        import os
        self.path = path
        self.size = os.path.getsize(path)
        self.stats = RemoteStats()
        self._fh = open(path, "rb")

    def read_at(self, offset: int, length: int) -> bytes:
        length = max(0, min(length, self.size - offset))
        if length <= 0:
            return b""
        self._fh.seek(offset)
        data = self._fh.read(length)
        self.stats.note(offset, len(data))
        return data

    def close(self) -> None:
        self._fh.close()


class HttpRangeReader:
    """Random-access reader over HTTP using `Range:` requests."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 20.0):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.stats = RemoteStats()
        self.size = self._probe_size()

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"User-Agent": "lfmc-spike/0.1"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def _probe_size(self) -> int:
        req = urllib.request.Request(self.url, method="HEAD", headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            length = resp.headers.get("Content-Length")
            if length is None:
                raise RuntimeError("server did not report Content-Length")
            accept = resp.headers.get("Accept-Ranges", "")
            if accept.lower() != "bytes":
                raise RuntimeError(f"server does not advertise byte ranges ({accept!r})")
            return int(length)

    def read_at(self, offset: int, length: int) -> bytes:
        if offset >= self.size or length <= 0:
            return b""
        end = min(self.size - 1, offset + length - 1)
        req = urllib.request.Request(
            self.url, headers=self._headers({"Range": f"bytes={offset}-{end}"})
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 206:
                    raise RuntimeError(
                        f"expected 206 Partial Content, got {resp.status}; "
                        "server may be sending whole files"
                    )
                data = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"range request failed: HTTP {exc.code}") from exc
        self.stats.note(offset, len(data))
        return data

    def close(self) -> None:
        pass


def _parse_header(buf: bytes, offset: int) -> Box | None:
    if len(buf) < 8:
        return None
    size32, typ = struct.unpack(">I4s", buf[:8])
    header = 8
    if size32 == 1:
        if len(buf) < 16:
            return None
        (size,) = struct.unpack(">Q", buf[8:16])
        header = 16
    elif size32 == 0:
        return None  # "to end of file": caller resolves
    else:
        size = size32
    return Box(type=typ.decode("latin-1"), offset=offset, header_size=header, size=size)


def read_box_tree(reader, start: int = 0, end: int | None = None, depth: int = 0,
                  max_depth: int = 2, max_children: int = 64) -> list[Box]:
    """Walk top-level boxes (and containers to max_depth) via ranged reads."""
    end = reader.size if end is None else end
    boxes: list[Box] = []
    pos = start
    while pos < end and len(boxes) < max_children:
        header = reader.read_at(pos, 16)
        box = _parse_header(header, pos)
        if box is None or box.size < 8:
            break
        boxes.append(box)
        pos = box.end
    derived: list[Box] = []
    for box in boxes:
        derived.append(box)
        if depth < max_depth and box.type in CONTAINER_BOXES and box.payload_size > 0:
            children = read_box_tree(reader, box.payload_offset, box.end,
                                     depth + 1, max_depth, max_children)
            derived.extend(children)
    return derived


@dataclass
class OpenAnalysis:
    file_size: int
    box_count: int
    top_boxes: list[dict]
    moov_offset: int | None
    moov_size: int | None
    moov_before_mdat: bool | None
    bytes_fetched_for_open: int
    requests_for_open: int
    open_bytes_ratio: float
    modelled_seek_bytes: int
    modelled_total_ratio: float
    model_notes: str


def analyze(reader, seeks: int = 10, chunk_bytes: int = 2 * 1024 * 1024) -> OpenAnalysis:
    """Parse structure; model the cost of N random seeks.

    The seek number is a MODEL: a real NLE reads GOP-aligned chunks sized by
    codec/bitrate, and its exact pattern must be measured on real machines with
    a real mount (see docs/RISKS.md). We keep the model deliberately crude and
    label it as such rather than presenting it as a measurement.
    """
    before = reader.stats.bytes_fetched
    reqs = reader.stats.requests
    tree = read_box_tree(reader, 0, None, 0, max_depth=1)
    top = [b for b in tree if b.offset < 1024 * 1024 * 1024 and b.header_size in HEADER_SIZES]
    top_level = _top_level(tree)
    moov = next((b for b in top_level if b.type in ("moov", "moof")), None)
    boxes_out = [{"type": b.type, "offset": b.offset, "size": b.size} for b in top_level[:24]]
    mdat = next((b for b in top_level if b.type == "mdat"), None)
    fetched = reader.stats.bytes_fetched - before
    requests = reader.stats.requests - reqs
    modelled = seeks * chunk_bytes
    total_modelled = fetched + modelled
    size = max(1, reader.size)
    return OpenAnalysis(
        file_size=reader.size,
        box_count=len(tree),
        top_boxes=boxes_out,
        moov_offset=moov.offset if moov else None,
        moov_size=moov.size if moov else None,
        moov_before_mdat=(moov.offset < mdat.offset) if (moov and mdat) else None,
        bytes_fetched_for_open=fetched,
        requests_for_open=requests,
        open_bytes_ratio=round(fetched / size, 6),
        modelled_seek_bytes=modelled,
        modelled_total_ratio=round(total_modelled / size, 6),
        model_notes=(
            f"Seek cost is MODELLED as {seeks} x {chunk_bytes} bytes "
            "(typical GOP-aligned read). Not measured behaviour of Premiere/Resolve."
        ),
    )


def _top_level(tree: list[Box]) -> list[Box]:
    """Boxes that are not contained by another returned box."""
    out: list[Box] = []
    for b in tree:
        contained = any(o is not b and o.offset <= b.offset and b.end <= o.end for o in tree)
        if not contained:
            out.append(b)
    return sorted(out, key=lambda b: b.offset)


def analyse_source(source: str, token: str | None = None, seeks: int = 10,
                   chunk_bytes: int = 2 * 1024 * 1024) -> dict:
    if source.startswith(("http://", "https://")):
        reader = HttpRangeReader(source, token)
    else:
        reader = FileRangeReader(source)
    try:
        result = analyze(reader, seeks=seeks, chunk_bytes=chunk_bytes)
        out = result.__dict__
        out["stats"] = {"requests": reader.stats.requests, "bytes": reader.stats.bytes_fetched,
                        "first_ranges": reader.stats.ranges[:6]}
        return out
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# Synthetic ISO-BMFF writer (test fixture; also used by the CLI demo)
# ---------------------------------------------------------------------------

def build_box(box_type: str, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type.encode("latin-1")[:4]) + payload


def write_synthetic_mp4(path: str, media_bytes: int = 8 * 1024 * 1024,
                        moov_at_end: bool = True, sparse: bool = True) -> dict:
    """Write a structurally valid-enough MP4 skeleton for parser/cost tests.

    Not a playable video: it has ftyp/moov/mdat with correct box framing so the
    reader and HTTP layer can be exercised deterministically. `sparse=True`
    seeks past the media payload so large fixtures cost ~no disk.
    """
    ftyp = build_box("ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    moov = build_box(
        "moov",
        build_box("mvhd", b"\x00" * 100)
        + build_box("trak", build_box("mdia", build_box("mdhd", b"\x00" * 24)
                                      + build_box("minf", build_box("stbl", b"\x00" * 64)))),
    )
    mdat_payload_len = max(0, media_bytes)
    mdat_header = struct.pack(">I4s", 8 + mdat_payload_len, b"mdat")

    with open(path, "wb") as fh:
        fh.write(ftyp)
        if not moov_at_end:
            fh.write(moov)
        fh.write(mdat_header)
        if sparse:
            fh.seek(mdat_payload_len, 1)  # sparse hole: payload only, header already written
        else:
            remaining = mdat_payload_len
            while remaining > 0:
                n = min(remaining, 1 << 20)
                fh.write(b"\x00" * n)
                remaining -= n
        if moov_at_end:
            fh.write(moov)
        fh.write(b"\x00" * 0)
    import os
    return {"path": path, "file_size": os.path.getsize(path),
            "moov_at_end": moov_at_end, "moov_size": len(moov), "ftyp_size": len(ftyp)}


if __name__ == "__main__":
    import argparse
    import tempfile
    import os

    ap = argparse.ArgumentParser(description="ISO-BMFF ranged-read spike")
    ap.add_argument("source", nargs="?", help="local path or http(s) URL of an MP4/MOV")
    ap.add_argument("--token", default=os.environ.get("LFMC_TOKEN"))
    ap.add_argument("--seeks", type=int, default=10)
    ap.add_argument("--chunk-mb", type=float, default=2.0)
    ap.add_argument("--demo-size-mb", type=float, default=64.0,
                    help="size of the synthetic fixture used when no source is given")
    args = ap.parse_args()

    if not args.source:
        tmp = os.path.join(tempfile.gettempdir(), "lfmc_synthetic.mp4")
        info = write_synthetic_mp4(tmp, media_bytes=int(args.demo_size_mb * 1024 * 1024))
        print("synthetic fixture:", json.dumps(info, indent=2))
        args.source = tmp

    report = analyse_source(args.source, args.token, args.seeks,
                            int(args.chunk_mb * 1024 * 1024))
    print(json.dumps(report, indent=2))
