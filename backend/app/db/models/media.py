"""Artifact derivatives and native-memory compute admission."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import DerivativeState


class ArtifactDerivative(SQLModel, table=True):
    """The lifecycle record of one derived output of one Artifact.

    A derivative is a pure function of an Artifact's bytes and a versioned
    recipe. Its *output* stays with the owner of that output (``metadata``,
    ``File.thumbnail_path``, a toolpath blob); this row records whether the
    output exists at the current recipe, how many attempts it took, and why it
    failed. An Artifact with no row for an applicable kind at the current recipe
    is pending: the derivative source finds it with an anti-join, so ingestion
    never needs to know which kinds exist.
    """

    __tablename__ = "artifact_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "kind",
            "recipe_version",
            name="uq_artifact_derivatives_recipe",
        ),
        Index(
            "ix_artifact_derivatives_kind_recipe_state",
            "kind",
            "recipe_version",
            "state",
            "next_attempt_at",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    kind: str = Field(max_length=32)
    recipe_version: int
    state: DerivativeState = Field(
        default=DerivativeState.QUEUED,
        sa_column=Column(String(16), nullable=False),
    )
    attempts: int = Field(default=0)
    next_attempt_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(default=None, max_length=64)
    # The storage object this derivative published, when it publishes one.
    # A column rather than JSON because trash, backup ownership and vault
    # migration must find (and remap) every derivative object by key.
    storage_key: Optional[str] = Field(default=None, max_length=2048)
    # Owner-defined description of the output (a size, a strategy). Never
    # input: a re-derivation reads the Artifact, not this.
    output_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    duration_ms: Optional[int] = None
    # Bytes: a native render's resident set passes 2 GiB, beyond INTEGER.
    peak_rss_bytes: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DerivativeRegeneration(SQLModel, table=True):
    """An administrator's "regenerate all" for one derivative kind.

    A recipe bump is the code saying outputs changed. This is an operator
    saying so without a code change (for example after changing the thumbnail
    width): every output of the kind older than ``requested_at`` counts as
    stale to the derivative source until it is re-derived.
    """

    __tablename__ = "derivative_regenerations"

    kind: str = Field(primary_key=True, max_length=32)
    requested_at: datetime = Field(default_factory=utcnow)
    requested_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
