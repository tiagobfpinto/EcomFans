"""add worker jobs queue

Revision ID: 9b8d1e4f2a10
Revises: 1f2a9b7c3d44
Create Date: 2026-03-08 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9b8d1e4f2a10"
down_revision = "1f2a9b7c3d44"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("queue_name", sa.String(length=50), nullable=False, server_default="default"),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_token", sa.String(length=64), nullable=True),
        sa.Column("worker_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worker_jobs_status_queue_available", "worker_jobs", ["status", "queue_name", "available_at"])
    op.create_index("ix_worker_jobs_user_id", "worker_jobs", ["user_id"])
    op.create_index("ix_worker_jobs_job_type", "worker_jobs", ["job_type"])


def downgrade():
    op.drop_index("ix_worker_jobs_job_type", table_name="worker_jobs")
    op.drop_index("ix_worker_jobs_user_id", table_name="worker_jobs")
    op.drop_index("ix_worker_jobs_status_queue_available", table_name="worker_jobs")
    op.drop_table("worker_jobs")
