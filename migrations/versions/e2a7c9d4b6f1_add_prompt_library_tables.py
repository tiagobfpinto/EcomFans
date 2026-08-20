"""add prompt library tables

Revision ID: e2a7c9d4b6f1
Revises: b8f1d3a6c2e9
Create Date: 2026-06-22

"""
from alembic import op
import sqlalchemy as sa


revision = "e2a7c9d4b6f1"
down_revision = "b8f1d3a6c2e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prompt_library_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prompt_library_targets_user_type_name",
        "prompt_library_targets",
        ["user_id", "target_type", "name"],
    )
    op.create_index(
        "ix_prompt_library_targets_product_id",
        "prompt_library_targets",
        ["product_id"],
    )

    op.create_table(
        "prompt_library_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["target_id"], ["prompt_library_targets.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prompt_library_items_user_created",
        "prompt_library_items",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_prompt_library_items_target_id",
        "prompt_library_items",
        ["target_id"],
    )

    op.create_table(
        "prompt_library_thumbnails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompt_library_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prompt_library_thumbnails_prompt_id",
        "prompt_library_thumbnails",
        ["prompt_id"],
    )


def downgrade():
    op.drop_index(
        "ix_prompt_library_thumbnails_prompt_id",
        table_name="prompt_library_thumbnails",
    )
    op.drop_table("prompt_library_thumbnails")

    op.drop_index(
        "ix_prompt_library_items_target_id",
        table_name="prompt_library_items",
    )
    op.drop_index(
        "ix_prompt_library_items_user_created",
        table_name="prompt_library_items",
    )
    op.drop_table("prompt_library_items")

    op.drop_index(
        "ix_prompt_library_targets_product_id",
        table_name="prompt_library_targets",
    )
    op.drop_index(
        "ix_prompt_library_targets_user_type_name",
        table_name="prompt_library_targets",
    )
    op.drop_table("prompt_library_targets")
