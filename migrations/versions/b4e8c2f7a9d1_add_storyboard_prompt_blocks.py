"""add storyboard prompt blocks

Revision ID: b4e8c2f7a9d1
Revises: a1c9e7d5b3f8
Create Date: 2026-08-18 01:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


revision = "b4e8c2f7a9d1"
down_revision = "a1c9e7d5b3f8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("storyboard_projects") as batch_op:
        batch_op.add_column(
            sa.Column(
                "prompt_blocks_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade():
    with op.batch_alter_table("storyboard_projects") as batch_op:
        batch_op.drop_column("prompt_blocks_json")
