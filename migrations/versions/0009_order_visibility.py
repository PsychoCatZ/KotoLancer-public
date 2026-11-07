"""Archive and reversible order deletion preserve history and AI spending."""

import sqlalchemy as sa
from alembic import op

revision = "0009_order_visibility"
down_revision = "0008_github_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Both production PostgreSQL and supported SQLite support DROP COLUMN;
    # rebuilding a referenced orders table would break SQLite foreign keys.
    op.drop_column("orders", "deleted_at")
    op.drop_column("orders", "archived_at")
