"""Add batches table

Revision ID: 003
Revises: 002
Create Date: 2026-04-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("articles", JSONB(), nullable=False),
        sa.Column("columns", JSONB(), nullable=True),
        sa.Column("filter_results", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batches_user_id", "batches", ["user_id"])
    op.create_index("ix_batches_expires_at", "batches", ["expires_at"])


def downgrade() -> None:
    op.drop_table("batches")