"""rework prompt library targets

Revision ID: f3b1c8d2e4a9
Revises: e2a7c9d4b6f1
Create Date: 2026-06-22

"""
from alembic import op
import sqlalchemy as sa


revision = "f3b1c8d2e4a9"
down_revision = "e2a7c9d4b6f1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("prompt_library_targets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))

    with op.batch_alter_table("prompt_library_items", schema=None) as batch_op:
        batch_op.alter_column(
            "target_id",
            existing_type=sa.Integer(),
            nullable=True,
        )

    op.create_table(
        "prompt_library_target_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["target_id"], ["prompt_library_targets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prompt_library_target_images_target_id",
        "prompt_library_target_images",
        ["target_id"],
    )


def downgrade():
    op.drop_index(
        "ix_prompt_library_target_images_target_id",
        table_name="prompt_library_target_images",
    )
    op.drop_table("prompt_library_target_images")

    op.execute(
        """
        DELETE FROM prompt_library_items
        WHERE target_id IS NULL
        """
    )
    with op.batch_alter_table("prompt_library_items", schema=None) as batch_op:
        batch_op.alter_column(
            "target_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    with op.batch_alter_table("prompt_library_targets", schema=None) as batch_op:
        batch_op.drop_column("description")
