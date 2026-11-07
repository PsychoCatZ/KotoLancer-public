"""Persistent consultant queue, provenance, analysis, chat and drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0002_consultant"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders", sa.Column("conversation_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("order_id", "request_id", name="uq_ai_job_request"),
    )
    op.create_index("ix_ai_jobs_order_id", "ai_jobs", ["order_id"])
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"])
    op.create_table(
        "ai_runs",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("ai_jobs.id"), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("rules_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("response_id", sa.String(150), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("ai_jobs.id"), nullable=False, unique=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analyses_order_id", "analyses", ["order_id"])
    op.create_table(
        "order_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("ai_jobs.id"), nullable=False),
        sa.Column("author", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_order_messages_order_id", "order_messages", ["order_id"])
    op.create_table(
        "reply_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("ai_jobs.id"), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reply_drafts_order_id", "reply_drafts", ["order_id"])
    op.create_table(
        "ai_worker_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for name in ("ai_worker_state", "reply_drafts", "order_messages", "analyses", "ai_runs", "ai_jobs"):
        op.drop_table(name)
    op.drop_column("orders", "conversation_revision")
