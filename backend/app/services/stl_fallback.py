"""Bounded-memory STL thumbnail fallback used when full mesh loading is unsafe."""

from __future__ import annotations

import io
import struct
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


_BINARY_TRIANGLE = struct.Struct("<12fH")
_MAX_SAMPLED_TRIANGLES = 100_000
_RASTER_CHUNK_TRIANGLES = 2_048


def _is_binary_stl(path: Path) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(84)
        if len(header) != 84:
            return False
        count = struct.unpack("<I", header[80:84])[0]
        return count > 0 and size >= 84 + count * _BINARY_TRIANGLE.size
    except (OSError, struct.error):
        return False


def _iter_binary_triangles(path: Path) -> Iterator[tuple[float, ...]]:
    with path.open("rb") as stream:
        header = stream.read(84)
        if len(header) != 84:
            return
        count = struct.unpack("<I", header[80:84])[0]
        for _ in range(count):
            record = stream.read(_BINARY_TRIANGLE.size)
            if len(record) != _BINARY_TRIANGLE.size:
                return
            values = _BINARY_TRIANGLE.unpack(record)
            yield tuple(float(value) for value in values[3:12])


def _iter_ascii_triangles(path: Path) -> Iterator[tuple[float, ...]]:
    vertices: list[float] = []
    with path.open("r", encoding="ascii", errors="ignore") as stream:
        for line in stream:
            parts = line.lstrip().split()
            if len(parts) != 4 or parts[0].lower() != "vertex":
                continue
            try:
                vertices.extend(float(value) for value in parts[1:])
            except ValueError:
                vertices.clear()
                continue
            if len(vertices) == 9:
                yield tuple(vertices)
                vertices.clear()


def _iter_stl_triangles(path: Path) -> Iterator[tuple[float, ...]]:
    if _is_binary_stl(path):
        yield from _iter_binary_triangles(path)
    else:
        yield from _iter_ascii_triangles(path)


def _scan_bounds(
    path: Path,
) -> tuple[int, tuple[float, float, float], tuple[float, float, float]] | None:
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    count = 0
    try:
        for triangle in _iter_stl_triangles(path):
            count += 1
            for offset in (0, 3, 6):
                for axis in range(3):
                    value = triangle[offset + axis]
                    lower[axis] = min(lower[axis], value)
                    upper[axis] = max(upper[axis], value)
    except OSError:
        return None
    if count == 0 or any(value == float("inf") for value in lower):
        return None
    return count, (lower[0], lower[1], lower[2]), (upper[0], upper[1], upper[2])


def render_stl_thumbnail(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    max_triangles: int = _MAX_SAMPLED_TRIANGLES,
) -> STLThumbnailResult | None:
    """Stream twice, uniformly sample, and rasterise into one bounded z-buffer."""
    try:
        import numpy as np
        from PIL import Image

        from app.services.mesh_render import _rasterise_triangles, _select_view_rotation
    except ImportError:
        return None

    scanned = _scan_bounds(path)
    if scanned is None:
        return None
    triangle_count, bounds_min, bounds_max = scanned
    sample_count = min(triangle_count, max(max_triangles, 1))
    # Midpoints of equal-width bins provide deterministic uniform coverage of
    # the whole file without reservoir memory or random state.
    targets = (
        ((sample_index * triangle_count + triangle_count // 2) // sample_count)
        for sample_index in range(sample_count)
    )
    next_target = next(targets, None)

    corners = np.asarray(
        list(product(*zip(bounds_min, bounds_max, strict=True))), dtype=np.float32
    )
    center = (np.asarray(bounds_min) + np.asarray(bounds_max)) * 0.5
    rotation = _select_view_rotation(corners - center, np).astype(np.float32)
    view_corners = (corners - center) @ rotation.T
    extent_x = max(float(np.ptp(view_corners[:, 0])), 1e-6)
    extent_y = max(float(np.ptp(view_corners[:, 1])), 1e-6)
    margin = 0.18
    scale = min(
        width * (1 - 2 * margin) / extent_x,
        height * (1 - 2 * margin) / extent_y,
    )
    view_mid = (view_corners.max(axis=0) + view_corners.min(axis=0)) * 0.5

    image = np.zeros((height, width, 3), dtype=np.uint8)
    z_buffer = np.full((height, width), np.inf, dtype=np.float64)
    base_color = np.asarray([176, 190, 214], dtype=np.float32)
    light = np.asarray([-0.45, 0.6, 1.0], dtype=np.float32)
    light /= np.linalg.norm(light)

    def shade(normals):
        diffuse = np.clip(normals @ light, 0.0, 1.0)[:, None]
        return np.clip(0.32 + diffuse * 0.68, 0.0, 1.0)

    def rasterise(chunk: list[tuple[float, ...]]) -> None:
        triangles = np.asarray(chunk, dtype=np.float32).reshape((-1, 3, 3))
        view = (triangles - center) @ rotation.T
        screen = np.empty_like(view)
        screen[:, :, 0] = (view[:, :, 0] - view_mid[0]) * scale + width * 0.5
        screen[:, :, 1] = height * 0.5 - (view[:, :, 1] - view_mid[1]) * scale
        screen[:, :, 2] = view[:, :, 2]
        normals = np.cross(view[:, 1] - view[:, 0], view[:, 2] - view[:, 0])
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals /= np.where(lengths == 0, 1.0, lengths)
        normals = np.where(normals[:, 2:3] >= 0, normals, -normals)
        corner_normals = np.repeat(normals[:, None, :], 3, axis=1)
        _rasterise_triangles(
            image,
            z_buffer,
            screen,
            corner_normals,
            shade,
            base_color,
            width,
            height,
        )

    chunk: list[tuple[float, ...]] = []
    sampled = 0
    for index, triangle in enumerate(_iter_stl_triangles(path)):
        if next_target is None:
            break
        if index != next_target:
            continue
        chunk.append(triangle)
        sampled += 1
        next_target = next(targets, None)
        if len(chunk) >= _RASTER_CHUNK_TRIANGLES:
            rasterise(chunk)
            chunk.clear()
    if chunk:
        rasterise(chunk)
    if sampled == 0 or not np.isfinite(z_buffer).any():
        return None

    alpha = np.where(np.isfinite(z_buffer), 255, 0).astype(np.uint8)
    rgba = np.dstack([image, alpha])
    output = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(output, format="PNG", optimize=True)
    return STLThumbnailResult(
        png=output.getvalue(),
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        triangle_count=triangle_count,
        sampled_triangles=sampled,
    )
