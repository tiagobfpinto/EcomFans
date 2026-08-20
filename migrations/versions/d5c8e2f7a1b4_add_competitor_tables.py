"""add competitor tables

Revision ID: d5c8e2f7a1b4
Revises: c7e1b9a3d5f2
Create Date: 2026-07-19

"""
from alembic import op
import sqlalchemy as sa


revision = "d5c8e2f7a1b4"
down_revision = "c7e1b9a3d5f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "competitors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_competitors_user_created", "competitors", ["user_id", "created_at"]
    )
    op.create_index("ix_competitors_product_id", "competitors", ["product_id"])

    op.create_table(
        "competitor_ads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=False,
            server_default="video/mp4",
        ),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column(
            "transcript_status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("transcript_error", sa.Text(), nullable=True),
        sa.Column("transcribe_job_id", sa.Integer(), nullable=True),
        sa.Column("analysis_json", sa.Text(), nullable=True),
        sa.Column(
            "analysis_status",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
        sa.Column("analysis_error", sa.Text(), nullable=True),
        sa.Column("analysis_job_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.ForeignKeyConstraint(["transcribe_job_id"], ["worker_jobs.id"]),
        sa.ForeignKeyConstraint(["analysis_job_id"], ["worker_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_competitor_ads_competitor_created",
        "competitor_ads",
        ["competitor_id", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_competitor_ads_competitor_created", table_name="competitor_ads"
    )
    op.drop_table("competitor_ads")
    op.drop_index("ix_competitors_product_id", table_name="competitors")
    op.drop_index("ix_competitors_user_created", table_name="competitors")
    op.drop_table("competitors")
