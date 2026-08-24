"""Framework-neutral mesh rendering and geometric normalization."""

from .rasterizer import (
    FLAT_MESH_THICKNESS_RATIO,
    RasterBudget,
    render_mesh_thumbnail,
)

__all__ = ["FLAT_MESH_THICKNESS_RATIO", "RasterBudget", "render_mesh_thumbnail"]
