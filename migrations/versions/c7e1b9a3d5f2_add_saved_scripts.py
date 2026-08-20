"""add saved scripts

Revision ID: c7e1b9a3d5f2
Revises: a4d9e7b2c6f3
Create Date: 2026-06-27

"""
from alembic import op
import sqlalchemy as sa


revision = "c7e1b9a3d5f2"
down_revision = "a4d9e7b2c6f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_scripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=200),
            nullable=False,
            server_default="Untitled script",
        ),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("thumbnail_storage_path", sa.String(length=500), nullable=True),
        sa.Column("thumbnail_mime_type", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_saved_scripts_user_created",
        "saved_scripts",
        ["user_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_saved_scripts_user_created", table_name="saved_scripts")
    op.drop_table("saved_scripts")
