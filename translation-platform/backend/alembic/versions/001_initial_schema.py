"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Articles
    op.create_table(
        "articles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False, unique=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("source_body", sa.Text(), nullable=True),
        sa.Column("extraction_metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # source_url already has a unique index from unique=True

    # Translation Jobs
    op.create_table(
        "translation_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("source_lang", sa.String(5), nullable=False, server_default="EN"),
        sa.Column("target_lang", sa.String(5), nullable=False, server_default="JA"),
        sa.Column("ai_title", sa.Text(), nullable=True),
        sa.Column("ai_body", sa.Text(), nullable=True),
        sa.Column("draft_title", sa.Text(), nullable=True),
        sa.Column("draft_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("deepl_chars_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_translation_jobs_status", "translation_jobs", ["status"])
    op.create_index("ix_translation_jobs_article_id", "translation_jobs", ["article_id"])

    # Final Translations
    op.create_table(
        "final_translations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("translation_jobs.id"), nullable=False),
        sa.Column("final_title", sa.Text(), nullable=False),
        sa.Column("final_body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_final_translations_job_id", "final_translations", ["job_id"])

    # Audit Logs
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("final_translations")
    op.drop_table("translation_jobs")
    op.drop_table("articles")