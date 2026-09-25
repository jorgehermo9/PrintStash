"""Retiring grouping tables leaves independent printable library records intact."""

from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

from alembic import command
from app.db.migrate import _alembic_config
from app.db.models import File, FileType, Model, MultipartModel, PrintJob
from tests.factories import (
    build_file,
    build_model,
    build_multipart_model,
    build_print_job,
)

PREVIOUS = "6f27f2e6090a"
REMOVAL = "0b36c56fb17d"


class TestRemoveModelFamiliesMigration:
    def test_upgrade_preserves_printable_records_with_existing_grouping(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'library.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, PREVIOUS)
        engine = create_engine(url)
        try:
            with Session(engine) as session:
                model = build_model(session, "Original", hash="a" * 64)
                revision = build_file(
                    session,
                    model,
                    file_type=FileType.GCODE,
                    filename="original.gcode",
                    path="/library/original.gcode",
                    size_bytes=3,
                    sha256="b" * 64,
                )
                job = build_print_job(session, revision)
                multipart = build_multipart_model(session, "Printed kit")
                now = "2026-01-01T00:00:00+00:00"
                session.execute(
                    text("""
                    INSERT INTO model_families
                        (name, slug, export_id, version, created_at, updated_at)
                    VALUES ('Variations', 'variations', '00000000-0000-0000-0000-000000000001',
                            1, :now, :now)
                """),
                    {"now": now},
                )
                session.execute(
                    text("""
                    INSERT INTO model_family_members
                        (family_id, model_id, role, mirrored, mirror_verified,
                         relative_review_required, joined_via, sort_order, created_at, updated_at)
                    VALUES (1, :model_id, 'canonical', 0, 1, 0, 'manual', 0, :now, :now)
                """),
                    {"model_id": model.id, "now": now},
                )
                session.commit()
                model_id, file_id, job_id, multipart_id = (
                    model.id,
                    revision.id,
                    job.id,
                    multipart.id,
                )

            command.upgrade(config, REMOVAL)

            tables = set(inspect(engine).get_table_names())
            assert not tables.intersection(
                {
                    "model_families",
                    "model_family_members",
                    "model_family_tags",
                    "model_family_stars",
                }
            )
            with Session(engine) as session:
                assert session.get(Model, model_id).name == "Original"
                assert session.get(File, file_id).original_filename == "original.gcode"
                assert session.get(PrintJob, job_id).model_id == model_id
                assert session.get(MultipartModel, multipart_id).name == "Printed kit"
                assert session.exec(select(Model.id)).all() == [model_id]

            command.downgrade(config, PREVIOUS)
            tables = set(inspect(engine).get_table_names())
            assert {
                "model_families",
                "model_family_members",
                "model_family_tags",
                "model_family_stars",
            } <= tables
            with Session(engine) as session:
                assert session.exec(select(Model.id)).all() == [model_id]
                assert session.execute(text("SELECT COUNT(*) FROM model_families")).scalar_one() == 0

            command.upgrade(config, REMOVAL)
            assert "model_families" not in inspect(engine).get_table_names()
        finally:
            engine.dispose()
