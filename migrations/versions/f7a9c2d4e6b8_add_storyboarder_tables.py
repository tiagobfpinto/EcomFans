"""add storyboarder tables

Revision ID: f7a9c2d4e6b8
Revises: d5c8e2f7a1b4
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa


revision = "f7a9c2d4e6b8"
down_revision = "d5c8e2f7a1b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "storyboard_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "base_prompt",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
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
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_storyboard_projects_user_created",
        "storyboard_projects",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_storyboard_projects_product_id",
        "storyboard_projects",
        ["product_id"],
    )

    op.create_table(
        "storyboard_frames",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("clip_type", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("photo", sa.Text(), nullable=False),
        sa.Column("transform_prompt", sa.Text(), nullable=False),
        sa.Column("voiceover", sa.Text(), nullable=False),
        sa.Column("video_prompt", sa.Text(), nullable=False),
        sa.Column("thumbnail_filename", sa.String(length=255), nullable=True),
        sa.Column("thumbnail_mime_type", sa.String(length=100), nullable=True),
        sa.Column(
            "thumbnail_storage_path",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column("thumbnail_width", sa.Integer(), nullable=True),
        sa.Column("thumbnail_height", sa.Integer(), nullable=True),
        sa.Column("thumbnail_file_size_bytes", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["storyboard_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "sort_order",
            name="uq_storyboard_frames_project_sort_order",
        ),
    )
    op.create_index(
        "ix_storyboard_frames_project_id",
        "storyboard_frames",
        ["project_id"],
    )


def downgrade():
    op.drop_index(
        "ix_storyboard_frames_project_id",
        table_name="storyboard_frames",
    )
    op.drop_table("storyboard_frames")

    op.drop_index(
        "ix_storyboard_projects_product_id",
        table_name="storyboard_projects",
    )
    op.drop_index(
        "ix_storyboard_projects_user_created",
        table_name="storyboard_projects",
    )
    op.drop_table("storyboard_projects")
