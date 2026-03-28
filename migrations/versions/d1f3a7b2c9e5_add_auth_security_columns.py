"""add auth security columns to users

Revision ID: d1f3a7b2c9e5
Revises: b2d4f6a1c8e3
Create Date: 2026-03-09

Adds brute-force lockout, password reset rate limiting, and DB-stored
reset tokens to the users table.
"""
from alembic import op
import sqlalchemy as sa


revision = "d1f3a7b2c9e5"
down_revision = "b2d4f6a1c8e3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_password_reset_request_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reset_token", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reset_token_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_unique_constraint("uq_users_reset_token", ["reset_token"])

    op.create_index("ix_users_reset_token", "users", ["reset_token"])


def downgrade():
    op.drop_index("ix_users_reset_token", table_name="users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_reset_token", type_="unique")
        batch_op.drop_column("reset_token_expires_at")
        batch_op.drop_column("reset_token")
        batch_op.drop_column("last_password_reset_request_at")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
