"""GitHub observations share AI queue and monetary allowance; no synthetic orders."""

from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0008_github_reviews"
down_revision = "0007_github_reads"
branch_labels = None
depends_on = None


@contextmanager
def rebuild_tables():
    # SQLite must rebuild referenced tables to alter nullability; PostgreSQL uses ALTER.
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        with op.get_context().autocommit_block():
            op.get_bind().exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        yield
    finally:
        if sqlite:
            with op.get_context().autocommit_block():
                violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                op.get_bind().exec_driver_sql("PRAGMA foreign_keys=ON")
                if violations:
                    raise RuntimeError("Migration integrity check failed")


def upgrade():
    with rebuild_tables():
        upgrade_tables()


def upgrade_tables():
    with op.batch_alter_table("ai_jobs") as batch:
        batch.alter_column("order_id", existing_type=sa.String(36), nullable=True)
        batch.create_check_constraint(
            "ck_ai_job_subject",
            "(kind = 'github_review' AND order_id IS NULL) OR (kind <> 'github_review' AND order_id IS NOT NULL)",
        )
        batch.create_index(
            "uq_github_review_request",
            ["request_id"],
            unique=True,
            sqlite_where=sa.text("kind = 'github_review'"),
            postgresql_where=sa.text("kind = 'github_review'"),
        )
    with op.batch_alter_table("evidence_proposals") as batch:
        batch.add_column(sa.Column("review_job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("review_item", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_proposal_review_job", "ai_jobs", ["review_job_id"], ["id"])
        batch.create_unique_constraint("uq_review_observation", ["review_job_id", "review_item"])


def downgrade():
    # A review job has no order: never silently erase it to satisfy the old schema.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM ai_jobs WHERE order_id IS NULL")):
        raise RuntimeError("GitHub reviews exist; downgrade would lose data")
    with rebuild_tables():
        downgrade_tables()


def downgrade_tables():
    with op.batch_alter_table("evidence_proposals") as batch:
        batch.drop_constraint("uq_review_observation", type_="unique")
        batch.drop_constraint("fk_proposal_review_job", type_="foreignkey")
        batch.drop_column("review_item")
        batch.drop_column("review_job_id")
    with op.batch_alter_table("ai_jobs") as batch:
        batch.drop_index("uq_github_review_request")
        batch.drop_constraint("ck_ai_job_subject", type_="check")
        batch.alter_column("order_id", existing_type=sa.String(36), nullable=False)
