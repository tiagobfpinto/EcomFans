"""add generated media storage paths

Revision ID: 1f2a9b7c3d44
Revises: a9c2f3b7d4e1
Create Date: 2026-03-07 20:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f2a9b7c3d44"
down_revision = "a9c2f3b7d4e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("image_generations", sa.Column("storage_path", sa.String(length=500), nullable=True))
    op.add_column("avatar_results", sa.Column("before_storage_path", sa.String(length=500), nullable=True))
    op.add_column("avatar_results", sa.Column("after_storage_path", sa.String(length=500), nullable=True))
    op.add_column("creative_results", sa.Column("generated_storage_path", sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column("creative_results", "generated_storage_path")
    op.drop_column("avatar_results", "after_storage_path")
    op.drop_column("avatar_results", "before_storage_path")
    op.drop_column("image_generations", "storage_path")
