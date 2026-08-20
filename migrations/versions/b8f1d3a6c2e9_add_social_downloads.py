"""add social downloads

Revision ID: b8f1d3a6c2e9
Revises: a6d4e9f1b2c7
Create Date: 2026-06-15

"""
from alembic import op
import sqlalchemy as sa


revision = "b8f1d3a6c2e9"
down_revision = "a6d4e9f1b2c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "social_downloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("worker_job_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["worker_job_id"], ["worker_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_social_downloads_user_created",
        "social_downloads",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_social_downloads_user_status",
        "social_downloads",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_social_downloads_worker_job_id",
        "social_downloads",
        ["worker_job_id"],
    )


def downgrade():
    op.drop_index(
        "ix_social_downloads_worker_job_id", table_name="social_downloads"
    )
    op.drop_index(
        "ix_social_downloads_user_status", table_name="social_downloads"
    )
    op.drop_index(
        "ix_social_downloads_user_created", table_name="social_downloads"
    )
    op.drop_table("social_downloads")
