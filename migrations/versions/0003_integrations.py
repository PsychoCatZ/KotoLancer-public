"""Telegram inbox and read-only GitHub evidence proposals."""

import sqlalchemy as sa
from alembic import op

revision = "0003_integrations"
down_revision = "0002_consultant"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_cursors",
        sa.Column("bot_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "telegram_updates",
        sa.Column("bot_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("update_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("material_ids", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "github_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "evidence_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("github_snapshots.id"), nullable=False),
        sa.Column("base_revision", sa.Integer(), sa.ForeignKey("profile_revisions.id"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("capability_ids", sa.JSON(), nullable=False),
        sa.Column("project_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("accepted_revision", sa.Integer(), sa.ForeignKey("profile_revisions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in ("evidence_proposals", "github_snapshots", "telegram_updates", "telegram_cursors"):
        op.drop_table(table)
