"""Owner-confirmed order lifecycle and bounded completion evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0006_order_lifecycle"
down_revision = "0005_production_ai_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_outcomes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        *[
            sa.Column(k, sa.Text(), nullable=False)
            for k in ["delivered", "validation", "excluded", "learned"]
        ],
        sa.Column("capability_ids", sa.JSON(), nullable=False),
        sa.Column("finished_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "revision", name="uq_order_outcome_revision"),
    )
    op.create_index("ix_order_outcomes_order_id", "order_outcomes", ["order_id"])
    op.create_table(
        "order_work",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("acceptance_snapshot", sa.JSON(), nullable=False),
        *[sa.Column(k, sa.Text(), nullable=False) for k in ["agreed_scope", "agreed_stack", "terms"]],
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("outcome_id", sa.String(36), sa.ForeignKey("order_outcomes.id"), nullable=True),
    )
    op.create_table(
        "completion_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column(
            "outcome_id", sa.String(36), sa.ForeignKey("order_outcomes.id"), nullable=False, unique=True
        ),
        sa.Column("base_revision", sa.Integer(), sa.ForeignKey("profile_revisions.id"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("capability_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("accepted_revision", sa.Integer(), sa.ForeignKey("profile_revisions.id"), nullable=True),
        sa.Column("revoked_revision", sa.Integer(), sa.ForeignKey("profile_revisions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_completion_proposals_order_id", "completion_proposals", ["order_id"])


def downgrade():
    op.drop_table("completion_proposals")
    op.drop_table("order_work")
    op.drop_table("order_outcomes")
