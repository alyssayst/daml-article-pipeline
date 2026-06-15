"""Add user and claim columns

Revision ID: 002
Revises: 001
Create Date: 2026-04-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # articles: track who submitted
    op.add_column("articles", sa.Column("created_by", UUID(as_uuid=True), nullable=True))

    # translation_jobs: claim and ownership columns
    op.add_column("translation_jobs", sa.Column("claimed_by", UUID(as_uuid=True), nullable=True))
    op.add_column("translation_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("translation_jobs", sa.Column("translated_by", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_translation_jobs_claimed_by", "translation_jobs", ["claimed_by"])

    # audit_logs: track who performed the action
    op.add_column("audit_logs", sa.Column("user_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "user_id")
    op.drop_index("ix_translation_jobs_claimed_by", table_name="translation_jobs")
    op.drop_column("translation_jobs", "translated_by")
    op.drop_column("translation_jobs", "claimed_at")
    op.drop_column("translation_jobs", "claimed_by")
    op.drop_column("articles", "created_by")