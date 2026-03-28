"""add stripe fields to users

Revision ID: e8b3f2a7c1d9
Revises: c4e2f1a8b9d3
Create Date: 2026-03-09 01:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e8b3f2a7c1d9"
down_revision = "c4e2f1a8b9d3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stripe_customer_id", sa.String(length=60), nullable=True)
        )
        batch_op.add_column(
            sa.Column("stripe_subscription_id", sa.String(length=60), nullable=True)
        )
        batch_op.add_column(
            sa.Column("stripe_subscription_status", sa.String(length=30), nullable=True)
        )
        batch_op.create_unique_constraint("uq_users_stripe_customer_id", ["stripe_customer_id"])

    op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"])


def downgrade():
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_stripe_customer_id", type_="unique")
        batch_op.drop_column("stripe_subscription_status")
        batch_op.drop_column("stripe_subscription_id")
        batch_op.drop_column("stripe_customer_id")
