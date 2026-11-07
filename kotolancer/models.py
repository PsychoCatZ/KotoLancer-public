from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class ProfileRevision(Base):
    __tablename__ = "profile_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(50), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProfileHead(Base):
    __tablename__ = "profile_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("profile_revisions.id"))
    seed_id: Mapped[str] = mapped_column(String(100))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(100), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    materials_revision: Mapped[int] = mapped_column(Integer, default=0)
    conversation_revision: Mapped[int] = mapped_column(Integer, default=0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __mapper_args__ = {"version_id_col": version}


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20))
    role: Mapped[str] = mapped_column(String(30), default="brief")
    text: Mapped[str] = mapped_column(Text, default="")
    file_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    extraction_status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaterialExtract(Base):
    __tablename__ = "material_extracts"
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), primary_key=True)
    blocks: Mapped[list] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON)
    processor: Mapped[str] = mapped_column(String(50), default="local-v1")


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AIJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        UniqueConstraint("order_id", "request_id", name="uq_ai_job_request"),
        CheckConstraint(
            "(kind = 'github_review' AND order_id IS NULL) OR (kind <> 'github_review' AND order_id IS NOT NULL)",
            name="ck_ai_job_subject",
        ),
        Index(
            "uq_github_review_request",
            "request_id",
            unique=True,
            sqlite_where=text("kind = 'github_review'"),
            postgresql_where=text("kind = 'github_review'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True, nullable=True)
    request_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
    context: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIRun(Base):
    __tablename__ = "ai_runs"
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    rules_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    reserved_nusd: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    counted_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id"), unique=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderMessage(Base):
    __tablename__ = "order_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id"))
    author: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReplyDraft(Base):
    __tablename__ = "reply_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id"), unique=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __mapper_args__ = {"version_id_col": version}


class AIWorkerState(Base):
    __tablename__ = "ai_worker_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramCursor(Base):
    __tablename__ = "telegram_cursors"
    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    next_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"
    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GitHubSnapshot(Base):
    __tablename__ = "github_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    repository: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str] = mapped_column(String(40))
    facts: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GitHubReadJob(Base):
    __tablename__ = "github_read_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(36), unique=True)
    repository: Mapped[str] = mapped_column(String(100))
    source_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("github_snapshots.id"), nullable=True)
    paths: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    lease: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("github_snapshots.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceProposal(Base):
    __tablename__ = "evidence_proposals"
    __table_args__ = (UniqueConstraint("review_job_id", "review_item", name="uq_review_observation"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("github_snapshots.id"))
    review_job_id: Mapped[str | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    review_item: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_revision: Mapped[int] = mapped_column(ForeignKey("profile_revisions.id"))
    statement: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    capability_ids: Mapped[list] = mapped_column(JSON)
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    accepted_revision: Mapped[int | None] = mapped_column(ForeignKey("profile_revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrowserLogin(Base):
    __tablename__ = "browser_logins"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    browser_hash: Mapped[str] = mapped_column(String(64), unique=True)
    approval_hash: Mapped[str] = mapped_column(String(64), unique=True)
    binding: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderOutcome(Base):
    __tablename__ = "order_outcomes"
    __table_args__ = (UniqueConstraint("order_id", "revision", name="uq_order_outcome_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(20))
    delivered: Mapped[str] = mapped_column(Text)
    validation: Mapped[str] = mapped_column(Text)
    excluded: Mapped[str] = mapped_column(Text, default="")
    learned: Mapped[str] = mapped_column(Text, default="")
    capability_ids: Mapped[list] = mapped_column(JSON)
    finished_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderWork(Base):
    __tablename__ = "order_work"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    acceptance_snapshot: Mapped[dict] = mapped_column(JSON)
    agreed_scope: Mapped[str] = mapped_column(Text, default="")
    agreed_stack: Mapped[str] = mapped_column(Text, default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome_id: Mapped[str | None] = mapped_column(ForeignKey("order_outcomes.id"), nullable=True)


class CompletionProposal(Base):
    __tablename__ = "completion_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    outcome_id: Mapped[str] = mapped_column(ForeignKey("order_outcomes.id"), unique=True)
    base_revision: Mapped[int] = mapped_column(ForeignKey("profile_revisions.id"))
    statement: Mapped[str] = mapped_column(Text)
    capability_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    accepted_revision: Mapped[int | None] = mapped_column(ForeignKey("profile_revisions.id"), nullable=True)
    revoked_revision: Mapped[int | None] = mapped_column(ForeignKey("profile_revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
