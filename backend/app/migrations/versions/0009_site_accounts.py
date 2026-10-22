"""site accounts the apply agent made, and attended apply runs

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20 12:00:00

Passwords are not stored here: they live in the OS keyring. This table is the
index of which sites have one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'site_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('login', sa.String(length=320), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'host', name='uq_site_account_host'),
    )
    with op.batch_alter_table('site_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_site_accounts_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('apply_runs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('attended', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table('apply_runs', schema=None) as batch_op:
        batch_op.drop_column('attended')
    with op.batch_alter_table('site_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_site_accounts_user_id'))
    op.drop_table('site_accounts')
