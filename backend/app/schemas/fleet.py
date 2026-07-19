from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import RoutingStrategy


class QueueJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(gt=0)
    strategy: RoutingStrategy = RoutingStrategy.MANUAL
    printer_id: Optional[int] = Field(default=None, gt=0)
    spool_id: Optional[int] = None
    spool_name: Optional[str] = Field(default=None, max_length=256)
    spool_filament_id: Optional[int] = None

    @model_validator(mode="after")
    def manual_requires_printer(self) -> "QueueJobCreate":
        if self.strategy == RoutingStrategy.MANUAL and self.printer_id is None:
            raise ValueError("printer_id_required")
        return self


class QueueJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Optional[RoutingStrategy] = None
    printer_id: Optional[int] = Field(default=None, gt=0)
    queue_position: Optional[int] = Field(default=None, ge=1)
    expected_updated_at: Optional[datetime] = None


class PrinterRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_default: Optional[bool] = None
    drain_mode: Optional[bool] = None
    drain_reason: Optional[str] = Field(default=None, max_length=512)


class PrinterRoutingRead(BaseModel):
    printer_id: int
    is_default: bool
    drain_mode: bool
    drain_reason: Optional[str] = None
    drain_updated_at: Optional[datetime] = None


class MaintenanceWindowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def valid_range(self) -> "MaintenanceWindowCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("maintenance_window_invalid")
        return self


class MaintenanceWindowRead(BaseModel):
    id: int
    printer_id: int
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MaintenanceWindowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=512)


class MaintenanceLogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: Optional[datetime] = None
    category: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=1, max_length=4096)
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = Field(default=None, max_length=32)


class MaintenanceLogRead(BaseModel):
    id: int
    printer_id: int
    performed_at: datetime
    category: str
    note: str
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MaintenanceLogUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: Optional[datetime] = None
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = Field(default=None, max_length=32)


class FleetSummary(BaseModel):
    total_printers: int
    queued_jobs: int
    active_jobs: int
    draining_printers: int
    maintenance_printers: int
    attention_jobs: int
