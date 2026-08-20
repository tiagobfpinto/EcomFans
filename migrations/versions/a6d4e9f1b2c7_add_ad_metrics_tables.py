"""add ad metrics tables

Revision ID: a6d4e9f1b2c7
Revises: f2e4a8c1b7d6
Create Date: 2026-06-15

"""
from alembic import op
import sqlalchemy as sa


revision = "a6d4e9f1b2c7"
down_revision = "f2e4a8c1b7d6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_metrics_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "reported_ad_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "has_reported_total",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "total_spend",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_cpm",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_cpc",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_ctr",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_adds_to_cart",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_purchases",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_cost_per_purchase",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_roas",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_frequency",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "metric_date",
            name="uq_ad_metrics_snapshots_user_date",
        ),
    )
    op.create_index(
        "ix_ad_metrics_snapshots_user_date",
        "ad_metrics_snapshots",
        ["user_id", "metric_date"],
    )

    op.create_table(
        "ad_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("ad_name", sa.String(length=255), nullable=False),
        sa.Column(
            "spend",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cpm",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cpc",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "ctr",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "adds_to_cart",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "purchases",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_per_purchase",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "roas",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "frequency",
            sa.Numeric(precision=14, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["ad_metrics_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "ad_name", name="uq_ad_metrics_snapshot_ad"
        ),
    )
    op.create_index(
        "ix_ad_metrics_snapshot_id", "ad_metrics", ["snapshot_id"]
    )


def downgrade():
    op.drop_index("ix_ad_metrics_snapshot_id", table_name="ad_metrics")
    op.drop_table("ad_metrics")
    op.drop_index(
        "ix_ad_metrics_snapshots_user_date",
        table_name="ad_metrics_snapshots",
    )
    op.drop_table("ad_metrics_snapshots")
