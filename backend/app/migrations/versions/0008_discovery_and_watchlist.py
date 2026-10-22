"""discovery runs and company watchlist

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19 10:00:00

Also resets old approval-queue rows that never got a screen or tailored CV back
to `matched`, so the new inbox doesn't show CV-less cards; discovery re-screens
them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'watchlist_companies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('ats', sa.String(length=32), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('careers_url', sa.String(length=2048), nullable=True),
        sa.Column('last_polled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'ats', 'slug', name='uq_watchlist_board'),
    )
    with op.batch_alter_table('watchlist_companies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_watchlist_companies_user_id'), ['user_id'], unique=False)

    op.create_table(
        'discovery_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('found', sa.Integer(), nullable=False),
        sa.Column('new', sa.Integer(), nullable=False),
        sa.Column('screened', sa.Integer(), nullable=False),
        sa.Column('kept', sa.Integer(), nullable=False),
        sa.Column('sent', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('discovery_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_discovery_runs_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_discovery_runs_started_at'), ['started_at'], unique=False)

    op.execute(
        "UPDATE applications SET status = 'matched' "
        "WHERE status = 'awaiting_approval' AND tailoring IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table('discovery_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_discovery_runs_started_at'))
        batch_op.drop_index(batch_op.f('ix_discovery_runs_user_id'))
    op.drop_table('discovery_runs')
    with op.batch_alter_table('watchlist_companies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_watchlist_companies_user_id'))
    op.drop_table('watchlist_companies')
