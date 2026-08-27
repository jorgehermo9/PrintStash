"""Mesh-density cap that stops one dense lattice/gyroid file from OOM-killing the
process during a library scan (issue #24).

Loading + rasterising a mesh costs ~700 MB of peak RSS per million triangles,
and the cost is paid inside the trimesh scene loader — so the only safe defence is to
estimate the triangle count *before* loading and skip the monster. The file is
still indexed; a 3MF still yields its embedded slicer preview.
"""

from __future__ import annotations

import io
import math
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import _overlay
from app.services import mesh_processing
from tests.fixtures.three_mf_projects import build_3d_builder_component_project


@pytest.fixture(autouse=True)
def _static_cap_only():
    """Most tests here exercise the *static* triangle/byte caps, whose outcome
    must not depend on the CI host's RAM. Disable the RAM-aware cap by default;
    the dedicated RAM-cap tests re-enable it explicitly."""
    prev = _overlay.get("mesh_memory_budget_fraction", "__unset__")
    _overlay["mesh_memory_budget_fraction"] = 0
    yield
    if prev == "__unset__":
        _overlay.pop("mesh_memory_budget_fraction", None)
    else:
        _overlay["mesh_memory_budget_fraction"] = prev


def _write_binary_stl(path: Path, n_triangles: int) -> None:
    """A minimal but structurally valid binary STL with *n_triangles* facets."""
    with path.open("wb") as fh:
        fh.write(b"\x00" * 80)  # header
        fh.write(struct.pack("<I", n_triangles))
        fh.write(b"\x00" * (50 * n_triangles))  # 50 bytes per facet


def _write_renderable_binary_stl(path: Path, n_triangles: int) -> None:
    """Valid non-degenerate facets spread across the model bounds."""
    record = struct.Struct("<12fH")
    with path.open("wb") as fh:
        fh.write(b"fallback-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n_triangles))
        for index in range(n_triangles):
            x = float(index % 100)
            y = float((index // 100) % 100)
            z = float(index % 7) * 0.1
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    x,
                    y,
                    z,
                    x + 0.8,
                    y,
                    z,
                    x,
                    y + 0.8,
                    z,
                    0,
                )
            )


def _write_3d_builder_component_project(path: Path) -> None:
    """Write a standards-shaped 3MF with nested build/component placement."""
    path.write_bytes(build_3d_builder_component_project())


def _write_large_projected_binary_stl(path: Path, n_triangles: int) -> None:
    """Write many facets whose projected boxes deliberately cover the frame."""
    record = struct.Struct("<12fH")
    with path.open("wb") as fh:
        fh.write(b"large-projected".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n_triangles))
        for _ in range(n_triangles):
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    -100.0,
                    -100.0,
                    0.0,
                    100.0,
                    -100.0,
                    0.0,
                    0.0,
                    100.0,
                    0.0,
                    0,
                )
            )


def _write_annular_binary_stl(path: Path, segments: int = 96) -> None:
    """Write a deterministic thin ring whose projected center must stay empty."""
    record = struct.Struct("<12fH")
    outer, inner = 10.0, 4.0
    top, bottom = 0.5, -0.5

    def point(radius: float, index: int, z: float) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return (radius * math.cos(angle), radius * math.sin(angle), z)

    triangles: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(segments):
        next_index = (index + 1) % segments
        ot0, ot1 = point(outer, index, top), point(outer, next_index, top)
        it0, it1 = point(inner, index, top), point(inner, next_index, top)
        ob0, ob1 = point(outer, index, bottom), point(outer, next_index, bottom)
        ib0, ib1 = point(inner, index, bottom), point(inner, next_index, bottom)
        triangles.extend(
            [
                (ot0, ot1, it1),
                (ot0, it1, it0),
                (ob0, ib1, ob1),
                (ob0, ib0, ib1),
                (ot0, ob1, ot1),
                (ot0, ob0, ob1),
                (it0, it1, ib1),
                (it0, ib1, ib0),
            ]
        )

    with path.open("wb") as fh:
        fh.write(b"annular-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", len(triangles)))
        for first, second, third in triangles:
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    *first,
                    *second,
                    *third,
                    0,
                )
            )


def _write_microfaceted_surface_stl(
    path: Path, columns: int = 420, rows: int = 420
) -> int:
    """Write a connected, densely tessellated non-planar surface.

    The surface is intentionally much wider than an individual facet at
    thumbnail scale.  This is a small public stand-in for large microfaceted
    solids: a bounded facet sample contains real geometry, but the true
    projected area of each sampled facet is too small to hit a pixel reliably.
    """
    record = struct.Struct("<12fH")
    triangles = 2 * columns * rows

    def surface_z(x: float, y: float) -> float:
        return 4.0 * math.sin(x / 17.0) * math.cos(y / 19.0)

    with path.open("wb") as fh:
        fh.write(b"microfaceted-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", triangles))
        for row in range(rows):
            y0 = float(row)
            y1 = float(row + 1)
            for column in range(columns):
                x0 = float(column)
                x1 = float(column + 1)
                z00 = surface_z(x0, y0)
                z10 = surface_z(x1, y0)
                z11 = surface_z(x1, y1)
                z01 = surface_z(x0, y1)
                fh.write(
                    record.pack(
                        0.0,
                        0.0,
                        1.0,
                        x0,
                        y0,
                        z00,
                        x1,
                        y0,
                        z10,
                        x1,
                        y1,
                        z11,
                        0,
                    )
                )
                fh.write(
                    record.pack(
                        0.0,
                        0.0,
                        1.0,
                        x0,
                        y0,
                        z00,
                        x1,
                        y1,
                        z11,
                        x0,
                        y1,
                        z01,
                        0,
                    )
                )
    return triangles


def _write_microfaceted_annular_stl(
    path: Path, segments: int = 512, radial_steps: int = 8
) -> int:
    """Write a densely tessellated annulus with a real center hole."""
    record = struct.Struct("<12fH")
    outer, inner = 10.0, 4.0
    top, bottom = 0.5, -0.5
    triangles: list[tuple[tuple[float, float, float], ...]] = []

    def point(radius: float, index: int, z: float) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return (radius * math.cos(angle), radius * math.sin(angle), z)

    for index in range(segments):
        next_index = (index + 1) % segments
        for step in range(radial_steps):
            outer0 = outer - (outer - inner) * step / radial_steps
            outer1 = outer - (outer - inner) * (step + 1) / radial_steps
            ot0, ot1 = point(outer0, index, top), point(outer0, next_index, top)
            it0, it1 = point(outer1, index, top), point(outer1, next_index, top)
            ob0, ob1 = point(outer0, index, bottom), point(outer0, next_index, bottom)
            ib0, ib1 = point(outer1, index, bottom), point(outer1, next_index, bottom)
            triangles.extend(
                [
                    (ot0, ot1, it1),
                    (ot0, it1, it0),
                    (ob0, ib1, ob1),
                    (ob0, ib0, ib1),
                ]
            )
        ot0, ot1 = point(outer, index, top), point(outer, next_index, top)
        ob0, ob1 = point(outer, index, bottom), point(outer, next_index, bottom)
        it0, it1 = point(inner, index, top), point(inner, next_index, top)
        ib0, ib1 = point(inner, index, bottom), point(inner, next_index, bottom)
        triangles.extend(
            [
                (ot0, ob1, ot1),
                (ot0, ob0, ob1),
                (it0, it1, ib1),
                (it0, ib1, ib0),
            ]
        )

    with path.open("wb") as fh:
        fh.write(b"microfaceted-annulus".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", len(triangles)))
        for first, second, third in triangles:
            fh.write(record.pack(0.0, 0.0, 1.0, *first, *second, *third, 0))
    return len(triangles)


def _largest_component_fraction(mask: np.ndarray) -> float:
    visible = int(mask.sum())
    if visible == 0:
        return 0.0
    visited = np.zeros(mask.shape, dtype=bool)
    largest = 0
    height, width = mask.shape
    for y, x in zip(*np.where(mask), strict=True):
        if visited[y, x]:
            continue
        visited[y, x] = True
        stack = [(int(y), int(x))]
        size = 0
        while stack:
            current_y, current_x = stack.pop()
            size += 1
            for delta_y in (-1, 0, 1):
                for delta_x in (-1, 0, 1):
                    next_y, next_x = current_y + delta_y, current_x + delta_x
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        stack.append((next_y, next_x))
        largest = max(largest, size)
    return largest / visible


def _valid_preview_png(color: tuple[int, int, int] = (16, 192, 224)) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="PNG")
    return output.getvalue()


def _fake_mesh(num_faces: int):
    return SimpleNamespace(
        vertices=np.zeros((3, 3), dtype=np.float64),
        bounds=np.array([[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]]),
        faces=np.zeros((num_faces, 3), dtype=np.int64),
        volume=42.0,
    )


def test_binary_stl_triangle_count_is_exact(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 1234)
    assert mesh_processing._estimate_triangle_count(p) == 1234


def test_3mf_triangle_count_from_uncompressed_xml(tmp_path: Path) -> None:
    p = tmp_path / "dense.3mf"
    model_xml = b"<triangle/>" * 10_000  # 110_000 bytes of "mesh"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml)
    # ~70 bytes per triangle proxy.
    assert mesh_processing._estimate_triangle_count(p) == len(model_xml) // 70


def test_over_cap_mesh_is_never_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)  # well over the cap

    def _boom(_path):  # pragma: no cover - must never run
        raise AssertionError("over-cap mesh must not be loaded into trimesh")

    monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)

    geometry, thumb = mesh_processing.analyze_mesh(p)

    # Indexed, but with no geometry/thumbnail — and crucially, no load attempt.
    assert geometry["triangle_count"] is None
    assert thumb is None


def test_over_cap_valid_stl_uses_streaming_thumbnail_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
    path = tmp_path / "issue-67-over-limit.stl"
    _write_renderable_binary_stl(path, 1_001)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("fallback must not load through trimesh")
        ),
    )

    geometry, thumb = mesh_processing.analyze_mesh(path)

    assert isinstance(thumb, mesh_processing.FallbackThumbnail)
    assert thumb.startswith(mesh_processing._PNG_MAGIC)
    assert thumb.complete is True
    assert geometry["triangle_count"] == 1_001
    assert geometry["bbox_x_mm"] == 99.8
    assert geometry["bbox_y_mm"] == 10.8


def test_stl_fallback_uniformly_caps_sample_to_100k(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "sampled.stl"
    _write_renderable_binary_stl(path, 101)
    result = stl_fallback.render_stl_thumbnail(path, max_triangles=10)

    assert result is not None
    assert result.triangle_count == 101
    assert result.sampled_triangles == 10
    assert result.parsed_triangles == 10
    assert result.scanned_bytes <= 84 + (10 * 50)


def test_stl_fallback_dense_fixture_has_a_coherent_silhouette(
    tmp_path: Path,
) -> None:
    """The production fallback must cover a dense mesh instead of drawing points.

    An icosphere with 327,680 deterministic facets is deliberately above the
    100k work budget.  Aggregating its bounded sample into a coarse coverage
    grid should fill the projected silhouette, retain contrast, and leave a safe
    margin around the object.  The assertions are image properties rather than
    a pixel snapshot, so they tolerate renderer/library updates.
    """
    import trimesh
    from PIL import Image

    from app.services import stl_fallback

    path = tmp_path / "issue-67-dense-figure.stl"
    mesh = trimesh.creation.icosphere(subdivisions=7, radius=10.0)
    path.write_bytes(mesh.export(file_type="stl"))

    result = stl_fallback.render_stl_thumbnail(path, width=96, height=72)

    assert result is not None
    assert result.triangle_count == len(mesh.faces)
    assert result.sampled_triangles == stl_fallback._MAX_SAMPLED_TRIANGLES

    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    visible = pixels[:, :, 3] > 20
    ys, xs = np.where(visible)
    assert visible.mean() > 0.10  # visible silhouette, not a sparse point cloud
    assert np.ptp(xs) + 1 > pixels.shape[1] * 0.25
    assert np.ptp(ys) + 1 > pixels.shape[0] * 0.25
    assert xs.min() > 2 and xs.max() < pixels.shape[1] - 3
    assert ys.min() > 2 and ys.max() < pixels.shape[0] - 3
    bbox_area = (np.ptp(xs) + 1) * (np.ptp(ys) + 1)
    assert float(visible.sum() / bbox_area) > 0.45

    shaded = pixels[:, :, :3][visible]
    assert float(shaded.std()) > 10.0  # lighting still provides useful contrast


def test_stl_fallback_microfacets_keep_connected_surface_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    """Sub-pixel sampled facets must still produce a connected preview."""
    from PIL import Image

    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 10_000)
    path = tmp_path / "issue-67-connected-microfacets.stl"
    triangle_count = _write_microfaceted_surface_stl(path)
    result = stl_fallback.render_stl_thumbnail(path, width=96, height=72)

    assert result is not None
    assert result.triangle_count == triangle_count
    assert result.sampled_triangles == stl_fallback._MAX_SAMPLED_TRIANGLES
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    visible = pixels[:, :, 3] > 20
    ys, xs = np.where(visible)
    assert visible.mean() >= 0.08
    assert np.ptp(xs) + 1 > pixels.shape[1] * 0.5
    assert np.ptp(ys) + 1 > pixels.shape[0] * 0.5
    bbox_area = (np.ptp(xs) + 1) * (np.ptp(ys) + 1)
    assert float(visible.sum() / bbox_area) >= 0.65


def test_stl_fallback_work_budget_is_observable(tmp_path: Path, monkeypatch) -> None:
    from app.services import mesh_render, stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 32)
    original_rasterise = mesh_render._rasterise_triangles
    calls: list[int] = []

    def bounded_rasterise(*args, **kwargs):
        calls.append(int(args[2].shape[0]))
        return original_rasterise(*args, **kwargs)

    monkeypatch.setattr(mesh_render, "_rasterise_triangles", bounded_rasterise)
    path = tmp_path / "budget.stl"
    _write_annular_binary_stl(path)

    result = stl_fallback.render_stl_thumbnail(
        path, width=64, height=48, max_triangles=1_000
    )

    assert result is not None
    assert result.triangle_count == 768
    assert result.sampled_triangles == 32
    assert result.parsed_triangles == 32
    assert result.scanned_bytes <= 84 + (32 * 50)
    # Incomplete samples retain all source triangles and add one centroid-splat
    # triangle per source facet. Both paths stay bounded by the sample cap.
    assert calls and 32 <= sum(calls) <= 2 * 32


def test_stl_fallback_global_candidate_budget_for_large_facets(tmp_path: Path) -> None:
    from app.services import stl_fallback

    path = tmp_path / "large-projected.stl"
    _write_large_projected_binary_stl(path, 100_001)

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.sampled_triangles == 100_000
    assert result.raster_candidates == stl_fallback._MAX_COVERAGE_CANDIDATES


def test_stl_fallback_rasterises_real_area_and_preserves_hole(tmp_path: Path) -> None:
    from PIL import Image

    from app.services import stl_fallback

    path = tmp_path / "annular-hole.stl"
    _write_annular_binary_stl(path)
    result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

    assert result is not None
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    alpha = pixels[:, :, 3]
    center = alpha[alpha.shape[0] // 2, alpha.shape[1] // 2]
    assert center < 32
    assert float((alpha > 200).mean()) > 0.15
    assert float(pixels[:, :, :3][alpha > 200].std()) > 5.0


def test_incomplete_annular_sample_still_preserves_hole(
    tmp_path: Path, monkeypatch
) -> None:
    from PIL import Image

    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 32)
    path = tmp_path / "incomplete-annular-hole.stl"
    _write_annular_binary_stl(path)
    result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

    assert result is not None
    assert result.complete is False
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    assert pixels[pixels.shape[0] // 2, pixels.shape[1] // 2, 3] < 32


def test_dense_microfaceted_annulus_keeps_hole_and_connected_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    from PIL import Image

    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 768)
    path = tmp_path / "dense-microfaceted-annulus.stl"
    triangle_count = _write_microfaceted_annular_stl(path)
    result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

    assert result is not None
    assert result.triangle_count == triangle_count
    assert result.sampled_triangles == 768
    assert result.raster_candidates <= stl_fallback._MAX_COVERAGE_CANDIDATES
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    alpha = pixels[:, :, 3]
    assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] < 32
    visible = alpha > 20
    assert visible.mean() >= 0.08
    assert _largest_component_fraction(visible) >= 0.70


def test_ascii_fallback_discards_hostile_line_and_recovers(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "hostile-line.stl"
    valid = """facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
"""
    path.write_text(
        "solid hostile\n"
        + "comment "
        + ("x" * (stl_fallback._MAX_ASCII_LINE_BYTES + 10_000))
        + "\n"
        + valid
        + "endsolid hostile\n",
        encoding="ascii",
    )

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.triangle_count == 1
    assert result.parsed_triangles == 1
    assert result.scanned_bytes <= stl_fallback._MAX_ASCII_BYTES
    assert result.complete is False


def test_ascii_fallback_caps_total_facets_and_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 4)
    path = tmp_path / "ascii-budget.stl"
    facets = []
    for index in range(10):
        facets.append(
            "facet normal 0 0 1\n"
            "outer loop\n"
            f"vertex {index} 0 0\n"
            f"vertex {index + 1} 0 0\n"
            f"vertex {index} 1 0\n"
            "endloop\n"
            "endfacet\n"
        )
    path.write_text("solid budget\n" + "".join(facets) + "endsolid budget\n")

    result = stl_fallback.render_stl_thumbnail(
        path, width=64, height=48, max_triangles=1_000
    )

    assert result is not None
    assert result.triangle_count == 4
    assert result.sampled_triangles == 4
    assert result.parsed_triangles == 4
    assert result.scanned_bytes <= stl_fallback._MAX_ASCII_BYTES


@pytest.mark.parametrize(
    "pending_vertices",
    ["vertex 2 2 2\n", "vertex 2 2 2\nvertex 3 3 3\n"],
)
def test_ascii_pending_vertices_at_eof_are_incomplete(
    tmp_path: Path, monkeypatch, pending_vertices: str
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-pending-eof.stl"
    path.write_text(
        "solid pending\n"
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 0 0 0\n"
        "vertex 1 0 0\n"
        "vertex 0 1 0\n"
        "endloop\n"
        "endfacet\n" + pending_vertices
    )

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.parsed_triangles == 1
    assert result.complete is False

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)
    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is False
    assert geometry["triangle_count"] is None
    assert geometry["bbox_x_mm"] is None


def test_ascii_fallback_marks_truncated_metadata_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_ASCII_BYTES", 500)
    path = tmp_path / "ascii-truncated.stl"
    facet = (
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 0 0 0\n"
        "vertex 1 0 0\n"
        "vertex 0 1 0\n"
        "endloop\n"
        "endfacet\n"
    )
    path.write_text("solid truncated\n" + (facet * 20) + "endsolid truncated\n")

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.complete is False
    assert result.scanned_bytes <= 500

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)
    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is False
    assert geometry["triangle_count"] is None
    assert geometry["bbox_x_mm"] is None


def test_ascii_fallback_rejects_float32_overflow(tmp_path: Path) -> None:
    from app.services import stl_fallback

    path = tmp_path / "float-overflow.stl"
    path.write_text(
        "solid overflow\n"
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 1e308 0 0\n"
        "vertex 0 1e308 0\n"
        "vertex 0 0 1e308\n"
        "endloop\n"
        "endfacet\n"
        "endsolid overflow\n",
        encoding="ascii",
    )

    assert stl_fallback.render_stl_thumbnail(path, width=64, height=48) is None


def test_stl_fallback_skips_nonfinite_facets(tmp_path: Path) -> None:
    from app.services import stl_fallback

    path = tmp_path / "malformed-coordinates.stl"
    record = struct.Struct("<12fH")
    with path.open("wb") as fh:
        fh.write(b"malformed".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", 2))
        fh.write(
            record.pack(
                0.0,
                0.0,
                1.0,
                float("nan"),
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0,
            )
        )
        fh.write(
            record.pack(
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0,
            )
        )

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.triangle_count == 2
    assert result.sampled_triangles == 1


def test_stl_fallback_binary_helpers_bound_reads_and_reject_truncation(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "helpers.stl"
    _write_renderable_binary_stl(path, 2)

    assert stl_fallback._binary_stl_info(path) == (2, path.stat().st_size)
    assert stl_fallback._is_binary_stl(path)
    records = list(stl_fallback._iter_binary_triangles(path, max_triangles=1))
    assert len(records) == 1
    assert len(records[0]) == 9

    short_header = tmp_path / "short-header.stl"
    short_header.write_bytes(b"short")
    assert stl_fallback._binary_stl_info(short_header) is None
    assert list(stl_fallback._iter_binary_triangles(short_header)) == []

    truncated = tmp_path / "truncated.stl"
    truncated.write_bytes(b"x" * 80 + struct.pack("<I", 1) + b"x")
    assert list(stl_fallback._iter_binary_triangles(truncated)) == []
    assert stl_fallback._read_binary_samples(truncated, 1) is None


def test_stl_fallback_binary_helpers_fail_closed_on_io_errors(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "io-error.stl"
    _write_renderable_binary_stl(path, 1)
    original_open = Path.open

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert stl_fallback._binary_stl_info(path) is None
    assert list(stl_fallback._iter_binary_triangles(path)) == []
    assert stl_fallback._read_binary_samples(path, 1) is None
    monkeypatch.setattr(Path, "open", original_open)

    def fail_stat(_path: Path):
        raise OSError("missing")

    monkeypatch.setattr(Path, "stat", fail_stat)
    assert stl_fallback._read_samples(path, 1) is None


def test_stl_fallback_binary_sampler_handles_short_records_and_io_failures(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "sampler-short-record.stl"
    _write_renderable_binary_stl(path, 1)
    info = (1, path.stat().st_size)
    short = tmp_path / "sampler-truncated-record.stl"
    short.write_bytes(b"x" * 84 + b"x")
    assert stl_fallback._read_binary_samples(short, 1, info=(1, 85)) is None

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert stl_fallback._read_binary_samples(path, 1, info=info) is None


def test_stl_fallback_ascii_iterator_recovers_after_bad_and_oversized_lines(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-iterator.stl"
    valid = b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
    path.write_bytes(
        b"solid iterator\n"
        + b"vertex not-a-number 0 0\n"
        + b"vertex nan 0 0\n"
        + b"comment "
        + b"x" * (stl_fallback._MAX_ASCII_LINE_BYTES + 10)
        + b"\n"
        + valid
        + b"endsolid iterator\n"
    )

    records = list(stl_fallback._iter_ascii_triangles(path))
    assert records == [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]
    assert list(stl_fallback._iter_ascii_triangles(path, max_triangles=0)) == []
    assert list(stl_fallback._iter_ascii_triangles(path, max_lines=0)) == []


def test_stl_fallback_ascii_helpers_fail_closed_on_io_errors(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-io-error.stl"
    path.write_text("solid empty\n", encoding="ascii")

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert list(stl_fallback._iter_ascii_triangles(path)) == []
    assert stl_fallback._read_ascii_samples(path, 1) is None


def test_stl_fallback_ascii_samples_mark_invalid_source_and_truncation(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-invalid-source.stl"
    path.write_text(
        "solid invalid\nvertex broken 0 0\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n",
        encoding="ascii",
    )
    sampled = stl_fallback._read_ascii_samples(path, 4)
    assert sampled is not None
    assert sampled.parsed_triangles == 1
    assert sampled.complete is False

    empty = tmp_path / "ascii-empty.stl"
    empty.write_text("solid empty\nvertex nan 0 0\n", encoding="ascii")
    assert stl_fallback._read_ascii_samples(empty, 1) is None


def test_stl_fallback_dispatches_binary_and_ascii_iterators(tmp_path: Path) -> None:
    from app.services import stl_fallback

    binary = tmp_path / "dispatch.stl"
    _write_renderable_binary_stl(binary, 1)
    ascii_path = tmp_path / "dispatch-ascii.stl"
    ascii_path.write_text(
        "solid dispatch\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendsolid dispatch\n",
        encoding="ascii",
    )
    assert len(list(stl_fallback._iter_stl_triangles(binary))) == 1
    assert len(list(stl_fallback._iter_stl_triangles(ascii_path))) == 1
    assert stl_fallback._read_binary_samples(binary, 0) is None


def test_stl_fallback_render_rejects_bad_dimensions_and_numeric_states(
    tmp_path: Path, monkeypatch
) -> None:
    from array import array

    from app.services import mesh_render, stl_fallback

    path = tmp_path / "render-defensive.stl"
    _write_renderable_binary_stl(path, 1)
    assert stl_fallback.render_stl_thumbnail(path, width=0) is None
    assert (
        stl_fallback.render_stl_thumbnail(
            path, height=stl_fallback._MAX_RENDER_DIMENSION + 1
        )
        is None
    )

    sampled = stl_fallback._SampledSTL(
        coordinates=array("f", [float("nan")] * 9),
        triangle_count=1,
        sampled_triangles=1,
        bounds_min=(0.0, 0.0, 0.0),
        bounds_max=(1.0, 1.0, 1.0),
        scanned_bytes=1,
        parsed_triangles=1,
        complete=True,
    )
    monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.coordinates = array("f", [0.0] * 9)
    sampled.bounds_min = (1e308, 1e308, 1e308)
    sampled.bounds_max = (1e308, 1e308, 1e308)
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (0.0, 0.0, 0.0)
    sampled.bounds_max = (1.0, 1.0, 1.0)
    monkeypatch.setattr(
        mesh_render,
        "_select_view_rotation",
        lambda *_args: (_ for _ in ()).throw(ValueError("rotation")),
    )
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (-1e308, -1e308, -1e308)
    sampled.bounds_max = (1e308, 1e308, 1e308)
    monkeypatch.setattr(mesh_render, "_select_view_rotation", lambda *_args: np.eye(3))
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (0.0, 0.0, 0.0)
    sampled.bounds_max = (1.0, 1.0, 1.0)
    monkeypatch.setattr(
        mesh_render,
        "_select_view_rotation",
        lambda *_args: np.full((3, 3), np.nan),
    )
    assert stl_fallback.render_stl_thumbnail(path) is None


def test_stl_fallback_returns_none_when_optional_render_dependencies_are_missing(
    tmp_path: Path, monkeypatch
) -> None:
    import builtins

    from app.services import stl_fallback

    path = tmp_path / "missing-dependency.stl"
    _write_renderable_binary_stl(path, 1)
    original_import = builtins.__import__

    def missing_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("numpy unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_numpy)
    assert stl_fallback.render_stl_thumbnail(path) is None


def test_over_cap_3mf_still_gets_embedded_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    png = _valid_preview_png()
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "3D/3dmodel.model", b"<triangle/>" * 100_000
        )  # ~157k tris, over cap
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] is None  # mesh skipped
    assert thumb == png  # but the cheap embedded preview is still used


def test_valid_embedded_3mf_preview_precedes_mesh_render(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid slicer preview is preferred even when mesh loading is safe."""
    png = _valid_preview_png((220, 40, 120))
    p = tmp_path / "preview-first.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render,
        "render_mesh_thumbnail",
        lambda *args, **kwargs: b"RENDERED-MESH",
    )

    _geometry, thumb = mesh_processing.analyze_mesh(p)
    assert thumb == png


def test_3mf_component_scene_transforms_are_baked_into_stl(tmp_path: Path) -> None:
    path = tmp_path / "3d-builder-component.3mf"
    _write_3d_builder_component_project(path)

    converted = mesh_processing.to_stl_bytes(path)

    assert converted is not None and len(converted) > 84
    import trimesh

    mesh = trimesh.load_mesh(io.BytesIO(converted), file_type="stl", process=False)
    np.testing.assert_allclose(
        mesh.bounds,
        np.asarray([[110.0, 220.0, 330.0], [112.0, 223.0, 334.0]]),
        atol=1e-5,
    )


def test_malformed_3mf_conversion_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "malformed.3mf"
    path.write_bytes(b"not a zip archive")

    assert mesh_processing.to_stl_bytes(path) is None


def test_post_load_backstop_skips_render_when_estimate_missed(
    tmp_path: Path, monkeypatch
) -> None:
    # A format the estimator can't size up (returns None) but whose loaded mesh
    # is over budget: keep the cheap geometry, skip the expensive render.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 10)
    p = tmp_path / "model.obj"
    p.write_text("# obj")

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(
        mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=99)
    )
    monkeypatch.setattr(
        mesh_processing.mesh_render,
        "render_mesh_thumbnail",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not render")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] == 99  # cheap geometry kept
    assert thumb is None  # render skipped


def test_under_cap_mesh_renders_normally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)

    monkeypatch.setattr(
        mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=500)
    )
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNGDATA"
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] == 500
    assert thumb == b"PNGDATA"


# ---------------------------------------------------------------------------
# RAM-aware cap: the effective triangle ceiling scales down with available
# memory so a small host skips meshes a large host renders (issue #29).
# ---------------------------------------------------------------------------


def test_detect_memory_limit_is_positive_on_linux() -> None:
    limit = mesh_processing._detect_memory_limit_bytes()
    # On Linux CI this reads /proc/meminfo or a cgroup; elsewhere it may be None.
    assert limit is None or limit > 0


def test_ram_cap_disabled_when_fraction_zero(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
    assert mesh_processing._ram_triangle_cap(".stl") is None


def test_ram_cap_scales_with_memory_and_format(monkeypatch) -> None:
    # Pin a 4 GB ceiling so the result is host-independent.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    stl_cap = mesh_processing._ram_triangle_cap(".stl")
    mf_cap = mesh_processing._ram_triangle_cap(".3mf")
    # 2 GB budget / per-triangle cost.
    assert stl_cap == int(
        2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE
    )
    assert mf_cap == int(2 * 1024**3 / mesh_processing._PEAK_BYTES_PER_TRIANGLE[".3mf"])
    # 3MF is the heavier format, so its cap is the lower of the two.
    assert mf_cap < stl_cap


def test_ram_cap_skips_mesh_a_big_host_would_render(
    tmp_path: Path, monkeypatch
) -> None:
    # Static ceiling is generous (5M), but a 2 GB host can't afford this mesh.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 5_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 2 * 1024**3)
    p = tmp_path / "mid.stl"
    # ~700k triangles: under the 5M static cap, but over the ~480k RAM cap @ 2 GB.
    _write_binary_stl(p, 700_000)
    assert mesh_processing._ram_triangle_cap(".stl") < 700_000

    def _boom(_path):  # pragma: no cover
        raise AssertionError("RAM-capped mesh must not load")

    monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


def test_static_cap_still_applies_on_a_huge_ram_host(
    tmp_path: Path, monkeypatch
) -> None:
    # A 256 GB host: the RAM cap is enormous, so the static ceiling is what binds.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 256 * 1024**3)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(
            AssertionError("over static cap must not load")
        ),
    )
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


# ---------------------------------------------------------------------------
# Per-file memory reclamation: a loaded mesh's arrays are freed and returned to
# the OS between files so a long scan's RSS doesn't only ever climb (issue #29).
# ---------------------------------------------------------------------------


def test_loaded_mesh_triggers_memory_reclaim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)

    calls = {"n": 0}
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
    )
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )

    mesh_processing.analyze_mesh(p)
    assert calls["n"] == 1  # freed exactly once, after the mesh was used


def test_skipped_mesh_does_not_reclaim(tmp_path: Path, monkeypatch) -> None:
    # No mesh was loaded (over cap), so there's nothing to free — and we don't pay
    # gc.collect()/malloc_trim for a file we never touched.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)

    calls = {"n": 0}
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    mesh_processing.analyze_mesh(p)
    assert calls["n"] == 0


def test_reclaim_memory_is_safe_to_call() -> None:
    # Must never raise, regardless of libc/platform — it's best-effort cleanup.
    mesh_processing._reclaim_memory()


# ---------------------------------------------------------------------------
# Raw byte-size guard: the format-blind backstop for files the triangle
# estimate can't size up (issue #29 — a ~900 MB 3MF that OOM-killed the scan).
# ---------------------------------------------------------------------------


def test_oversize_file_is_never_loaded(tmp_path: Path, monkeypatch) -> None:
    # Triangle cap is generous so it can't be what trips the guard; the file is
    # only ~2 MB of facets (well under it). The 1 MB *size* cap must still skip
    # the load — this is the path that protects against an estimator that comes
    # up empty on a huge file.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    p = tmp_path / "big.stl"
    _write_binary_stl(p, 42_000)  # ~2 MB on disk
    assert p.stat().st_size > 1024 * 1024

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("oversize file must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb is None


def test_oversize_3mf_still_gets_embedded_preview(tmp_path: Path, monkeypatch) -> None:
    # A 3MF over the byte cap is never decompressed into trimesh, but the cheap
    # embedded slicer preview (read straight from the zip) still stands in.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    png = _valid_preview_png()
    p = tmp_path / "big.3mf"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 200_000)  # ~2 MB stored
        zf.writestr("Metadata/thumbnail.png", png)
    assert p.stat().st_size > 1024 * 1024

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("oversize 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb == png


def test_size_guard_disabled_when_zero(tmp_path: Path, monkeypatch) -> None:
    # mesh_max_load_mb = 0 turns the byte cap off; a big-but-sparse-triangle file
    # then loads normally (only the triangle cap still applies).
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    p = tmp_path / "big.stl"
    _write_binary_stl(p, 42_000)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(42_000))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] == 42_000
    assert thumb == b"PNG"


def test_3mf_without_model_part_falls_back_to_total_uncompressed_size(
    tmp_path: Path,
) -> None:
    # No ".model" entry: the estimator must not return None (which would let the
    # archive load blind). It falls back to the total uncompressed payload as a
    # conservative upper bound (issue #29).
    p = tmp_path / "weird.3mf"
    payload = b"x" * 700_000
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/mesh.bin", payload)
    est = mesh_processing._estimate_triangle_count(p)
    assert est == len(payload) // 70


# ---------------------------------------------------------------------------
# Estimator: binary-vs-ASCII STL disambiguation (the dangerous direction).
# ---------------------------------------------------------------------------


def test_binary_stl_with_trailing_bytes_is_not_underestimated(tmp_path: Path) -> None:
    # Some exporters append metadata after the facet block, so the exact
    # 84 + 50*N size check fails. The old code fell back to the ASCII estimate
    # (size // 250), underestimating a binary file ~5x and letting an over-cap
    # mesh slip through to an OOM load. The body-size estimate must stay a safe
    # upper bound on the real triangle count.
    p = tmp_path / "trailing.stl"
    n = 100_000
    _write_binary_stl(p, n)
    with p.open("ab") as fh:
        fh.write(b"exported by SomeSlicer\x00\x01\x02" * 50)  # trailing junk

    est = mesh_processing._estimate_triangle_count(p)
    assert est is not None
    assert est >= n  # never below the true count (the OOM-unsafe direction)
    # And nowhere near the 5x-low ASCII misread.
    assert est < n * 2


def test_ascii_stl_is_detected_and_estimated_by_text_density(tmp_path: Path) -> None:
    facet = (
        b"  facet normal 0 0 1\n"
        b"    outer loop\n"
        b"      vertex 0 0 0\n"
        b"      vertex 1 0 0\n"
        b"      vertex 0 1 0\n"
        b"    endloop\n"
        b"  endfacet\n"
    )
    p = tmp_path / "ascii.stl"
    p.write_bytes(b"solid mymesh\n" + facet * 300 + b"endsolid mymesh\n")

    est = mesh_processing._estimate_triangle_count(p)
    # ASCII estimate is size // 250; the file holds 300 real facets, and the
    # estimate should land in the same order of magnitude (not the 5x-too-low
    # binary misread of size // 50-equivalents).
    assert est == p.stat().st_size // 250
    assert est > 0


def test_binary_stl_header_starting_with_solid_is_not_misread_as_ascii(
    tmp_path: Path,
) -> None:
    # The classic STL trap: a binary STL whose 80-byte header text starts with
    # "solid". The NUL bytes in the binary body must keep it on the binary path.
    p = tmp_path / "trap.stl"
    n = 60_000
    with p.open("wb") as fh:
        fh.write(b"solid exported-by-tool".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n))
        fh.write(b"\x00" * (50 * n))
    with p.open("ab") as fh:
        fh.write(b"trailer")  # break the exact size match

    est = mesh_processing._estimate_triangle_count(p)
    assert est is not None
    assert est >= n  # treated as binary, not the 5x-low ASCII estimate


# ---------------------------------------------------------------------------
# Estimator: PLY face count from the header (no body parse).
# ---------------------------------------------------------------------------


def test_ply_face_count_from_header(tmp_path: Path) -> None:
    p = tmp_path / "scan.ply"
    header = (
        b"ply\n"
        b"format binary_little_endian 1.0\n"
        b"element vertex 8\n"
        b"property float x\n"
        b"property float y\n"
        b"property float z\n"
        b"element face 1234567\n"
        b"property list uchar int vertex_indices\n"
        b"end_header\n"
    )
    # Body is intentionally tiny/garbage — the estimate must come from the header
    # alone, never from loading the (declared-huge) body.
    p.write_bytes(header + b"\x00" * 32)

    assert mesh_processing._estimate_triangle_count(p) == 1234567


def test_ply_without_face_element_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "points.ply"
    p.write_bytes(
        b"ply\nformat ascii 1.0\nelement vertex 3\n"
        b"property float x\nend_header\n0 0 0\n"
    )
    assert mesh_processing._estimate_triangle_count(p) is None


def test_over_cap_ply_skips_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.ply"
    p.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement face 999999\nend_header\n"
    )
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap PLY must not load")),
    )

    geometry = mesh_processing.extract_geometry(p)
    assert geometry["triangle_count"] is None


# ---------------------------------------------------------------------------
# The cap is enforced on every entry point, not just analyze_mesh.
# ---------------------------------------------------------------------------


def test_extract_geometry_respects_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


def test_render_thumbnail_respects_cap_and_falls_back_to_embedded(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    png = _valid_preview_png()
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
        zf.writestr("Metadata/thumbnail.png", png)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    assert mesh_processing.render_thumbnail(p) == png


def test_to_stl_bytes_refuses_over_cap_mesh(tmp_path: Path, monkeypatch) -> None:
    # A download-as-STL click on a monster 3MF/OBJ must not run an unbounded
    # trimesh.load_mesh (which would OOM the process for every user). Refuse cleanly.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_passes_through_raw_stl(tmp_path: Path) -> None:
    # An STL is returned byte-for-byte without any load, so the cap never applies
    # (no conversion, no memory blow-up) even for a large file.
    p = tmp_path / "raw.stl"
    _write_binary_stl(p, 10)
    assert mesh_processing.to_stl_bytes(p) == p.read_bytes()


# ---------------------------------------------------------------------------
# Estimator: OBJ face-directive count (a first-class type that was unguarded).
# ---------------------------------------------------------------------------


def _write_obj(path: Path, tri_faces: int, *, quads: int = 0) -> None:
    lines = [b"# comment\n", b"o mesh\n", b"v 0 0 0\n", b"vn 0 0 1\n"]
    lines += [b"f 1//1 2//1 3//1\n"] * tri_faces
    lines += [b"f 1 2 3 4\n"] * quads  # quad = 2 triangles after fan
    path.write_bytes(b"".join(lines))


def test_obj_triangle_count_from_face_directives(tmp_path: Path) -> None:
    p = tmp_path / "mesh.obj"
    _write_obj(p, tri_faces=300)
    # 300 triangular faces -> 300 triangles (exact for tris).
    assert mesh_processing._estimate_triangle_count(p) == 300


def test_obj_ngon_faces_count_conservatively(tmp_path: Path) -> None:
    p = tmp_path / "quads.obj"
    _write_obj(p, tri_faces=10, quads=5)  # 10 + 5*(4-2) = 20 triangles
    assert mesh_processing._estimate_triangle_count(p) == 20


def test_obj_without_faces_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "points.obj"
    p.write_bytes(b"v 0 0 0\nv 1 0 0\nvn 0 0 1\n")
    assert mesh_processing._estimate_triangle_count(p) is None


# ---------------------------------------------------------------------------
# Concurrency-aware RAM budget: the per-job triangle cap divides by
# VAULT_MAX_RENDER_JOBS, and a semaphore caps how many renders run at once so a
# bulk upload's background tasks can't collectively OOM the box (issue #29).
# ---------------------------------------------------------------------------


def test_render_jobs_limit_floors_at_one(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "max_render_jobs", 0)
    assert mesh_processing._render_jobs_limit() == 1
    monkeypatch.setitem(_overlay, "max_render_jobs", -5)
    assert mesh_processing._render_jobs_limit() == 1


def test_ram_cap_divides_budget_by_max_render_jobs(monkeypatch) -> None:
    # Same RAM, same fraction — doubling the concurrent-job count halves the
    # per-job triangle cap.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

    monkeypatch.setitem(_overlay, "max_render_jobs", 1)
    one = mesh_processing._ram_triangle_cap(".stl")
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    two = mesh_processing._ram_triangle_cap(".stl")

    assert one == int(2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE)
    assert two == one // 2


def test_render_semaphore_caps_concurrent_renders(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    # Drop any cached semaphore built at a different limit by an earlier test.
    monkeypatch.setattr(mesh_processing, "_RENDER_SEMAPHORE", None)

    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))

    state = {"current": 0, "peak": 0}
    lock = threading.Lock()

    def _slow_render(*_a, **_k):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.05)  # hold the slot so overlap is observable
        with lock:
            state["current"] -= 1
        return b"PNG"

    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", _slow_render
    )

    threads = [
        threading.Thread(target=lambda: mesh_processing.analyze_mesh(p))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["peak"] >= 1  # work really ran
    assert state["peak"] <= 2  # never more than VAULT_MAX_RENDER_JOBS at once


# ---------------------------------------------------------------------------
# Large-3MF embedded-preview preference: a 3MF over the adaptive cap uses its
# embedded slicer preview without ever decompressing/parsing the mesh — gated by
# VAULT_USE_EMBEDDED_3MF_PREVIEW_FOR_LARGE_FILES (issue #29).
# ---------------------------------------------------------------------------


def _over_cap_3mf_with_preview(tmp_path: Path) -> tuple[Path, bytes]:
    png = _valid_preview_png((240, 128, 32))
    p = tmp_path / "big.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # ~157k tris
        zf.writestr("Metadata/thumbnail.png", png)
    return p, png


def test_large_3mf_uses_embedded_preview_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)  # 3MF is over cap
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", True)
    p, png = _over_cap_3mf_with_preview(tmp_path)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None  # never loaded
    assert thumb == png  # embedded preview used instead


def test_large_3mf_skips_embedded_preview_when_flag_off(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
    p, _png = _over_cap_3mf_with_preview(tmp_path)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb is None  # flag off → no embedded fallback for the over-cap file


def test_over_cap_obj_skips_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.obj"
    _write_obj(p, tri_faces=5000)  # well over the cap
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap OBJ must not load")),
    )

    assert mesh_processing.extract_geometry(p)["triangle_count"] is None
    assert mesh_processing.render_thumbnail(p) is None
    assert mesh_processing.to_stl_bytes(p) is None


# ---------------------------------------------------------------------------
# Estimator: unsupported / corrupt formats.
# ---------------------------------------------------------------------------


def test_estimator_returns_none_for_unrecognised_suffix(tmp_path: Path) -> None:
    p = tmp_path / "part.step"
    p.write_bytes(b"not a real STEP file")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_estimator_returns_none_for_corrupt_3mf(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.3mf"
    p.write_bytes(b"not actually a zip")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_ply_header_without_end_header_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "truncated.ply"
    # File ends mid-header, before an "end_header" line is ever seen.
    p.write_bytes(b"ply\nformat ascii 1.0\nelement vertex 3\n")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_ply_face_count_non_integer_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad-count.ply"
    p.write_bytes(b"ply\nformat ascii 1.0\nelement face notanumber\nend_header\n")
    assert mesh_processing._estimate_triangle_count(p) is None


# ---------------------------------------------------------------------------
# Memory-limit detection: cgroup / meminfo read failures each degrade
# gracefully rather than raising.
# ---------------------------------------------------------------------------


def test_detect_memory_limit_survives_unreadable_sources(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text
    unreadable = {
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        "/proc/meminfo",
    }

    def fake_read_text(self, *a, **k):
        if str(self) in unreadable:
            raise OSError("no such file")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    assert mesh_processing._detect_memory_limit_bytes() is None


def test_detect_memory_limit_reads_cgroup_v2_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "2147483648\n"  # 2 GB
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is not None
    assert limit <= 2147483648  # smallest of cgroup v2 and any other source


def test_detect_memory_limit_reads_cgroup_v1_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            raise OSError("cgroup v2 absent")
        if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
            return "1073741824\n"  # 1 GB
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is not None
    assert limit <= 1073741824


def test_detect_memory_limit_ignores_unlimited_cgroup_v2(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "max\n"
        if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
            raise OSError("absent")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    # Falls through to /proc/meminfo (real, host-dependent) or None.
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is None or limit > 0


def test_ram_triangle_cap_uses_cached_memory_limit(monkeypatch) -> None:
    # _MEMORY_LIMIT_BYTES already resolved (not None) -> _detect_memory_limit_bytes
    # is never called again.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

    def _boom():  # pragma: no cover - must never run
        raise AssertionError("must reuse cached limit")

    monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", _boom)
    assert mesh_processing._ram_triangle_cap(".stl") is not None


def test_ram_triangle_cap_none_when_detection_fails(monkeypatch) -> None:
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", None)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", lambda: None)
    assert mesh_processing._ram_triangle_cap(".stl") is None


def test_render_jobs_limit_falls_back_to_one_on_bad_config(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "max_render_jobs", "not-a-number")
    assert mesh_processing._render_jobs_limit() == 1


def test_exceeds_cap_survives_stat_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    p = tmp_path / "ghost.stl"
    _write_binary_stl(p, 10)

    def fake_stat(self):
        raise OSError("gone")

    monkeypatch.setattr(Path, "stat", fake_stat)
    # size_mb falls back to 0.0 on OSError, so the size cap can't trip; the
    # (also-mocked-out) triangle estimate then decides. Real stat is restored
    # by monkeypatch teardown.
    assert mesh_processing._exceeds_cap(p) is False


# ---------------------------------------------------------------------------
# Real mesh loading + geometry/thumbnail entry points (no mocked _load_mesh).
# ---------------------------------------------------------------------------


def _real_binary_stl_cube(path: Path) -> None:
    import trimesh

    trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(path, file_type="stl")


def test_load_mesh_returns_trimesh_for_real_stl(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) > 0


def test_load_mesh_renders_real_step_fixture() -> None:
    path = Path(__file__).parent / "fixtures" / "cascadio_material.stp"

    mesh = mesh_processing._load_mesh(path)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)

    assert mesh is not None
    assert len(mesh.faces) > 0
    assert geometry["triangle_count"] == len(mesh.faces)
    assert thumbnail is not None
    assert thumbnail.startswith(mesh_processing._PNG_MAGIC)


def test_step_tessellation_is_killed_when_child_exceeds_rss_budget(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "complex.step"
    path.write_text("ISO-10303-21;")

    class MemoryHungryProcess:
        pid = 4242
        returncode = None
        killed = False

        def poll(self):
            return -9 if self.killed else None

        def kill(self):
            self.killed = True
            self.returncode = -9

        def communicate(self):
            return b"", b""

    process = MemoryHungryProcess()
    monkeypatch.setattr(mesh_processing.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: 1024)
    monkeypatch.setattr(mesh_processing, "_process_rss_bytes", lambda _pid: 2048)

    assert mesh_processing._load_step_mesh_isolated(path) is None
    assert process.killed is True


def test_step_worker_rejects_tessellation_above_triangle_cap(monkeypatch) -> None:
    from app.services import step_worker

    path = Path(__file__).parent / "fixtures" / "cascadio_material.stp"
    output = path.parent / ".step-worker-over-cap.glb"
    monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", "1")
    monkeypatch.setattr(
        step_worker.sys, "argv", ["step_worker", str(path), str(output)]
    )
    try:
        assert step_worker.main() == 3
        assert not output.exists()
    finally:
        output.unlink(missing_ok=True)


def test_load_mesh_returns_none_for_unrecognised_extension(tmp_path: Path) -> None:
    # trimesh can't even pick a loader for an unknown extension, so this raises
    # inside trimesh.load_scene — exercising _load_mesh's broad except-and-log path.
    p = tmp_path / "garbage.foobar"
    p.write_bytes(b"this is not a mesh at all \x00\x01\x02")
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_flattens_scene_with_multiple_geometries(
    tmp_path: Path, monkeypatch
) -> None:
    # _load_mesh keeps the scene returned by trimesh.load_scene(...) until dump()
    # bakes each graph path's transforms. Stub load_scene to return a real Scene
    # so this scene-flattening path is exercised with real trimesh geometry.
    import trimesh

    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=[5, 5, 5]), node_name="a")
    scene.add_geometry(
        trimesh.creation.box(extents=[3, 3, 3]).apply_translation([10, 0, 0]),
        node_name="b",
    )
    p = tmp_path / "scene.3mf"
    scene.export(p, file_type="3mf")

    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    # Concatenated geometry from both boxes.
    assert len(mesh.faces) == 24  # 12 triangles per box * 2


def test_load_mesh_scene_with_no_trimesh_geometry_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    empty_scene = trimesh.Scene()  # no geometry at all
    p = tmp_path / "empty.3mf"
    p.write_bytes(b"placeholder")
    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: empty_scene)
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_scene_with_single_geometry_returns_it_directly(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    scene = trimesh.Scene()
    box = trimesh.creation.box(extents=[5, 5, 5])
    scene.add_geometry(box, node_name="a")
    p = tmp_path / "single.3mf"
    p.write_bytes(b"placeholder")
    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) == 12


def test_load_mesh_returns_none_for_unsupported_loaded_type(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    p = tmp_path / "cloud.stl"
    p.write_bytes(b"placeholder")
    # A defensive loader may return a PointCloud (or other non-mesh geometry) for
    # some inputs; _load_mesh must decline rather than mishandle it.
    monkeypatch.setattr(
        trimesh,
        "load_scene",
        lambda *a, **k: trimesh.points.PointCloud([[0, 0, 0]]),
    )
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_uses_typed_loader_without_processing(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    expected = trimesh.creation.box(extents=[1, 1, 1])
    calls: list[tuple[tuple, dict]] = []

    def typed_loader(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(trimesh, "load_scene", typed_loader)
    path = tmp_path / "typed.stl"
    path.write_bytes(b"placeholder")

    assert mesh_processing._load_mesh(path) is expected
    assert calls == [((str(path),), {"process": False})]


def test_geometry_from_mesh_handles_non_watertight_volume_error(monkeypatch) -> None:
    class _BrokenVolume:
        vertices = np.zeros((3, 3), dtype=np.float64)
        bounds = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        faces = np.zeros((1, 3), dtype=np.int64)

        @property
        def volume(self):
            raise ValueError("non-watertight")

    geometry = mesh_processing._geometry_from_mesh(_BrokenVolume())
    assert geometry["volume_mm3"] is None
    assert geometry["bbox_x_mm"] == 1.0


def test_extract_embedded_3mf_thumbnail_no_candidates_returns_none(
    tmp_path: Path,
) -> None:
    p = tmp_path / "no-preview.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
    assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None


def test_extract_embedded_3mf_thumbnail_survives_corrupt_archive(
    tmp_path: Path,
) -> None:
    p = tmp_path / "corrupt.3mf"
    p.write_bytes(b"not a zip archive")
    assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None


def test_analyze_mesh_reports_progress_labels(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    labels: list[str] = []
    geometry, thumb = mesh_processing.analyze_mesh(p, report=labels.append)
    assert labels == ["loading_mesh", "extracting_geometry", "rendering_thumbnail"]
    assert geometry["triangle_count"] is not None
    assert thumb is not None


def test_extract_geometry_real_load_and_reclaim(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    calls = {"n": 0}
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )
    geometry = mesh_processing.extract_geometry(p)
    assert geometry["triangle_count"] is not None
    assert calls["n"] == 1


def test_render_thumbnail_real_mesh_renders_png(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    thumb = mesh_processing.render_thumbnail(p)
    assert thumb is not None
    assert thumb.startswith(mesh_processing._PNG_MAGIC)


def test_render_thumbnail_falls_back_to_embedded_when_render_fails(
    tmp_path: Path, monkeypatch
) -> None:
    png = _valid_preview_png((32, 160, 240))
    p = tmp_path / "model.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
    )
    assert mesh_processing.render_thumbnail(p) == png


def test_render_thumbnail_returns_none_when_render_fails_and_no_embedded(
    tmp_path: Path, monkeypatch
) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 10)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
    )
    assert mesh_processing.render_thumbnail(p) is None


def test_render_thumbnail_over_cap_with_embedded_fallback_disabled_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    # Over cap, and the large-file embedded-preview fallback explicitly off:
    # nothing to fall back to, so the function must return None outright.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
    p = tmp_path / "dense.obj"
    _write_obj(p, tri_faces=5000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap must not load")),
    )
    assert mesh_processing.render_thumbnail(p) is None


def test_to_stl_bytes_read_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 10)

    def fake_read_bytes(self):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_converts_non_stl_mesh(tmp_path: Path) -> None:
    import trimesh

    p = tmp_path / "cube.obj"
    trimesh.creation.box(extents=[4, 4, 4]).export(p, file_type="obj")
    out = mesh_processing.to_stl_bytes(p)
    assert out is not None
    assert out[80:84] != b""  # binary STL triangle-count header present


def test_to_stl_bytes_returns_none_when_mesh_fails_to_load(tmp_path: Path) -> None:
    p = tmp_path / "garbage.foobar"
    p.write_bytes(b"not a mesh file \x00\x01")
    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_returns_none_on_export_failure(
    tmp_path: Path, monkeypatch
) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    # Force the "already an STL" fast-path to miss by faking a different suffix
    # so we exercise the load+export branch, then make export blow up.
    obj_path = tmp_path / "cube.obj"
    import trimesh

    trimesh.creation.box(extents=[4, 4, 4]).export(obj_path, file_type="obj")

    class _Boom:
        faces = np.zeros((1, 3))

        def export(self, *_a, **_k):
            raise RuntimeError("export boom")

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _Boom())
    assert mesh_processing.to_stl_bytes(obj_path) is None
