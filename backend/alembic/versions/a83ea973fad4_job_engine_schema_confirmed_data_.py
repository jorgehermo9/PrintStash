"""job engine schema [confirmed data-dropping, not a rename: jobs.claim_token, jobs.lease_expires_at, jobs.next_attempt_at, jobs.payload_json, jobs.replay_safe, jobs.visible]

The tables of the level-triggered job engine: reconciler cursors, work fences,
executor heartbeats, lane overrides, typed ingest requests, and the
``artifact_derivatives`` rows that record which derivatives each Artifact has
at which recipe.

``jobs`` (renamed from ``background_jobs`` by the previous revision) loses the
registry's claim, lease and replay columns: a Job never carries replayable
input, and the engine owns execution. Existing rows are history only, so they
get a ``legacy/<id>`` subject and interactive priority.

``thumbnail_generations`` and ``thumbnail_render_slots`` are replaced by
``artifact_derivatives`` and ``native_compute_slots``. Before they are dropped,
what they (and the ingest-time metadata) already proved is carried over at
recipe version 1, so an upgraded library is not re-rendered from scratch:

- a mesh Artifact whose metadata has geometry, and any G-code Artifact with a
  metadata row, has a ready ``metadata`` derivative;
- an Artifact with a published thumbnail has a ready ``thumbnail`` derivative
  owning that object.

The data work is plain SQL (``INSERT ... SELECT``) so the revision renders
offline; the output facts a generation recorded (hash, size, render timing)
are not carried over, since nothing reads them back.

Anything else (failed or superseded generations, missing geometry) is simply
not carried over: the derivative source finds it pending and derives it again
at backfill priority. Superseded generation objects lose their owner here and
are reclaimed by the storage audit like any other unowned object.

Revision ID: a83ea973fad4
Revises: 73abd9b883d9
Create Date: 2026-09-24 02:40:29.291707

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a83ea973fad4'
down_revision: Union[str, Sequence[str], None] = '73abd9b883d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pinned to this revision: the values stored at the time it was written.
_SENTINEL_FILE_HASH = "ext-file-sentinel-0000000000000000000000000000000000000000000"
_MESH_TYPES = ("STL", "THREE_MF", "OBJ", "STEP")
_GCODE = "GCODE"
_RECIPE = 1

_FILES = sa.table(
    "files",
    sa.column("id", sa.Integer),
    sa.column("file_type", sa.String),
    sa.column("sha256", sa.String),
    sa.column("thumbnail_path", sa.String),
)
_METADATA = sa.table(
    "metadata",
    sa.column("file_id", sa.Integer),
    sa.column("triangle_count", sa.Integer),
)
_DERIVATIVES = sa.table(
    "artifact_derivatives",
    sa.column("file_id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("recipe_version", sa.Integer),
    sa.column("state", sa.String),
    sa.column("attempts", sa.Integer),
    sa.column("storage_key", sa.String),
    sa.column("output_json", sa.Text),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)
_JOBS = sa.table(
    "jobs",
    sa.column("id", sa.String),
    sa.column("subject_key", sa.String),
    sa.column("priority", sa.String),
    sa.column("resubmits", sa.Integer),
)


def _file_type_in(values: tuple[str, ...]) -> sa.ColumnElement[bool]:
    # A native enum on PostgreSQL, a VARCHAR on SQLite: compare as text on both.
    return sa.cast(_FILES.c.file_type, sa.String).in_(values)


def _ready(kind: str, *, storage_key: sa.ColumnElement | None = None) -> list:
    now = sa.func.current_timestamp()
    return [
        _FILES.c.id,
        sa.literal(kind, sa.String),
        sa.literal(_RECIPE, sa.Integer),
        sa.literal("ready", sa.String),
        sa.literal(1, sa.Integer),
        storage_key if storage_key is not None else sa.null(),
        sa.literal("{}", sa.Text),
        now,
        now,
    ]


def _backfill_derivatives() -> None:
    columns = [
        "file_id",
        "kind",
        "recipe_version",
        "state",
        "attempts",
        "storage_key",
        "output_json",
        "created_at",
        "updated_at",
    ]
    real = _FILES.c.sha256 != _SENTINEL_FILE_HASH
    op.execute(
        _DERIVATIVES.insert().from_select(
            columns,
            sa.select(*_ready("metadata"))
            .select_from(_FILES.join(_METADATA, _METADATA.c.file_id == _FILES.c.id))
            .where(real)
            .where(
                sa.or_(
                    sa.and_(
                        _file_type_in(_MESH_TYPES),
                        _METADATA.c.triangle_count.is_not(None),
                    ),
                    _file_type_in((_GCODE,)),
                )
            ),
        )
    )
    op.execute(
        _DERIVATIVES.insert().from_select(
            columns,
            sa.select(*_ready("thumbnail", storage_key=_FILES.c.thumbnail_path))
            .where(real)
            .where(_file_type_in((*_MESH_TYPES, _GCODE)))
            .where(_FILES.c.thumbnail_path.is_not(None))
            .where(_FILES.c.thumbnail_path != ""),
        )
    )


def _backfill_jobs() -> None:
    op.execute(
        _JOBS.update().values(
            subject_key=sa.literal("legacy/") + _JOBS.c.id,
            priority="interactive",
            resubmits=0,
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('native_compute_slots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slot_number', sa.Integer(), nullable=False),
    sa.Column('lease_token', sa.String(length=64), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_native_compute_slots'))
    )
    with op.batch_alter_table('native_compute_slots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_native_compute_slots_lease_expires_at'), ['lease_expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_native_compute_slots_lease_token'), ['lease_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_native_compute_slots_slot_number'), ['slot_number'], unique=True)

    op.create_table('reconcile_cursors',
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('nudged_at', sa.DateTime(), nullable=True),
    sa.Column('pass_queued_at', sa.DateTime(), nullable=True),
    sa.Column('holder', sa.String(length=128), nullable=True),
    sa.Column('holder_expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_pass_started_at', sa.DateTime(), nullable=True),
    sa.Column('last_pass_finished_at', sa.DateTime(), nullable=True),
    sa.Column('last_pass_submitted', sa.Integer(), nullable=False),
    sa.Column('last_pass_deferred', sa.Integer(), nullable=False),
    sa.Column('last_occurrence_at', sa.DateTime(), nullable=True),
    sa.Column('state_json', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('source', name=op.f('pk_reconcile_cursors'))
    )
    op.create_table('work_executors',
    sa.Column('executor_id', sa.String(length=128), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('hostname', sa.String(length=255), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('app_version', sa.String(length=64), nullable=False),
    sa.Column('lanes', sa.Text(), nullable=False),
    sa.Column('active_mutations', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('executor_id', name=op.f('pk_work_executors'))
    )
    with op.batch_alter_table('work_executors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_work_executors_heartbeat_at'), ['heartbeat_at'], unique=False)

    op.create_table('work_fences',
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('holder', sa.String(length=128), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=False),
    sa.Column('acquired_at', sa.DateTime(), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_work_fences'))
    )
    with op.batch_alter_table('work_fences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_work_fences_expires_at'), ['expires_at'], unique=False)

    op.create_table('artifact_derivatives',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('recipe_version', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
    sa.Column('failure_reason', sa.String(length=64), nullable=True),
    sa.Column('storage_key', sa.String(length=2048), nullable=True),
    sa.Column('output_json', sa.Text(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('peak_rss_bytes', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], name=op.f('fk_artifact_derivatives_file_id_files'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_artifact_derivatives')),
    sa.UniqueConstraint('file_id', 'kind', 'recipe_version', name='uq_artifact_derivatives_recipe')
    )
    with op.batch_alter_table('artifact_derivatives', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_artifact_derivatives_file_id'), ['file_id'], unique=False)
        batch_op.create_index('ix_artifact_derivatives_kind_recipe_state', ['kind', 'recipe_version', 'state', 'next_attempt_at'], unique=False)

    op.create_table('derivative_regenerations',
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('requested_at', sa.DateTime(), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_derivative_regenerations_requested_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('kind', name=op.f('pk_derivative_regenerations'))
    )
    op.create_table('work_lane_overrides',
    sa.Column('lane', sa.String(length=32), nullable=False),
    sa.Column('concurrency', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_work_lane_overrides_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('lane', name=op.f('pk_work_lane_overrides'))
    )
    op.create_table('ingest_requests',
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('owner_user_id', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=512), nullable=True),
    sa.Column('model_name', sa.String(length=255), nullable=True),
    sa.Column('collection', sa.String(length=1024), nullable=True),
    sa.Column('tags', sa.Text(), nullable=True),
    sa.Column('source_hash', sa.String(length=64), nullable=True),
    sa.Column('file_type', sa.String(length=16), nullable=True),
    sa.Column('target_library_id', sa.Integer(), nullable=True),
    sa.Column('source_url', sa.String(length=2048), nullable=True),
    sa.Column('source_credential', sa.Text(), nullable=True),
    sa.Column('selection_json', sa.Text(), nullable=False),
    sa.Column('manifest_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_ingest_requests_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_ingest_requests_owner_user_id_users')),
    sa.ForeignKeyConstraint(['target_library_id'], ['external_libraries.id'], name=op.f('fk_ingest_requests_target_library_id_external_libraries'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('job_id', name=op.f('pk_ingest_requests'))
    )
    with op.batch_alter_table('ingest_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ingest_requests_owner_user_id'), ['owner_user_id'], unique=False)

    # Hand-added: carry proven outputs over before their tables go.
    _backfill_derivatives()

    with op.batch_alter_table('thumbnail_render_slots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_thumbnail_render_slots_generation_id'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_render_slots_lease_expires_at'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_render_slots_lease_token'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_render_slots_slot_number'))

    op.drop_table('thumbnail_render_slots')
    with op.batch_alter_table('thumbnail_generations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_thumbnail_generation_state_lease'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_generations_file_id'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_generations_lease_expires_at'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_generations_lease_token'))
        batch_op.drop_index(batch_op.f('ix_thumbnail_generations_state'))

    op.drop_table('thumbnail_generations')
    with op.batch_alter_table('artifact_upload_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_artifact_upload_sessions_background_job_id'))
        batch_op.create_index(batch_op.f('ix_artifact_upload_sessions_job_id'), ['job_id'], unique=False)

    with op.batch_alter_table('external_libraries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scan_requested_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('scan_requested_path', sa.String(length=2048), nullable=True))
        batch_op.create_index(batch_op.f('ix_external_libraries_scan_requested_at'), ['scan_requested_at'], unique=False)

    with op.batch_alter_table('inbox_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inbox_items_background_job_id'))
        batch_op.create_index(batch_op.f('ix_inbox_items_job_id'), ['job_id'], unique=False)

    # Hand-split: the new NOT NULL columns are added nullable, filled for the
    # existing rows, and only then constrained.
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject_key', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('priority', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('resubmits', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('app_version', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))

    _backfill_jobs()

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.alter_column('subject_key', existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column('priority', existing_type=sa.String(length=16), nullable=False)
        batch_op.alter_column('resubmits', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_index(batch_op.f('ix_background_jobs_claim_token'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_created_at'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_finished_at'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_kind'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_lease_expires_at'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_owner_user_id'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_replay_safe'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_state'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_updated_at'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_visible'))
        batch_op.drop_index(batch_op.f('ix_background_jobs_visible_state_owner_updated'))
        batch_op.create_index(batch_op.f('ix_jobs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_finished_at'), ['finished_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_kind'), ['kind'], unique=False)
        batch_op.create_index('ix_jobs_owner_state_updated', ['owner_user_id', 'state', 'updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_owner_user_id'), ['owner_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_subject_key'), ['subject_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index('uq_jobs_active_subject', ['kind', 'subject_key'], unique=True, sqlite_where=sa.text("state IN ('queued', 'running', 'interrupted')"), postgresql_where=sa.text("state IN ('queued', 'running', 'interrupted')"))
        batch_op.drop_column('next_attempt_at')
        batch_op.drop_column('replay_safe')
        batch_op.drop_column('claim_token')
        batch_op.drop_column('visible')
        batch_op.drop_column('payload_json')
        batch_op.drop_column('lease_expires_at')

    with op.batch_alter_table('staging_leases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_staging_leases_background_job_id'))
        batch_op.create_index(batch_op.f('ix_staging_leases_job_id'), ['job_id'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('staging_leases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_staging_leases_job_id'))
        batch_op.create_index(batch_op.f('ix_staging_leases_background_job_id'), ['job_id'], unique=False)

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lease_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('payload_json', sa.Text(), nullable=True))
        # Hand-added defaults: existing rows need a value for the old NOT NULL
        # columns. Every restored row is terminal history, so false is right.
        batch_op.add_column(sa.Column('visible', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('claim_token', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('replay_safe', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('next_attempt_at', sa.DateTime(), nullable=True))
        batch_op.drop_index('uq_jobs_active_subject', sqlite_where=sa.text("state IN ('queued', 'running', 'interrupted')"), postgresql_where=sa.text("state IN ('queued', 'running', 'interrupted')"))
        batch_op.drop_index(batch_op.f('ix_jobs_updated_at'))
        batch_op.drop_index(batch_op.f('ix_jobs_subject_key'))
        batch_op.drop_index(batch_op.f('ix_jobs_state'))
        batch_op.drop_index(batch_op.f('ix_jobs_owner_user_id'))
        batch_op.drop_index('ix_jobs_owner_state_updated')
        batch_op.drop_index(batch_op.f('ix_jobs_kind'))
        batch_op.drop_index(batch_op.f('ix_jobs_finished_at'))
        batch_op.drop_index(batch_op.f('ix_jobs_created_at'))
        batch_op.create_index(batch_op.f('ix_background_jobs_visible_state_owner_updated'), ['visible', 'state', 'owner_user_id', 'updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_visible'), ['visible'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_replay_safe'), ['replay_safe'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_owner_user_id'), ['owner_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_next_attempt_at'), ['next_attempt_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_lease_expires_at'), ['lease_expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_finished_at'), ['finished_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_background_jobs_claim_token'), ['claim_token'], unique=False)
        batch_op.drop_column('started_at')
        batch_op.drop_column('app_version')
        batch_op.drop_column('resubmits')
        batch_op.drop_column('priority')
        batch_op.drop_column('subject_key')

    with op.batch_alter_table('inbox_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inbox_items_job_id'))
        batch_op.create_index(batch_op.f('ix_inbox_items_background_job_id'), ['job_id'], unique=False)

    with op.batch_alter_table('external_libraries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_external_libraries_scan_requested_at'))
        batch_op.drop_column('scan_requested_path')
        batch_op.drop_column('scan_requested_at')

    with op.batch_alter_table('artifact_upload_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_artifact_upload_sessions_job_id'))
        batch_op.create_index(batch_op.f('ix_artifact_upload_sessions_background_job_id'), ['job_id'], unique=False)

    op.create_table('thumbnail_generations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('source_sha256', sa.String(length=64), nullable=False),
    sa.Column('recipe_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('storage_key', sa.String(length=2048), nullable=True),
    sa.Column('output_sha256', sa.String(length=64), nullable=True),
    sa.Column('output_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('output_etag', sa.String(length=256), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('strategy', sa.String(length=32), nullable=True),
    sa.Column('complete', sa.Boolean(), nullable=False),
    sa.Column('failure_reason', sa.String(length=64), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease_token', sa.String(length=64), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('peak_rss_bytes', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], name=op.f('fk_thumbnail_generations_file_id_files'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thumbnail_generations')),
    sa.UniqueConstraint('file_id', 'source_sha256', 'recipe_fingerprint', name=op.f('uq_thumbnail_generation_recipe'))
    )
    with op.batch_alter_table('thumbnail_generations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_thumbnail_generations_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_generations_lease_token'), ['lease_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_generations_lease_expires_at'), ['lease_expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_generations_file_id'), ['file_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_generation_state_lease'), ['state', 'lease_expires_at'], unique=False)

    op.create_table('thumbnail_render_slots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slot_number', sa.Integer(), nullable=False),
    sa.Column('generation_id', sa.Integer(), nullable=True),
    sa.Column('lease_token', sa.String(length=64), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['generation_id'], ['thumbnail_generations.id'], name=op.f('fk_thumbnail_render_slots_generation_id_thumbnail_generations'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thumbnail_render_slots'))
    )
    with op.batch_alter_table('thumbnail_render_slots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_thumbnail_render_slots_slot_number'), ['slot_number'], unique=True)
        batch_op.create_index(batch_op.f('ix_thumbnail_render_slots_lease_token'), ['lease_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_render_slots_lease_expires_at'), ['lease_expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_thumbnail_render_slots_generation_id'), ['generation_id'], unique=False)

    with op.batch_alter_table('ingest_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ingest_requests_owner_user_id'))

    op.drop_table('ingest_requests')
    op.drop_table('work_lane_overrides')
    op.drop_table('derivative_regenerations')
    with op.batch_alter_table('artifact_derivatives', schema=None) as batch_op:
        batch_op.drop_index('ix_artifact_derivatives_kind_recipe_state')
        batch_op.drop_index(batch_op.f('ix_artifact_derivatives_file_id'))

    op.drop_table('artifact_derivatives')
    with op.batch_alter_table('work_fences', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_work_fences_expires_at'))

    op.drop_table('work_fences')
    with op.batch_alter_table('work_executors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_work_executors_heartbeat_at'))

    op.drop_table('work_executors')
    op.drop_table('reconcile_cursors')
    with op.batch_alter_table('native_compute_slots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_native_compute_slots_slot_number'))
        batch_op.drop_index(batch_op.f('ix_native_compute_slots_lease_token'))
        batch_op.drop_index(batch_op.f('ix_native_compute_slots_lease_expires_at'))

    op.drop_table('native_compute_slots')
    # ### end Alembic commands ###
