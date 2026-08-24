"""repair duplicate Bambu external jobs and preserve capture error detail

Revision ID: f8a6c2d9e1b4
Revises: e7b4c1d9a6f2
Create Date: 2026-08-24 00:00:00
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision: str = "f8a6c2d9e1b4"
down_revision: str | None = "e7b4c1d9a6f2"
branch_labels = None
depends_on = None

_IDENTITY_COLUMNS = (
    "external_task_id",
    "external_subtask_id",
    "external_project_id",
)
_METADATA_COLUMNS = (
    "external_display_name",
    "external_task_id",
    "external_subtask_id",
    "external_project_id",
    "external_profile_id",
    "external_gcode_file",
    "external_plate_index",
    "external_current_layer",
    "external_total_layers",
    "external_nozzle_diameter",
)
_EVIDENCE_RANK = {
    "vault": 0,
    "metadata_only": 1,
    "capture_pending": 2,
    "capture_failed": 2,
    "gcode_archived": 3,
    "project_archived": 4,
}


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _identity(row: sa.RowMapping) -> set[str]:
    return {
        str(row[column]).strip()
        for column in _IDENTITY_COLUMNS
        if row[column] not in (None, "")
    }


def _same_identity_or_transition(left: sa.RowMapping, right: sa.RowMapping) -> bool:
    if left["printer_id"] != right["printer_id"]:
        return False
    left_ids = _identity(left)
    right_ids = _identity(right)
    if left_ids.intersection(right_ids):
        return True
    # A Bambu firmware sequence can emit only project_id and then only
    # task_id. The filename is the remaining stable evidence; bound this
    # fallback to a short active-report window so repeated prints of the same
    # file remain separate history rows.
    if (
        left["remote_filename"]
        and left["remote_filename"] == right["remote_filename"]
        and left_ids
        and right_ids
    ):
        delta = abs(
            (
                _timestamp(left["created_at"])
                - _timestamp(right["created_at"])
            ).total_seconds()
        )
        return delta <= 300
    return bool(left.get("provider_job_id")) and left.get("provider_job_id") == right.get(
        "provider_job_id"
    )


def _groups(rows: list[sa.RowMapping]) -> list[list[sa.RowMapping]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, row in enumerate(rows):
        for other_index in range(index):
            if _same_identity_or_transition(row, rows[other_index]):
                union(index, other_index)
    grouped: dict[int, list[sa.RowMapping]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(find(index), []).append(row)
    return [group for group in grouped.values() if len(group) > 1]


def upgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        batch.add_column(sa.Column("artifact_capture_error_code", sa.String(128)))
        batch.add_column(sa.Column("artifact_capture_error_message", sa.String(1024)))
        batch.add_column(sa.Column("dedupe_absorbed_at", sa.DateTime()))
        batch.add_column(sa.Column("dedupe_survivor_id", sa.Integer()))
        batch.create_index("ix_print_jobs_dedupe_absorbed_at", ["dedupe_absorbed_at"])
        batch.create_index("ix_print_jobs_dedupe_survivor_id", ["dedupe_survivor_id"])

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, printer_id, file_id, model_id, remote_filename, state, "
                "progress, provider_job_id, source, external_display_name, "
                "external_task_id, external_subtask_id, external_project_id, "
                "external_profile_id, external_gcode_file, external_plate_index, "
                "external_current_layer, external_total_layers, "
                "external_nozzle_diameter, artifact_evidence, artifact_capture_error, "
                "artifact_capture_error_code, artifact_capture_error_message, "
                "started_at, finished_at, created_at, updated_at "
                "FROM print_jobs WHERE source = 'external' "
                "AND (external_task_id IS NOT NULL OR external_subtask_id IS NOT NULL "
                "OR external_project_id IS NOT NULL) "
                "ORDER BY printer_id, created_at, id"
            )
        ).mappings()
    )
    now = datetime.now(timezone.utc)
    for group in _groups(rows):
        ordered = sorted(group, key=lambda row: (_timestamp(row["created_at"]), row["id"]))
        survivor = ordered[0]
        survivor_id = int(survivor["id"])
        values: dict[str, object] = {}
        for column in _METADATA_COLUMNS:
            values[column] = next(
                (
                    row[column]
                    for row in ordered
                    if row[column] not in (None, "")
                ),
                None,
            )
        provider_job_id = next(
            (
                row["provider_job_id"]
                for row in reversed(ordered)
                if row["provider_job_id"] not in (None, "")
            ),
            None,
        )
        best_evidence = max(
            ordered,
            key=lambda row: _EVIDENCE_RANK.get(str(row["artifact_evidence"]), 0),
        )
        values.update(
            provider_job_id=provider_job_id,
            artifact_evidence=best_evidence["artifact_evidence"],
            artifact_capture_error=best_evidence["artifact_capture_error"],
            artifact_capture_error_code=best_evidence["artifact_capture_error_code"],
            artifact_capture_error_message=best_evidence[
                "artifact_capture_error_message"
            ],
        )
        if _EVIDENCE_RANK.get(str(best_evidence["artifact_evidence"]), 0) >= 3:
            values["file_id"] = best_evidence["file_id"]
            values["model_id"] = best_evidence["model_id"]
        latest = ordered[-1]
        for column in ("state", "progress", "started_at", "finished_at", "updated_at"):
            values[column] = latest[column]
        values["updated_at"] = max(_timestamp(latest["updated_at"]), now)
        connection.execute(
            sa.text(
                "UPDATE print_jobs SET "
                + ", ".join(f"{column} = :{column}" for column in values)
                + " WHERE id = :id"
            ),
            {**values, "id": survivor_id},
        )
        for absorbed in ordered[1:]:
            connection.execute(
                sa.text(
                    "UPDATE print_jobs SET dedupe_absorbed_at = :absorbed_at, "
                    "dedupe_survivor_id = :survivor_id, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {
                    "absorbed_at": now,
                    "survivor_id": survivor_id,
                    "updated_at": now,
                    "id": int(absorbed["id"]),
                },
            )


def downgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_index("ix_print_jobs_dedupe_survivor_id")
        batch.drop_index("ix_print_jobs_dedupe_absorbed_at")
        batch.drop_column("dedupe_survivor_id")
        batch.drop_column("dedupe_absorbed_at")
        batch.drop_column("artifact_capture_error_message")
        batch.drop_column("artifact_capture_error_code")
