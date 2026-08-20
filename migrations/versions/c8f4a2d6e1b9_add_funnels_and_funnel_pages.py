"""add funnels and funnel pages

Revision ID: c8f4a2d6e1b9
Revises: b4e8c2f7a9d1
Create Date: 2026-08-20 18:30:00.000000

"""
import sqlalchemy as sa
from alembic import op


revision = "c8f4a2d6e1b9"
down_revision = "b4e8c2f7a9d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "funnels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_funnels_user_updated", "funnels", ["user_id", "updated_at"])

    op.create_table(
        "funnel_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("funnel_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("page_type", sa.String(length=30), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "revision", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["funnel_id"], ["funnels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_funnel_pages_slug"),
    )
    op.create_index(
        "ix_funnel_pages_funnel_order",
        "funnel_pages",
        ["funnel_id", "sort_order"],
    )
    op.create_index(
        "ix_funnel_pages_status_slug", "funnel_pages", ["status", "slug"]
    )


def downgrade():
    op.drop_index("ix_funnel_pages_status_slug", table_name="funnel_pages")
    op.drop_index("ix_funnel_pages_funnel_order", table_name="funnel_pages")
    op.drop_table("funnel_pages")
    op.drop_index("ix_funnels_user_updated", table_name="funnels")
    op.drop_table("funnels")
