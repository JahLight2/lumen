"""HTTP Range semantics, isolated from the request handler so it is testable.

Only single-range reads are supported (`bytes=a-b`, `bytes=a-`, `bytes=-n`),
which is what media players and NLEs use in practice. Multi-range requests get
a 200-with-full-file? No: we answer 416 with an explicit capability header
instead, so a client never silently receives a whole file it did not ask for.
That choice is deliberate: silent full-file responses are exactly how projects
accidentally stage multi-gigabyte originals.
"""

from __future__ import annotations

from dataclasses import dataclass

STREAM_CHUNK = 64 * 1024


class RangeError(ValueError):
    """Malformed or unsatisfiable Range header."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range_header(value: str | None, size: int) -> ByteRange | None:
    """Parse a single-range header.

    Returns None when there is no header (caller may serve 200 only if it is a
    deliberate full download). Raises RangeError when malformed/unsatisfiable.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if not raw.lower().startswith("bytes="):
        raise RangeError("only byte ranges are supported")
    spec = raw.split("=", 1)[1].strip()
    if "," in spec:
        raise RangeError("multiple ranges not supported")
    if size <= 0:
        raise RangeError("empty resource")
    if "-" not in spec:
        raise RangeError("malformed range")
    start_s, end_s = spec.split("-", 1)
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if start_s == "":
            if end_s == "":
                raise RangeError("malformed range")
            suffix = int(end_s)
            if suffix <= 0:
                raise RangeError("invalid suffix length")
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_s)
            if start < 0 or start >= size:
                raise RangeError("start beyond end of resource")
            end = size - 1 if end_s == "" else int(end_s)
            if end < start:
                raise RangeError("end before start")
            end = min(end, size - 1)
    except ValueError as exc:  # non-numeric
        raise RangeError("non-numeric range") from exc
    return ByteRange(start, end)


def content_range_header(rng: ByteRange, size: int) -> str:
    return f"bytes {rng.start}-{rng.end}/{size}"


def unsatisfied_content_range(size: int) -> str:
    return f"bytes */{size}"


def iter_file_range(path, rng: ByteRange, chunk_size: int = STREAM_CHUNK):
    """Yield file bytes for the range, streaming (never loads the whole file)."""
    remaining = rng.length
    with open(path, "rb") as fh:
        fh.seek(rng.start)
        while remaining > 0:
            block = fh.read(min(chunk_size, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block
