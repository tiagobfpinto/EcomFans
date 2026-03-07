"""add billing plans, credit ledger, and mock payments

Revision ID: b7a1d2f9c3e4
Revises: c51b8a5e6bab
Create Date: 2026-03-06 18:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7a1d2f9c3e4"
down_revision = "c51b8a5e6bab"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extra_credits", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("plan_tier", sa.String(length=20), nullable=True, server_default="free"))
        batch_op.add_column(sa.Column("next_credit_reset_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("credits", existing_type=sa.Integer(), server_default="30")

    op.execute("UPDATE users SET plan_tier = 'free' WHERE plan_tier IS NULL")
    op.execute("UPDATE users SET extra_credits = 0 WHERE extra_credits IS NULL")
    op.execute("UPDATE users SET next_credit_reset_at = NOW() + INTERVAL '30 days' WHERE next_credit_reset_at IS NULL")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("plan_tier", existing_type=sa.String(length=20), nullable=False)
        batch_op.alter_column("extra_credits", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("next_credit_reset_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("monthly_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("monthly_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("feature", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "mock_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("payment_type", sa.String(length=20), nullable=False),
        sa.Column("plan_tier", sa.String(length=20), nullable=True),
        sa.Column("pack_id", sa.String(length=50), nullable=True),
        sa.Column("amount_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="EUR"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="paid"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("credits_charged", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("usage_events")
    op.drop_table("mock_payments")
    op.drop_table("credit_ledger")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("credits", existing_type=sa.Integer(), server_default="10")
        batch_op.drop_column("next_credit_reset_at")
        batch_op.drop_column("plan_tier")
        batch_op.drop_column("extra_credits")
