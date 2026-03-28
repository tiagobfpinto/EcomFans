"""add brand_dna_analyses table

Revision ID: f2e4a8c1b7d6
Revises: d1f3a7b2c9e5
Create Date: 2026-03-10

"""
from alembic import op
import sqlalchemy as sa

revision = "f2e4a8c1b7d6"
down_revision = "d1f3a7b2c9e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "brand_dna_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("brand_name", sa.String(length=255), nullable=True),
        sa.Column("brand_description", sa.Text(), nullable=True),
        sa.Column("color_palette", sa.Text(), nullable=True),
        sa.Column("products_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["worker_job_id"], ["worker_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brand_dna_analyses_user_id", "brand_dna_analyses", ["user_id"])
    op.create_index(
        "ix_brand_dna_analyses_worker_job_id", "brand_dna_analyses", ["worker_job_id"]
    )


def downgrade():
    op.drop_index("ix_brand_dna_analyses_worker_job_id", table_name="brand_dna_analyses")
    op.drop_index("ix_brand_dna_analyses_user_id", table_name="brand_dna_analyses")
    op.drop_table("brand_dna_analyses")
