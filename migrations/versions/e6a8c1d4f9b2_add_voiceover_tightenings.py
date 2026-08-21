"""add voiceover tightenings

Revision ID: e6a8c1d4f9b2
Revises: c8f4a2d6e1b9
Create Date: 2026-08-21

"""
from alembic import op
import sqlalchemy as sa


revision = "e6a8c1d4f9b2"
down_revision = "c8f4a2d6e1b9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voiceover_tightenings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("worker_job_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default="queued",
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("original_storage_path", sa.String(length=500), nullable=False),
        sa.Column("output_storage_path", sa.String(length=500), nullable=True),
        sa.Column("original_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "preset", sa.String(length=20), nullable=False,
            server_default="dynamic",
        ),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("original_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("output_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("removed_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("pauses_shortened", sa.Integer(), nullable=True),
        sa.Column("overlaps_applied", sa.Integer(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["worker_job_id"], ["worker_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_voiceover_tightenings_user_created",
        "voiceover_tightenings", ["user_id", "created_at"],
    )
    op.create_index(
        "ix_voiceover_tightenings_user_status",
        "voiceover_tightenings", ["user_id", "status"],
    )
    op.create_index(
        "ix_voiceover_tightenings_worker_job_id",
        "voiceover_tightenings", ["worker_job_id"],
    )


def downgrade():
    op.drop_index(
        "ix_voiceover_tightenings_worker_job_id",
        table_name="voiceover_tightenings",
    )
    op.drop_index(
        "ix_voiceover_tightenings_user_status",
        table_name="voiceover_tightenings",
    )
    op.drop_index(
        "ix_voiceover_tightenings_user_created",
        table_name="voiceover_tightenings",
    )
    op.drop_table("voiceover_tightenings")
