"""Bounded-memory STL thumbnail fallback used when full mesh loading is unsafe."""

from __future__ import annotations

import io
import math
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
# The fallback has a hard facet-work budget.  It does not rasterise every facet
# after the normal mesh cap: doing so turns a malicious/very dense STL into an
# unbounded CPU job.  The selected facets feed a coarse coverage accumulator,
# which closes the holes that the old uniform point sampling left behind.
_MAX_SAMPLED_TRIANGLES = 100_000
_COVERAGE_CHUNK_TRIANGLES = 2_048
_MAX_ASCII_LINE_BYTES = 64 * 1024
_FLOAT32_MAX = 3.4028234663852886e38
_MAX_RENDER_DIMENSION = 2048


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
            triangle = tuple(float(value) for value in values[3:12])
            # NaN/Inf coordinates can poison the bounds, camera matrix and
            # rasteriser.  Ignore those facets rather than allowing a malformed
            # file to turn into an unbounded/invalid NumPy operation.
            if all(_valid_coordinate(value) for value in triangle):
                yield triangle


def _iter_ascii_triangles(path: Path) -> Iterator[tuple[float, ...]]:
    vertices: list[float] = []
    with path.open("rb") as stream:
        while True:
            raw_line = stream.readline(_MAX_ASCII_LINE_BYTES + 1)
            if not raw_line:
                return
            if len(raw_line) > _MAX_ASCII_LINE_BYTES:
                # Discard the rest of a hostile line in bounded reads.  Do not
                # let ``for line in stream`` materialise an arbitrarily large
                # comment or vertex record supplied by an untrusted library.
                vertices.clear()
                while raw_line and not raw_line.endswith((b"\n", b"\r")):
                    raw_line = stream.readline(_MAX_ASCII_LINE_BYTES + 1)
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
                yield tuple(vertices)
                vertices.clear()


def _valid_coordinate(value: float) -> bool:
    """Return whether *value* is finite and representable in float32."""

    return math.isfinite(value) and abs(value) <= _FLOAT32_MAX


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
    max_triangles: int | None = None,
) -> STLThumbnailResult | None:
    """Stream twice and rasterise a bounded, spatially covered representation.

    The first pass obtains exact bounds.  The second pass examines at most the
    facet budget and accumulates each selected projection into a coarse coverage
    grid.  Facet normals are averaged per cell and the resulting coverage and
    shading grids are upscaled once; no selected facet reaches the expensive
    full-resolution triangle rasteriser.  This keeps CPU and memory bounded
    while the coverage mask closes the holes that uniform facet sampling used
    to expose on dense meshes (#67).
    """
    try:
        import numpy as np
        from PIL import Image

        from app.services.mesh_render import _select_view_rotation
    except ImportError:
        return None

    if not (1 <= width <= _MAX_RENDER_DIMENSION) or not (
        1 <= height <= _MAX_RENDER_DIMENSION
    ):
        return None

    scanned = _scan_bounds(path)
    if scanned is None:
        return None
    triangle_count, bounds_min, bounds_max = scanned
    work_budget = _MAX_SAMPLED_TRIANGLES
    if max_triangles is not None:
        work_budget = max(max_triangles, 1)
    sample_count = min(triangle_count, work_budget)
    # Midpoints of equal-width bins provide deterministic, bounded coverage
    # across the complete file.  Unlike the old implementation, selected facets
    # are aggregated spatially before any expensive rasterisation.
    targets = (
        ((sample_index * triangle_count + triangle_count // 2) // sample_count)
        for sample_index in range(sample_count)
    )
    next_target = next(targets, None)

    try:
        corners = np.asarray(
            list(product(*zip(bounds_min, bounds_max, strict=True))), dtype=np.float64
        )
        if not np.isfinite(corners).all():
            return None
        center = (np.asarray(bounds_min) + np.asarray(bounds_max)) * 0.5
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

    coverage_width = max(1, min(width, max(32, width // 4)))
    coverage_height = max(1, min(height, max(24, height // 4)))
    coverage_diff = np.zeros((coverage_height + 1, coverage_width + 1), dtype=np.int32)
    cell_count = coverage_width * coverage_height
    normal_sum = np.zeros((cell_count, 3), dtype=np.float64)
    normal_count = np.zeros(cell_count, dtype=np.int32)
    base_color = np.asarray([176, 190, 214], dtype=np.float32)
    light = np.asarray([-0.45, 0.6, 1.0], dtype=np.float32)
    light /= np.linalg.norm(light)

    def shade(normals):
        diffuse = np.clip(normals @ light, 0.0, 1.0)[:, None]
        return np.clip(0.32 + diffuse * 0.68, 0.0, 1.0)

    def accumulate_chunk(chunk: list[tuple[float, ...]]) -> None:
        """Aggregate one bounded facet chunk without per-facet raster calls."""

        triangles = np.asarray(chunk, dtype=np.float64).reshape((-1, 3, 3))
        view = (triangles - center) @ rotation.T
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

        normal = raw_normal / np.maximum(normal_length[:, None], 1e-12)
        normal = np.where(normal[:, 2:3] >= 0, normal, -normal)

        # Add projected triangle bounding boxes to a difference grid. Each facet
        # costs four scalar updates regardless of how large it is, so a malicious
        # giant triangle cannot expand a per-facet pixel array.
        coarse_x = screen[:, :, 0] / width * coverage_width
        coarse_y = screen[:, :, 1] / height * coverage_height
        x0 = np.clip(np.floor(coarse_x.min(axis=1)), 0, coverage_width - 1).astype(
            np.int64
        )
        x1 = np.clip(np.ceil(coarse_x.max(axis=1)) - 1, 0, coverage_width - 1).astype(
            np.int64
        )
        y0 = np.clip(np.floor(coarse_y.min(axis=1)), 0, coverage_height - 1).astype(
            np.int64
        )
        y1 = np.clip(np.ceil(coarse_y.max(axis=1)) - 1, 0, coverage_height - 1).astype(
            np.int64
        )
        valid &= (x1 >= x0) & (y1 >= y0)
        if not valid.any():
            return
        ids = np.flatnonzero(valid)
        np.add.at(coverage_diff, (y0[ids], x0[ids]), 1)
        np.add.at(coverage_diff, (y0[ids], x1[ids] + 1), -1)
        np.add.at(coverage_diff, (y1[ids] + 1, x0[ids]), -1)
        np.add.at(coverage_diff, (y1[ids] + 1, x1[ids] + 1), 1)

        centroid = screen[ids, :, :2].mean(axis=1)
        cell_x = np.clip(
            np.floor(centroid[:, 0] / width * coverage_width),
            0,
            coverage_width - 1,
        ).astype(np.int64)
        cell_y = np.clip(
            np.floor(centroid[:, 1] / height * coverage_height),
            0,
            coverage_height - 1,
        ).astype(np.int64)
        cells = cell_y * coverage_width + cell_x
        np.add.at(normal_sum, cells, normal[ids])
        np.add.at(normal_count, cells, 1)

    selected = 0
    sampled = 0
    chunk: list[tuple[float, ...]] = []
    for index, triangle in enumerate(_iter_stl_triangles(path)):
        if next_target is None:
            break
        if index != next_target:
            continue
        selected += 1
        sampled += 1
        next_target = next(targets, None)
        chunk.append(triangle)
        if len(chunk) >= _COVERAGE_CHUNK_TRIANGLES:
            accumulate_chunk(chunk)
            chunk.clear()
    if chunk:
        accumulate_chunk(chunk)

    if sampled == 0 or selected == 0:
        return None

    coverage = coverage_diff.cumsum(axis=0).cumsum(axis=1)[:-1, :-1] > 0
    if not coverage.any():
        return None
    normals = normal_sum / np.maximum(normal_count[:, None], 1)
    normal_lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals /= np.where(normal_lengths == 0, 1.0, normal_lengths)
    coarse_rgb = np.broadcast_to(base_color * 0.45, (cell_count, 3)).copy()
    has_normals = normal_count > 0
    coarse_rgb[has_normals] = base_color * shade(normals[has_normals])
    coarse_rgb = coarse_rgb.reshape((coverage_height, coverage_width, 3))
    image = np.asarray(
        Image.fromarray(
            np.clip(coarse_rgb, 0, 255).astype(np.uint8), mode="RGB"
        ).resize((width, height), Image.Resampling.BILINEAR),
        dtype=np.uint8,
    ).copy()
    alpha = np.asarray(
        Image.fromarray((coverage * 255).astype(np.uint8), mode="L").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.uint8,
    )
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
