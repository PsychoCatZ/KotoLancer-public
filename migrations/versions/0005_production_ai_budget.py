"""Durable upper-bound reservation before production generation."""

import sqlalchemy as sa
from alembic import op

revision = "0005_production_ai_budget"
down_revision = "0004_browser_login"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_runs", sa.Column("reserved_nusd", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("ai_runs", sa.Column("counted_input_tokens", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("ai_runs", "counted_input_tokens")
    op.drop_column("ai_runs", "reserved_nusd")
