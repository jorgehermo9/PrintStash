"""Versioned native metadata accepted from the OrcaSlicer hook."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrcaObjectLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=512)
    object_id: str = Field(min_length=1, max_length=64)
    copy_index: int = Field(ge=0, le=100_000)


class OrcaSourceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str | None = Field(default=None, max_length=512)
    basename: str | None = Field(default=None, max_length=512)
    first_object_name: str | None = Field(default=None, max_length=512)
    object_count: int | None = Field(default=None, ge=0, le=100_000)
    instance_count: int | None = Field(default=None, ge=0, le=100_000)
    plate_name: str | None = Field(default=None, max_length=255)
    project_name: str | None = Field(default=None, max_length=255)
    object_labels: list[OrcaObjectLabel] = Field(default_factory=list, max_length=512)


class OrcaSlicerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)


class OrcaPrinterContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=128)
    preset: str | None = Field(default=None, max_length=255)


class OrcaFilamentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, max_length=64)
    preset: str | None = Field(default=None, max_length=255)


class OrcaProcessContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str | None = Field(default=None, max_length=255)


class OrcaPrintStatsContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_time_s: int | None = Field(default=None, ge=0)
    filament_weight_g: float | None = Field(default=None, ge=0)
    filament_length_mm: float | None = Field(default=None, ge=0)
    filament_cost: float | None = Field(default=None, ge=0)


class OrcaNativeContext(BaseModel):
    """One normalized source of truth for an Orca export."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    classification: Literal["single_object", "multi_object", "unknown"]
    source: OrcaSourceContext
    slicer: OrcaSlicerContext
    printer: OrcaPrinterContext
    filaments: list[OrcaFilamentContext] = Field(default_factory=list, max_length=32)
    process: OrcaProcessContext
    print_stats: OrcaPrintStatsContext
    field_sources: dict[str, str] = Field(default_factory=dict, max_length=128)

    @model_validator(mode="after")
    def validate_classification(self) -> "OrcaNativeContext":
        labelled_objects = {label.object_id for label in self.source.object_labels}
        if self.classification == "single_object":
            if not self.source.filename or self.source.object_count != 1:
                raise ValueError("single_object_requires_one_named_source")
            if len(labelled_objects) > 1:
                raise ValueError("single_object_has_multiple_object_labels")
        elif self.classification == "multi_object":
            if self.source.object_count is None or self.source.object_count < 2:
                raise ValueError("multi_object_requires_multiple_sources")
        return self
