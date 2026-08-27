"""Getting a mesh object out of a file, in whatever shape the file arrives.

`_load_mesh` is the one place `trimesh` is called, and it exists to absorb the
variety of what that call returns. A `.3mf` or `.glb` usually loads as a
*Scene* rather than a mesh — one geometry, or several, or none that are meshes at
all — and every caller above this function wants a single mesh or nothing. A
`None` here means "no thumbnail, no geometry", which is a fine outcome; an
unhandled type means a traceback in a background scan.

STEP is the exception and runs out-of-process. Tessellating a CAD file is
unbounded work on untrusted input: a modest STEP can expand into hundreds of
millions of triangles, and there is no way to know before trying. So the child
is watched and killed when its RSS passes the budget — which is a real kill of a
real process, not a raised exception, because an in-process tessellation that
went that far would already have taken the parent with it.

`_geometry_from_mesh` reads dimensions off a loaded mesh. Volume is the sharp
edge: `trimesh` raises for a non-watertight mesh, and most models people
download are not watertight, so the failure is the common case and has to leave
the other measurements intact.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from app.services import mesh_processing
from tests.paths import FIXTURES_DIR

from .._meshes import _real_binary_stl_cube


class TestLoadMesh:
    def test_load_mesh_returns_trimesh_for_real_stl(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        mesh = mesh_processing._load_mesh(p)
        assert mesh is not None
        assert len(mesh.faces) > 0

    def test_load_mesh_renders_real_step_fixture(self) -> None:
        path = FIXTURES_DIR / "cascadio_material.stp"

        mesh = mesh_processing._load_mesh(path)
        geometry, thumbnail = mesh_processing.analyze_mesh(path)

        assert mesh is not None
        assert len(mesh.faces) > 0
        assert geometry["triangle_count"] == len(mesh.faces)
        assert thumbnail is not None
        assert thumbnail.startswith(mesh_processing._PNG_MAGIC)

    def test_load_mesh_returns_none_for_unrecognised_extension(
        self, tmp_path: Path
    ) -> None:
        # trimesh can't even pick a loader for an unknown extension, so this raises
        # inside trimesh.load_mesh — exercising _load_mesh's broad except-and-log path.
        p = tmp_path / "garbage.foobar"
        p.write_bytes(b"this is not a mesh at all \x00\x01\x02")
        assert mesh_processing._load_mesh(p) is None

    def test_load_mesh_flattens_scene_with_multiple_geometries(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # trimesh.load_mesh(...) already concatenates a multi-geometry
        # Scene into one Trimesh internally, so _load_mesh's own Scene-flattening
        # branch is normally unreachable through that call. Stub trimesh.load_mesh to
        # return a real Scene so this (still-real) fallback path is exercised —
        # it's a legitimate defensive path for a future/edge-case trimesh return.

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=[5, 5, 5]), node_name="a")
        scene.add_geometry(
            trimesh.creation.box(extents=[3, 3, 3]).apply_translation([10, 0, 0]),
            node_name="b",
        )
        p = tmp_path / "scene.3mf"
        scene.export(p, file_type="3mf")

        monkeypatch.setattr(trimesh, "load_mesh", lambda *a, **k: scene)
        mesh = mesh_processing._load_mesh(p)
        assert mesh is not None
        # Concatenated geometry from both boxes.
        assert len(mesh.faces) == 24

    def test_load_mesh_scene_with_no_trimesh_geometry_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        empty_scene = trimesh.Scene()  # no geometry at all
        p = tmp_path / "empty.3mf"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_mesh", lambda *a, **k: empty_scene)
        assert mesh_processing._load_mesh(p) is None

    def test_load_mesh_scene_with_single_geometry_returns_it_directly(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        scene = trimesh.Scene()
        box = trimesh.creation.box(extents=[5, 5, 5])
        scene.add_geometry(box, node_name="a")
        p = tmp_path / "single.3mf"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_mesh", lambda *a, **k: scene)
        mesh = mesh_processing._load_mesh(p)
        assert mesh is not None
        assert len(mesh.faces) == 12

    def test_load_mesh_returns_none_for_unsupported_loaded_type(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        p = tmp_path / "cloud.stl"
        p.write_bytes(b"placeholder")
        # A defensive loader may return a PointCloud (or other non-mesh geometry) for
        # some inputs; _load_mesh must decline rather than mishandle it.
        monkeypatch.setattr(
            trimesh,
            "load_mesh",
            lambda *a, **k: trimesh.points.PointCloud([[0, 0, 0]]),
        )
        assert mesh_processing._load_mesh(p) is None

    def test_load_mesh_uses_typed_loader_without_processing(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        expected = trimesh.creation.box(extents=[1, 1, 1])
        calls: list[tuple[tuple, dict]] = []

        def typed_loader(*args, **kwargs):
            calls.append((args, kwargs))
            return expected

        monkeypatch.setattr(trimesh, "load_mesh", typed_loader)
        path = tmp_path / "typed.stl"
        path.write_bytes(b"placeholder")

        assert mesh_processing._load_mesh(path) is expected
        assert calls == [((str(path),), {"process": False})]


class TestLoadStepMeshIsolated:
    def test_step_tessellation_is_killed_when_child_exceeds_rss_budget(
        self, tmp_path: Path, monkeypatch
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
        monkeypatch.setattr(
            mesh_processing.subprocess, "Popen", lambda *a, **k: process
        )
        monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: 1024)
        monkeypatch.setattr(mesh_processing, "_process_rss_bytes", lambda _pid: 2048)

        assert mesh_processing._load_step_mesh_isolated(path) is None
        assert process.killed is True


class TestGeometryFromMesh:
    def test_geometry_from_mesh_handles_non_watertight_volume_error(
        self, monkeypatch
    ) -> None:
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
