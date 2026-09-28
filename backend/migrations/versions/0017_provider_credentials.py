"""Introduce the unified provider credential table.

Replaces the scattered ``settings``-key based credential state
(``yuque-api.verified`` / ``yuque-web.connected`` / ``feishu.verified``) with
a single ``provider_credentials`` table keyed by ``(provider, channel)``.
Secrets stay in the platform keychain; this table stores only non-secret
state plus the keychain entry name (``secret_ref``).

Existing settings keys are backfilled into the table and left in place
(read-only compatibility); the API layer now writes through the new table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.models import utc_timestamp_server_default

revision = "0017_provider_credentials"
down_revision = "0016_remote_provider_decoupling"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_credentials",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column(
            "state",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
        sa.Column("account_label", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.String(128), nullable=True),
        sa.Column(
            "updated_at",
            sa.String(40),
            nullable=False,
            server_default=utc_timestamp_server_default(),
        ),
        sa.CheckConstraint(
            "state IN ('verified','unverified','disconnected')",
            name="ck_provider_credentials_state",
        ),
    )

    # --- Backfill from the legacy settings keys ---------------------------
    # Yuque API token: verified flag + account label + keychain ref.
    op.execute(
        """
        INSERT INTO provider_credentials (provider, channel, state, account_label, secret_ref)
        SELECT 'yuque', 'api',
               CASE WHEN v.value = 'true' THEN 'verified' ELSE 'unverified' END,
               NULLIF(l.value, ''),
               'yuque-api:token'
        FROM settings v
        LEFT JOIN settings l ON l.key = 'yuque-api.account-label'
        WHERE v.key = 'yuque-api.verified'
        """
    )
    # Yuque web (browser) channel: connected flag recorded by the login flow.
    op.execute(
        """
        INSERT INTO provider_credentials (provider, channel, state)
        SELECT 'yuque', 'web',
               CASE WHEN value = 'true' THEN 'verified' ELSE 'disconnected' END
        FROM settings
        WHERE key = 'yuque-web.connected'
        """
    )
    # Feishu webhook binding (notification channel, not a KB provider).
    op.execute(
        """
        INSERT INTO provider_credentials (provider, channel, state, secret_ref)
        SELECT 'feishu', 'webhook',
               CASE WHEN value = 'true' THEN 'verified' ELSE 'unverified' END,
               'feishu:webhook'
        FROM settings
        WHERE key = 'feishu.verified'
        """
    )


def downgrade():
    op.drop_table("provider_credentials")
