"""Owner-approved browser login and revocable opaque sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0004_browser_login"
down_revision = "0003_integrations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_logins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("approval_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("browser_sessions")
    op.drop_table("browser_logins")
