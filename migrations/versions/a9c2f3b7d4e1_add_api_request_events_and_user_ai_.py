"""add api request events and user ai settings

Revision ID: a9c2f3b7d4e1
Revises: e4f1a5d9c2b7
Create Date: 2026-03-07 18:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9c2f3b7d4e1"
down_revision = "e4f1a5d9c2b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "default_gemini_image_model",
                sa.String(length=120),
                nullable=True,
                server_default="gemini-2.5-flash-image",
            )
        )
        batch_op.add_column(
            sa.Column(
                "batch_api_for_queued_jobs",
                sa.Boolean(),
                nullable=True,
                server_default=sa.false(),
            )
        )

    op.execute(
        "UPDATE users SET default_gemini_image_model = 'gemini-2.5-flash-image' "
        "WHERE default_gemini_image_model IS NULL"
    )
    op.execute(
        "UPDATE users SET batch_api_for_queued_jobs = FALSE "
        "WHERE batch_api_for_queued_jobs IS NULL"
    )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "default_gemini_image_model",
            existing_type=sa.String(length=120),
            nullable=False,
        )
        batch_op.alter_column(
            "batch_api_for_queued_jobs",
            existing_type=sa.Boolean(),
            nullable=False,
        )

    op.create_table(
        "api_request_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feature", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("traffic_type", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("input_image_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("images_generated", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("estimated_cost_eur", sa.Numeric(12, 6), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("api_request_events")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("batch_api_for_queued_jobs")
        batch_op.drop_column("default_gemini_image_model")
