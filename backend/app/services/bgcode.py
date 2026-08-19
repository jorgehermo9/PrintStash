"""Reader for PrusaSlicer binary G-code (``.bgcode``) metadata and thumbnails.

Only the metadata blocks (File / Printer / Print / Slicer) and thumbnail blocks
are read; the heatshrink-compressed G-code blocks are skipped entirely. Those
metadata blocks are stored either uncompressed or zlib-deflate compressed — both
handled by the stdlib — so reading bgcode metadata needs no extra dependency.

The format is the libbgcode container: a 10-byte file header (magic ``GCDE``,
``version`` uint32, ``checksum_type`` uint16) followed by a sequence of blocks.
Each block has an 8-byte header (``type`` uint16, ``compression`` uint16,
``uncompressed_size`` uint32), an optional ``compressed_size`` uint32 when
compressed, block-specific parameters, the (possibly compressed) data, and an
optional CRC32 when the file declares a checksum.

Never raises on malformed input — callers get ``None`` / an empty iterator.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Iterator, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

MAGIC = b"GCDE"

# Block types.
_FILE_METADATA = 0
_GCODE = 1
_SLICER_METADATA = 2
_PRINTER_METADATA = 3
_PRINT_METADATA = 4
_THUMBNAIL = 5
_METADATA_TYPES = frozenset(
    {_FILE_METADATA, _SLICER_METADATA, _PRINTER_METADATA, _PRINT_METADATA}
)

# Compression. 2/3 are Heatshrink, only ever used by G-code blocks, which we
# never read — so the stdlib's deflate is all we need.
_COMP_NONE = 0
_COMP_DEFLATE = 1

# Thumbnail image formats (block parameters), mapped to a hint extension.
THUMBNAIL_FORMATS = {0: "png", 1: "jpg", 2: "qoi"}

# Safety caps. bgcode is a third-party / user-supplied file; bound everything so
# a truncated or hostile container can't drive us into an unbounded read.
_MAX_BLOCKS = 4096
_MAX_BLOCK_DATA = 64 * 1024 * 1024  # per-block on-disk data we'll read
_MAX_METADATA_BYTES = 8 * 1024 * 1024  # total decompressed metadata text
_MAX_THUMBNAIL_BYTES = 32 * 1024 * 1024


def is_bgcode(path: Path) -> bool:
    """True if the file begins with the bgcode magic ``GCDE``."""
    try:
        with path.open("rb") as fh:
            return fh.read(4) == MAGIC
    except OSError as e:
        logger.warning("bgcode: cannot read %s: %s", path, e)
        return False


def _parse_file_header(fh) -> Optional[int]:
    """Validate the file header; return the checksum type, or None if not bgcode."""
    header = fh.read(10)
    if len(header) < 10 or header[:4] != MAGIC:
        return None
    _version, checksum_type = struct.unpack_from("<IH", header, 4)
    return checksum_type


def _block_param_len(btype: int) -> int:
    if btype == _THUMBNAIL:
        return 6  # format, width, height (uint16 each)
    if btype in _METADATA_TYPES or btype == _GCODE:
        return 2  # encoding (uint16)
    return 0


def _walk(fh, want: frozenset[int]) -> Iterator[Tuple[int, int, bytes, bytes]]:
    """Yield ``(block_type, compression, params, data)`` for wanted block types.

    Data for unwanted blocks (notably the large G-code/heatshrink bodies) is
    seeked past, never read, so walking a multi-MB file stays cheap.
    """
    checksum_type = _parse_file_header(fh)
    if checksum_type is None:
        return

    for _ in range(_MAX_BLOCKS):
        block_header = fh.read(8)
        if len(block_header) < 8:
            return
        btype, compression, usize = struct.unpack("<HHI", block_header)

        if compression != _COMP_NONE:
            raw = fh.read(4)
            if len(raw) < 4:
                return
            (data_len,) = struct.unpack("<I", raw)
        else:
            data_len = usize

        param_len = _block_param_len(btype)
        params = fh.read(param_len)
        if len(params) < param_len:
            return

        if data_len < 0 or data_len > _MAX_BLOCK_DATA:
            return

        if btype in want:
            data = fh.read(data_len)
            if len(data) < data_len:
                return
            yield btype, compression, params, data
        else:
            fh.seek(data_len, 1)

        if checksum_type != 0:
            fh.seek(4, 1)


def _decompress(
    compression: int, data: bytes, *, max_output: int = _MAX_METADATA_BYTES
) -> Optional[bytes]:
    if compression == _COMP_NONE:
        return data if len(data) <= max_output else None
    if compression == _COMP_DEFLATE:
        try:
            decompressor = zlib.decompressobj()
            output = decompressor.decompress(data, max_output + 1)
            if len(output) > max_output or decompressor.unconsumed_tail:
                return None
            output += decompressor.flush(max_output + 1 - len(output))
            if (
                len(output) > max_output
                or not decompressor.eof
                or decompressor.unused_data
                or decompressor.unconsumed_tail
            ):
                return None
            return output
        except zlib.error:
            return None
    # Heatshrink (G-code blocks only) — never reached for metadata/thumbnails.
    return None


def read_metadata_text(path: Path) -> Optional[str]:
    """Render bgcode metadata blocks as G-code-style comment lines.

    The metadata is INI ``key=value`` text; prefixing each line with ``"; "``
    lets the existing :mod:`app.services.gcode_parser` patterns match it exactly
    as if it had come from an ASCII G-code header. Returns None when the file is
    not bgcode or carries no readable metadata.
    """
    lines: list[str] = []
    total = 0
    try:
        with path.open("rb") as fh:
            for btype, compression, _params, data in _walk(fh, _METADATA_TYPES):
                raw = _decompress(compression, data, max_output=_MAX_METADATA_BYTES)
                if raw is None:
                    continue
                total += len(raw)
                if total > _MAX_METADATA_BYTES:
                    break
                text = raw.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    lines.append("; " + line)
                    # File metadata records the slicer as ``Producer=...``;
                    # synthesise a "generated by" line so the parser's
                    # slicer-name/version detection fires off the same value.
                    if btype == _FILE_METADATA and line.lower().startswith("producer="):
                        lines.append("; generated by " + line.split("=", 1)[1].strip())
    except OSError as e:
        logger.warning("bgcode: cannot read metadata %s: %s", path, e)
        return None

    if not lines:
        return None
    return "\n".join(lines) + "\n"


def iter_thumbnails(path: Path) -> Iterator[Tuple[int, int, int, bytes]]:
    """Yield ``(format, width, height, image_bytes)`` for each thumbnail block."""
    try:
        with path.open("rb") as fh:
            for _btype, compression, params, data in _walk(fh, frozenset({_THUMBNAIL})):
                raw = _decompress(compression, data, max_output=_MAX_THUMBNAIL_BYTES)
                if raw is None or len(params) < 6:
                    continue
                fmt, width, height = struct.unpack_from("<HHH", params, 0)
                yield fmt, width, height, raw
    except OSError as e:
        logger.warning("bgcode: cannot read thumbnails %s: %s", path, e)
        return
