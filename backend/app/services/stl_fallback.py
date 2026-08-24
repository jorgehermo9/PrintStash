"""Bounded-memory STL thumbnail fallback used when full mesh loading is unsafe."""

from __future__ import annotations

import io
import math
import struct
from array import array
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class STLThumbnailResult:
    png: bytes
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    triangle_count: int
    sampled_triangles: int
    scanned_bytes: int = 0
    parsed_triangles: int = 0


@dataclass
class _SampledSTL:
    """A bounded sample and the work spent obtaining it."""

    coordinates: array
    triangle_count: int
    sampled_triangles: int
    bounds_min: tuple[float, float, float] | None
    bounds_max: tuple[float, float, float] | None
    scanned_bytes: int
    parsed_triangles: int


_BINARY_HEADER_BYTES = 84
_BINARY_TRIANGLE = struct.Struct("<12fH")
# This is a hard facet-work budget. Binary files are sampled by deterministic
# seeks and ASCII files stop after this many parsed facets. Neither path gets
# a second pass over the source.
_MAX_SAMPLED_TRIANGLES = 100_000
_COVERAGE_CHUNK_TRIANGLES = 2_048
_MAX_ASCII_LINE_BYTES = 64 * 1024
_MAX_ASCII_BYTES = 16 * 1024 * 1024
_MAX_ASCII_LINES = 1_000_000
_FLOAT32_MAX = 3.4028234663852886e38
_MAX_RENDER_DIMENSION = 2048


def _binary_stl_info(path: Path) -> tuple[int, int] | None:
    """Return ``(declared facets, file size)`` for a valid binary STL."""

    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(_BINARY_HEADER_BYTES)
        if len(header) != _BINARY_HEADER_BYTES:
            return None
        count = struct.unpack("<I", header[80:84])[0]
        expected_size = _BINARY_HEADER_BYTES + count * _BINARY_TRIANGLE.size
        if count == 0 or size < expected_size:
            return None
        return count, size
    except (OSError, struct.error):
        return None


def _is_binary_stl(path: Path) -> bool:
    return _binary_stl_info(path) is not None


def _valid_coordinate(value: float) -> bool:
    """Return whether *value* is finite and representable in float32."""

    return math.isfinite(value) and abs(value) <= _FLOAT32_MAX


def _iter_binary_triangles(
    path: Path, *, max_triangles: int = _MAX_SAMPLED_TRIANGLES
) -> Iterator[tuple[float, ...]]:
    """Yield at most *max_triangles* records for compatibility/debugging."""

    try:
        with path.open("rb") as stream:
            header = stream.read(_BINARY_HEADER_BYTES)
            if len(header) != _BINARY_HEADER_BYTES:
                return
            count = struct.unpack("<I", header[80:84])[0]
            limit = min(max(max_triangles, 0), count)
            for _ in range(limit):
                record = stream.read(_BINARY_TRIANGLE.size)
                if len(record) != _BINARY_TRIANGLE.size:
                    return
                values = _BINARY_TRIANGLE.unpack(record)
                triangle = tuple(float(value) for value in values[3:12])
                if all(_valid_coordinate(value) for value in triangle):
                    yield triangle
    except OSError:
        return


def _iter_ascii_triangles(
    path: Path,
    *,
    max_bytes: int = _MAX_ASCII_BYTES,
    max_lines: int = _MAX_ASCII_LINES,
    max_triangles: int = _MAX_SAMPLED_TRIANGLES,
) -> Iterator[tuple[float, ...]]:
    """Yield ASCII facets while bounding bytes, lines, line size, and facets."""

    vertices: list[float] = []
    scanned_bytes = 0
    lines = 0
    parsed = 0
    try:
        with path.open("rb") as stream:
            while (
                scanned_bytes < max(max_bytes, 0)
                and lines < max(max_lines, 0)
                and parsed < max(max_triangles, 0)
            ):
                remaining = max_bytes - scanned_bytes
                read_limit = min(_MAX_ASCII_LINE_BYTES + 1, remaining)
                if read_limit <= 0:
                    return
                raw_line = stream.readline(read_limit)
                if not raw_line:
                    return
                scanned_bytes += len(raw_line)
                lines += 1
                if len(raw_line) > _MAX_ASCII_LINE_BYTES:
                    vertices.clear()
                    continue
                line = raw_line.decode("ascii", errors="ignore")
                parts = line.lstrip().split()
                if len(parts) != 4 or parts[0].lower() != "vertex":
                    continue
                try:
                    values = [float(value) for value in parts[1:]]
                except ValueError:
                    vertices.clear()
                    continue
                if not all(_valid_coordinate(value) for value in values):
                    vertices.clear()
                    continue
                vertices.extend(values)
                if len(vertices) == 9:
                    parsed += 1
                    yield tuple(vertices)
                    vertices.clear()
    except OSError:
        return


def _iter_stl_triangles(
    path: Path, *, max_triangles: int = _MAX_SAMPLED_TRIANGLES
) -> Iterator[tuple[float, ...]]:
    if _is_binary_stl(path):
        yield from _iter_binary_triangles(path, max_triangles=max_triangles)
    else:
        yield from _iter_ascii_triangles(path, max_triangles=max_triangles)


def _update_bounds(
    lower: list[float], upper: list[float], triangle: tuple[float, ...]
) -> None:
    for offset in (0, 3, 6):
        for axis in range(3):
            value = triangle[offset + axis]
            lower[axis] = min(lower[axis], value)
            upper[axis] = max(upper[axis], value)


def _binary_sample_indices(count: int, sample_count: int) -> Iterator[int]:
    """Yield stratified records, retaining the first and last facet."""

    for sample_index in range(sample_count):
        if sample_index == 0:
            yield 0
            continue
        if sample_index == sample_count - 1:
            yield count - 1
            continue
        yield (sample_index * count + count // 2) // sample_count


def _read_binary_samples(
    path: Path, budget: int, info: tuple[int, int] | None = None
) -> _SampledSTL | None:
    info = info or _binary_stl_info(path)
    if info is None:
        return None
    triangle_count, _ = info
    sample_count = min(triangle_count, budget)
    if sample_count == 0:
        return None
    coordinates = array("f")
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    parsed = 0
    try:
        with path.open("rb") as stream:
            for index in _binary_sample_indices(triangle_count, sample_count):
                stream.seek(_BINARY_HEADER_BYTES + index * _BINARY_TRIANGLE.size)
                record = stream.read(_BINARY_TRIANGLE.size)
                if len(record) != _BINARY_TRIANGLE.size:
                    break
                values = _BINARY_TRIANGLE.unpack(record)
                triangle = tuple(float(value) for value in values[3:12])
                if not all(_valid_coordinate(value) for value in triangle):
                    continue
                coordinates.extend(triangle)
                _update_bounds(lower, upper, triangle)
                parsed += 1
    except (OSError, ValueError):
        return None
    if parsed == 0:
        return None
    return _SampledSTL(
        coordinates=coordinates,
        triangle_count=triangle_count,
        sampled_triangles=parsed,
        bounds_min=(lower[0], lower[1], lower[2]),
        bounds_max=(upper[0], upper[1], upper[2]),
        scanned_bytes=_BINARY_HEADER_BYTES + sample_count * _BINARY_TRIANGLE.size,
        parsed_triangles=parsed,
    )


def _read_ascii_samples(
    path: Path, budget: int, probe_bytes: int = _BINARY_HEADER_BYTES
) -> _SampledSTL | None:
    coordinates = array("f")
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    vertices: list[float] = []
    scanned_bytes = probe_bytes
    lines = 0
    parsed = 0
    try:
        with path.open("rb") as stream:
            while (
                scanned_bytes < _MAX_ASCII_BYTES
                and lines < _MAX_ASCII_LINES
                and parsed < budget
            ):
                remaining = _MAX_ASCII_BYTES - scanned_bytes
                read_limit = min(_MAX_ASCII_LINE_BYTES + 1, remaining)
                if read_limit <= 0:
                    break
                raw_line = stream.readline(read_limit)
                if not raw_line:
                    break
                scanned_bytes += len(raw_line)
                lines += 1
                if len(raw_line) > _MAX_ASCII_LINE_BYTES:
                    vertices.clear()
                    continue
                parts = raw_line.decode("ascii", errors="ignore").lstrip().split()
                if len(parts) != 4 or parts[0].lower() != "vertex":
                    continue
                try:
                    values = [float(value) for value in parts[1:]]
                except ValueError:
                    vertices.clear()
                    continue
                if not all(_valid_coordinate(value) for value in values):
                    vertices.clear()
                    continue
                vertices.extend(values)
                if len(vertices) == 9:
                    triangle = tuple(vertices)
                    coordinates.extend(triangle)
                    _update_bounds(lower, upper, triangle)
                    parsed += 1
                    vertices.clear()
    except OSError:
        return None
    if parsed == 0:
        return None
    return _SampledSTL(
        coordinates=coordinates,
        triangle_count=parsed,
        sampled_triangles=parsed,
        bounds_min=(lower[0], lower[1], lower[2]),
        bounds_max=(upper[0], upper[1], upper[2]),
        scanned_bytes=scanned_bytes,
        parsed_triangles=parsed,
    )


def _read_samples(path: Path, budget: int) -> _SampledSTL | None:
    info = _binary_stl_info(path)
    if info is not None:
        return _read_binary_samples(path, budget, info)
    try:
        probe_bytes = min(path.stat().st_size, _BINARY_HEADER_BYTES)
    except OSError:
        return None
    return _read_ascii_samples(path, budget, probe_bytes)


def render_stl_thumbnail(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    max_triangles: int | None = None,
) -> STLThumbnailResult | None:
    """Read and rasterise a bounded, spatially covered STL representation.

    Binary files use midpoint-stratified seeks, so their header and selected
    records are the only bytes read. ASCII files are consumed once with byte,
    line, line-length, and facet budgets. Selected facets are rasterised into a
    coarse z-buffer using their actual triangle area, then upscaled. This keeps
    CPU/memory bounded without turning triangles into bounding-box blobs.
    """
    try:
        import numpy as np
        from PIL import Image

        from app.services.mesh_render import _rasterise_triangles, _select_view_rotation
    except ImportError:
        return None

    if not (1 <= width <= _MAX_RENDER_DIMENSION) or not (
        1 <= height <= _MAX_RENDER_DIMENSION
    ):
        return None
    requested_budget = (
        _MAX_SAMPLED_TRIANGLES if max_triangles is None else max(max_triangles, 1)
    )
    work_budget = min(requested_budget, _MAX_SAMPLED_TRIANGLES)
    sampled = _read_samples(path, work_budget)
    if sampled is None or sampled.bounds_min is None or sampled.bounds_max is None:
        return None

    try:
        triangles = np.frombuffer(sampled.coordinates, dtype=np.float32).reshape(
            (-1, 3, 3)
        )
        corners = np.asarray(
            list(
                product(
                    *zip(
                        sampled.bounds_min,
                        sampled.bounds_max,
                        strict=True,
                    )
                )
            ),
            dtype=np.float64,
        )
        if not np.isfinite(triangles).all() or not np.isfinite(corners).all():
            return None
        center = (np.asarray(sampled.bounds_min) + np.asarray(sampled.bounds_max)) * 0.5
        if not np.isfinite(center).all():
            return None
        rotation = _select_view_rotation(corners - center, np).astype(np.float64)
        view_corners = (corners - center) @ rotation.T
        if not np.isfinite(rotation).all() or not np.isfinite(view_corners).all():
            return None
        extent_x = max(float(np.ptp(view_corners[:, 0])), 1e-6)
        extent_y = max(float(np.ptp(view_corners[:, 1])), 1e-6)
        margin = 0.18
        scale = min(
            width * (1 - 2 * margin) / extent_x,
            height * (1 - 2 * margin) / extent_y,
        )
        view_mid = (view_corners.max(axis=0) + view_corners.min(axis=0)) * 0.5
        if not math.isfinite(scale) or scale <= 0 or not np.isfinite(view_mid).all():
            return None
    except (FloatingPointError, ValueError, RuntimeError):
        return None

    # Half-resolution coverage keeps narrow silhouettes continuous while still
    # bounding candidate-pixel work to a quarter of a full-resolution render.
    coverage_width = max(1, min(width, max(64, width // 2)))
    coverage_height = max(1, min(height, max(48, height // 2)))
    coarse_image = np.zeros((coverage_height, coverage_width, 3), dtype=np.uint8)
    coarse_zbuffer = np.full(
        (coverage_height, coverage_width), np.inf, dtype=np.float64
    )
    base_color = np.asarray([176, 190, 214], dtype=np.float32)
    light = np.asarray([-0.45, 0.6, 1.0], dtype=np.float32)
    light /= np.linalg.norm(light)

    def shade(normals):
        diffuse = np.clip(normals @ light, 0.0, 1.0)[:, None]
        return np.clip(0.32 + diffuse * 0.68, 0.0, 1.0)

    coarse_scale_x = coverage_width / width
    coarse_scale_y = coverage_height / height

    def accumulate_chunk(chunk) -> None:
        view = (chunk - center) @ rotation.T
        screen = np.empty_like(view)
        screen[:, :, 0] = (view[:, :, 0] - view_mid[0]) * scale + width * 0.5
        screen[:, :, 1] = height * 0.5 - (view[:, :, 1] - view_mid[1]) * scale
        screen[:, :, 2] = view[:, :, 2]
        valid = np.isfinite(screen).all(axis=(1, 2))
        raw_normal = np.cross(view[:, 1] - view[:, 0], view[:, 2] - view[:, 0])
        normal_length = np.linalg.norm(raw_normal, axis=1)
        area = np.abs(
            (screen[:, 1, 0] - screen[:, 0, 0]) * (screen[:, 2, 1] - screen[:, 0, 1])
            - (screen[:, 2, 0] - screen[:, 0, 0]) * (screen[:, 1, 1] - screen[:, 0, 1])
        )
        valid &= np.isfinite(area) & (area > 1e-9) & (normal_length > 1e-12)
        if not valid.any():
            return
        ids = np.flatnonzero(valid)
        coarse_screen = screen[ids].copy()
        coarse_screen[:, :, 0] *= coarse_scale_x
        coarse_screen[:, :, 1] *= coarse_scale_y
        normals = raw_normal[ids] / normal_length[ids, None]
        normals = np.where(normals[:, 2:3] >= 0, normals, -normals)
        _rasterise_triangles(
            coarse_image,
            coarse_zbuffer,
            coarse_screen,
            normals[:, None, :].repeat(3, axis=1),
            shade,
            base_color,
            coverage_width,
            coverage_height,
        )

    for start in range(0, triangles.shape[0], _COVERAGE_CHUNK_TRIANGLES):
        accumulate_chunk(triangles[start : start + _COVERAGE_CHUNK_TRIANGLES])

    if not np.isfinite(coarse_zbuffer).any():
        return None
    alpha = np.where(np.isfinite(coarse_zbuffer), 255, 0).astype(np.uint8)
    image = np.asarray(
        Image.fromarray(coarse_image, mode="RGB").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.uint8,
    ).copy()
    alpha = np.asarray(
        Image.fromarray(alpha, mode="L").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.uint8,
    )
    rgba = np.dstack([image, alpha])
    output = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(output, format="PNG", optimize=True)
    return STLThumbnailResult(
        png=output.getvalue(),
        bounds_min=sampled.bounds_min,
        bounds_max=sampled.bounds_max,
        triangle_count=sampled.triangle_count,
        sampled_triangles=sampled.sampled_triangles,
        scanned_bytes=sampled.scanned_bytes,
        parsed_triangles=sampled.parsed_triangles,
    )
