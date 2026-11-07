"""Durable bounded GitHub reads and exact evidence locations."""

import sqlalchemy as sa
from alembic import op

revision = "0007_github_reads"
down_revision = "0006_order_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "evidence_proposals", sa.Column("citations", sa.JSON(), nullable=False, server_default="[]")
    )
    op.create_table(
        "github_read_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(36), nullable=False, unique=True),
        sa.Column("repository", sa.String(100), nullable=False),
        sa.Column("source_snapshot_id", sa.String(36), sa.ForeignKey("github_snapshots.id")),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("lease", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("github_snapshots.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_github_read_jobs_status", "github_read_jobs", ["status"])


def downgrade():
    op.drop_table("github_read_jobs")
    with op.batch_alter_table("evidence_proposals") as batch:
        batch.drop_column("citations")
