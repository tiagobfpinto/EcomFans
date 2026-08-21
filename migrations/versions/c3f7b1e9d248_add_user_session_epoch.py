"""add session_epoch to users

Invalidates every previously issued session when a password changes, so a
stolen session cookie stops working the moment the owner resets their password.

Revision ID: c3f7b1e9d248
Revises: e6a8c1d4f9b2
Create Date: 2026-08-21 16:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c3f7b1e9d248"
down_revision = "e6a8c1d4f9b2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "session_epoch",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("session_epoch")
