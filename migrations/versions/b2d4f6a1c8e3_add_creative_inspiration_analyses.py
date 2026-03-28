"""add_creative_inspiration_analyses

Revision ID: b2d4f6a1c8e3
Revises: e8b3f2a7c1d9
Create Date: 2026-03-09

Cache table for AI vision analysis of creative inspiration images.
Prevents re-calling the vision API when the same inspiration+product
combination is generated again within 30 days.
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d4f6a1c8e3"
down_revision = "e8b3f2a7c1d9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "creative_inspiration_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inspiration_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["inspiration_id"], ["creative_inspirations.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_creative_inspiration_analyses_lookup",
        "creative_inspiration_analyses",
        ["inspiration_id", "product_id", "provider", "prompt_hash"],
    )


def downgrade():
    op.drop_index("ix_creative_inspiration_analyses_lookup", table_name="creative_inspiration_analyses")
    op.drop_table("creative_inspiration_analyses")
