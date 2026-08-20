"""add prompt library item name

Revision ID: a4d9e7b2c6f3
Revises: f3b1c8d2e4a9
Create Date: 2026-06-22

"""
from alembic import op
import sqlalchemy as sa


revision = "a4d9e7b2c6f3"
down_revision = "f3b1c8d2e4a9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("prompt_library_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "name",
                sa.String(length=160),
                nullable=False,
                server_default="Untitled prompt",
            )
        )

    op.execute(
        """
        UPDATE prompt_library_items
        SET name = LEFT(NULLIF(TRIM(prompt_text), ''), 160)
        WHERE name = 'Untitled prompt'
          AND NULLIF(TRIM(prompt_text), '') IS NOT NULL
        """
    )

    with op.batch_alter_table("prompt_library_items", schema=None) as batch_op:
        batch_op.alter_column("name", server_default=None)


def downgrade():
    with op.batch_alter_table("prompt_library_items", schema=None) as batch_op:
        batch_op.drop_column("name")
