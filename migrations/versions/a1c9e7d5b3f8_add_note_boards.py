"""add note boards

Revision ID: a1c9e7d5b3f8
Revises: f7a9c2d4e6b8
Create Date: 2026-08-02

"""
from alembic import op
import sqlalchemy as sa


revision = "a1c9e7d5b3f8"
down_revision = "f7a9c2d4e6b8"
branch_labels = None
depends_on = None


EMPTY_DOCUMENT = '{"schema_version":1,"viewport":{"x":0,"y":0,"zoom":1},"objects":[]}'


def upgrade():
    op.create_table(
        "note_boards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "document_json",
            sa.Text(),
            nullable=False,
            server_default=EMPTY_DOCUMENT,
        ),
        sa.Column(
            "object_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_note_boards_user_updated", "note_boards", ["user_id", "updated_at"]
    )


def downgrade():
    op.drop_index("ix_note_boards_user_updated", table_name="note_boards")
    op.drop_table("note_boards")
