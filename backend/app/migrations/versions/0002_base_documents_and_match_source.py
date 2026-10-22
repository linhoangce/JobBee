"""Base vs. tailored documents; keep LLM match scores on re-rank.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_base", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(sa.Column("application_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("pdf_path", sa.String(length=1024), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_documents_application_id"), ["application_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_documents_application_id",
            "applications",
            ["application_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("match_source", sa.String(length=16), nullable=True))

    # Tailored copies were only identifiable by label before; anything an
    # application points at is tailored too.
    op.execute(
        """
        UPDATE documents SET is_base = 0
        WHERE id IN (SELECT resume_doc_id FROM applications WHERE resume_doc_id IS NOT NULL)
           OR id IN (SELECT cover_doc_id FROM applications WHERE cover_doc_id IS NOT NULL)
           OR lower(label) LIKE 'tailored resume%'
           OR label LIKE 'Cover letter —%'
        """
    )
    op.execute(
        """
        UPDATE documents SET application_id = (
            SELECT a.id FROM applications a
            WHERE a.resume_doc_id = documents.id OR a.cover_doc_id = documents.id
            ORDER BY a.id LIMIT 1
        )
        WHERE is_base = 0
        """
    )
    # Keyword scores and unparseable-LLM placeholders get refreshed on the next
    # run; everything else came from the model and is kept.
    op.execute(
        """
        UPDATE jobs SET match_source = CASE
            WHEN match_score IS NULL THEN NULL
            WHEN match_reason LIKE 'Matched % keyword%'
              OR match_reason LIKE 'Limited overlap%'
              OR match_reason LIKE 'Could not parse model score%'
              OR match_reason LIKE 'Invalid model score%'
              THEN 'heuristic'
            ELSE 'llm'
        END
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("match_source")

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_constraint("fk_documents_application_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_documents_application_id"))
        batch_op.drop_column("pdf_path")
        batch_op.drop_column("application_id")
        batch_op.drop_column("is_base")
