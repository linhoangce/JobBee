"""Move inbox credentials out of SQLite into the secret store.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18

Copies each user's Gmail refresh token / IMAP app password and the Gmail OAuth
client secret into `services.secrets` (OS keyring locally), then drops the
plaintext columns. If the store write fails the transaction rolls back and the
columns stay intact.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.services.secrets import (
        GMAIL_CLIENT_SECRET,
        GMAIL_REFRESH_TOKEN,
        IMAP_APP_PASSWORD,
        get_secret_store,
        set_user_secret,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT user_id, gmail_refresh_token, imap_app_password FROM email_sync_states"
        )
    ).fetchall()
    for user_id, refresh_token, imap_password in rows:
        if refresh_token and refresh_token.strip():
            set_user_secret(user_id, GMAIL_REFRESH_TOKEN, refresh_token)
        if imap_password and imap_password.strip():
            set_user_secret(user_id, IMAP_APP_PASSWORD, imap_password)

    client_secret = bind.execute(
        sa.text("SELECT value FROM app_config WHERE key = 'gmail_client_secret'")
    ).scalar()
    if client_secret and client_secret.strip():
        get_secret_store().set(GMAIL_CLIENT_SECRET, client_secret.strip())
    op.execute("DELETE FROM app_config WHERE key = 'gmail_client_secret'")

    with op.batch_alter_table("email_sync_states", schema=None) as batch_op:
        batch_op.drop_column("gmail_refresh_token")
        batch_op.drop_column("imap_app_password")


def downgrade() -> None:
    # Secrets stay in the store; reconnect the inbox after downgrading.
    with op.batch_alter_table("email_sync_states", schema=None) as batch_op:
        batch_op.add_column(sa.Column("imap_app_password", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("gmail_refresh_token", sa.Text(), nullable=True))
