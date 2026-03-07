"""add filesystem storage paths for product and inspiration images

Revision ID: e4f1a5d9c2b7
Revises: d2d8c4e1c7a3
Create Date: 2026-03-07 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e4f1a5d9c2b7"
down_revision = "d2d8c4e1c7a3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("product_images", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_path", sa.String(length=500), nullable=True))
        batch_op.alter_column("image_data", existing_type=sa.Text(), nullable=True)

    with op.batch_alter_table("creative_inspirations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_path", sa.String(length=500), nullable=True))
        batch_op.alter_column("image_data", existing_type=sa.Text(), nullable=True)


def downgrade():
    op.execute("UPDATE product_images SET image_data = '' WHERE image_data IS NULL")
    op.execute("UPDATE creative_inspirations SET image_data = '' WHERE image_data IS NULL")

    with op.batch_alter_table("creative_inspirations", schema=None) as batch_op:
        batch_op.alter_column("image_data", existing_type=sa.Text(), nullable=False)
        batch_op.drop_column("storage_path")

    with op.batch_alter_table("product_images", schema=None) as batch_op:
        batch_op.alter_column("image_data", existing_type=sa.Text(), nullable=False)
        batch_op.drop_column("storage_path")
