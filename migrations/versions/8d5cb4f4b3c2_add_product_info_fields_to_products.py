"""add product info fields to products

Revision ID: 8d5cb4f4b3c2
Revises: c51b8a5e6bab
Create Date: 2026-03-06 14:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8d5cb4f4b3c2"
down_revision = "c51b8a5e6bab"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("price", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("offer", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("benefits", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_column("benefits")
        batch_op.drop_column("offer")
        batch_op.drop_column("price")
