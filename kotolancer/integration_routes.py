import copy
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from .github_catalog import CatalogBusy, RepositoryCatalog
from .github_evidence import CitationInput, citations_from_snapshot, location_url
from .github_reader import GitHubError
from .github_sources import eligible, validate_repository
from .models import (
    EvidenceProposal,
    GitHubReadJob,
    GitHubSnapshot,
    ProfileHead,
    ProfileRevision,
    TelegramCursor,
    TelegramUpdate,
    now,
)
from .profile import ConflictError, revise_profile
from .schemas import ProfileUpdate, StrictModel


class InspectRequest(StrictModel):
    repository: str = Field(min_length=1, max_length=100)
    request_key: UUID = Field(default_factory=uuid4)
    source_snapshot_id: str | None = None
    paths: list[str] = Field(default_factory=list, max_length=12)


class ProposalRequest(StrictModel):
    snapshot_id: str
    expected_revision: int = Field(ge=1)
    statement: str = Field(default="", max_length=3000)
    citations: list[CitationInput] = Field(default_factory=list, max_length=5)
    capability_ids: list[str] = Field(min_length=1, max_length=20)
    project_id: str | None = None


def integration_routes(session, settings, github_reader=None):
    router = APIRouter()
    catalog = RepositoryCatalog(settings, github_reader)

    @router.get("/github/repositories")
    def repositories():
        return catalog.view()

    @router.post("/github/repositories/refresh")
    def refresh_repositories():
        if not settings.github_token.get_secret_value() and github_reader is None:
            raise HTTPException(409, "Сначала подключи read-only доступ GitHub.")
        try:
            return catalog.refresh()
        except CatalogBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "Не удалось обновить список. Прежний результат сохранён.") from exc

    @router.get("/integrations")
    def status(db=Depends(session)):
        prefix = settings.telegram_bot_token.get_secret_value().partition(":")[0]
        bot_id = int(prefix) if prefix.isdecimal() else 0
        poll = db.get(TelegramCursor, bot_id)
        stamp = poll.last_poll_at if poll else None
        if stamp and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        failures = db.scalars(
            select(TelegramUpdate)
            .where(TelegramUpdate.status == "failed", TelegramUpdate.bot_id == bot_id)
            .order_by(TelegramUpdate.created_at.desc())
            .limit(10)
        ).all()
        return {
            "telegram_configured": bool(
                settings.telegram_owner_id and settings.telegram_bot_token.get_secret_value()
            ),
            "telegram_polling": bool(stamp and stamp > now() - timedelta(seconds=90)),
            "telegram_last_poll": stamp,
            "mini_app_url": settings.mini_app_url,
            "github_configured": bool(settings.github_token.get_secret_value()),
            "github_owner": settings.github_owner,
            "telegram_failures": [{"update_id": r.update_id, "error": r.error} for r in failures],
        }

    @router.get("/github/snapshots")
    def snapshots(db=Depends(session)):
        return [
            {
                "id": r.id,
                "repository": r.repository,
                "commit_sha": r.commit_sha,
                "facts": r.facts,
                "created_at": r.created_at,
            }
            for r in db.scalars(select(GitHubSnapshot).order_by(GitHubSnapshot.created_at.desc()).limit(20))
        ]

    @router.get("/github/snapshots/{snapshot_id}")
    def saved_snapshot(snapshot_id: str, db=Depends(session)):
        row = db.get(GitHubSnapshot, snapshot_id)
        if not row:
            raise HTTPException(404, "Снимок не найден.")
        return {k: getattr(row, k) for k in ["id", "repository", "commit_sha", "facts", "created_at"]}

    def job_view(row):
        return {
            k: getattr(row, k)
            for k in [
                "id",
                "repository",
                "source_snapshot_id",
                "paths",
                "status",
                "progress",
                "error",
                "snapshot_id",
                "created_at",
                "finished_at",
            ]
        }

    @router.get("/github/reads")
    def reads(db=Depends(session)):
        return [
            job_view(r)
            for r in db.scalars(select(GitHubReadJob).order_by(GitHubReadJob.created_at.desc()).limit(30))
        ]

    @router.post("/github/inspect", status_code=202)
    def inspect(data: InspectRequest, db=Depends(session)):
        if not settings.github_token.get_secret_value() and github_reader is None:
            raise HTTPException(409, "Сначала подключи read-only доступ GitHub.")
        try:
            validate_repository(settings.github_owner, data.repository)
        except GitHubError as exc:
            raise HTTPException(422, str(exc)) from exc
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        prior = db.scalar(select(GitHubReadJob).where(GitHubReadJob.request_key == str(data.request_key)))
        if prior:
            if (prior.repository, prior.source_snapshot_id, prior.paths) != (
                data.repository,
                data.source_snapshot_id,
                data.paths,
            ):
                raise ConflictError("Этот ключ запуска уже использован с другими файлами.")
            return job_view(prior)
        if data.source_snapshot_id:
            source = db.get(GitHubSnapshot, data.source_snapshot_id)
            if not source or source.repository != f"{settings.github_owner}/{data.repository}":
                raise HTTPException(422, "Выбери снимок этого репозитория.")
            available = {e["path"] for e in source.facts.get("available_files", [])}
            if (
                not data.paths
                or len(data.paths) != len(set(data.paths))
                or any(p not in available or not eligible(p) for p in data.paths)
            ):
                raise HTTPException(422, "Выбери до 12 разных файлов из сохранённого списка.")
        elif data.paths:
            raise HTTPException(422, "Сначала прочитай список файлов и сохрани точный коммит.")
        active = list(
            db.scalars(select(GitHubReadJob).where(GitHubReadJob.status.in_(["queued", "running"])))
        )
        if len(active) >= 10 or any(r.repository == data.repository for r in active):
            raise ConflictError(
                "Для репозитория уже есть чтение, либо очередь заполнена. Дождись результата или отмени его."
            )
        row = GitHubReadJob(
            request_key=str(data.request_key),
            repository=data.repository,
            source_snapshot_id=data.source_snapshot_id,
            paths=data.paths,
        )
        db.add(row)
        db.commit()
        return job_view(row)

    @router.post("/github/reads/{job_id}/cancel")
    def cancel(job_id: str, db=Depends(session)):
        row = db.scalar(select(GitHubReadJob).where(GitHubReadJob.id == job_id).with_for_update())
        if not row:
            raise HTTPException(404, "Чтение не найдено.")
        if row.status in ["queued", "running"]:
            row.status, row.finished_at = "cancelled", now()
            db.commit()
        return job_view(row)

    @router.get("/github/proposals")
    def proposals(db=Depends(session)):
        return [
            {
                "id": r.id,
                "snapshot_id": r.snapshot_id,
                "base_revision": r.base_revision,
                "statement": r.statement,
                "capability_ids": r.capability_ids,
                "project_id": r.project_id,
                "status": r.status,
                "accepted_revision": r.accepted_revision,
                "citations": r.citations,
            }
            for r in db.scalars(
                select(EvidenceProposal).order_by(EvidenceProposal.created_at.desc()).limit(30)
            )
        ]

    @router.post("/github/proposals", status_code=201)
    def propose(data: ProposalRequest, db=Depends(session)):
        snapshot = db.get(GitHubSnapshot, data.snapshot_id)
        head = db.get(ProfileHead, 1)
        if not snapshot:
            raise HTTPException(404, "Снимок не найден.")
        if head.revision_id != data.expected_revision:
            raise ConflictError("Профиль изменился. Обнови страницу.")
        profile = db.get(ProfileRevision, head.revision_id).snapshot
        locations = citations_from_snapshot(snapshot, data.citations)
        if snapshot.facts.get("mode") == "implementation" and not locations:
            raise HTTPException(422, "Для реализации выбери хотя бы один прочитанный фрагмент кода.")
        statement = data.statement
        if not statement:
            if not locations:
                raise HTTPException(422, "Опиши наблюдение или выбери фрагмент для нейтрального предложения.")
            names = "; ".join(f"{c['path']}:{c['line_start']}–{c['line_end']}" for c in locations)
            statement = f"Выбранное основание: {snapshot.repository}@{snapshot.commit_sha[:8]}, {names}. Прочитаны указанные фрагменты; работоспособность, эксплуатация и уровень компетенции этим просмотром не подтверждены."
        if len(statement) < 10:
            raise HTTPException(422, "Опиши конкретное наблюдение хотя бы в 10 символах.")
        if not set(data.capability_ids).issubset({c["id"] for c in profile["capabilities"]}) or (
            data.project_id and data.project_id not in {p["id"] for p in profile["projects"]}
        ):
            raise HTTPException(422, "Выбери существующие компетенции и проект.")
        row = EvidenceProposal(
            snapshot_id=snapshot.id,
            base_revision=head.revision_id,
            statement=statement,
            citations=locations,
            capability_ids=list(dict.fromkeys(data.capability_ids)),
            project_id=data.project_id,
        )
        db.add(row)
        db.commit()
        return {"id": row.id, "status": row.status}

    @router.post("/github/proposals/{proposal_id}/accept")
    def accept(proposal_id: str, db=Depends(session)):
        head = db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        proposal = db.scalar(
            select(EvidenceProposal).where(EvidenceProposal.id == proposal_id).with_for_update()
        )
        if not proposal:
            raise HTTPException(404, "Предложение не найдено.")
        if proposal.status == "accepted":
            return {"revision": proposal.accepted_revision}
        if proposal.status != "pending" or proposal.base_revision != head.revision_id:
            raise ConflictError("Профиль изменился после подготовки предложения. Подготовь новое.")
        if proposal.review_job_id:
            from .github_review import freshness as review_freshness
            from .models import AIJob

            review = db.get(AIJob, proposal.review_job_id)
            if not review or review.status != "completed" or review_freshness(db, review)[0]:
                raise ConflictError("Исходный разбор устарел. Подготовь новое предложение.")
        snapshot = db.get(GitHubSnapshot, proposal.snapshot_id)
        profile = copy.deepcopy(db.get(ProfileRevision, head.revision_id).snapshot)
        evidence_id = f"github-{proposal.id}"
        profile["evidence"].append(
            {
                "id": evidence_id,
                "source": "github",
                "statement": proposal.statement,
                "reference": location_url(proposal.citations[0])
                if proposal.citations
                else f"https://github.com/{snapshot.repository}/tree/{snapshot.commit_sha}",
                "citations": [
                    {k: v for k, v in c.items() if k not in ["excerpt", "kind"]} for c in proposal.citations
                ],
            }
        )
        for item in profile["capabilities"]:
            if item["id"] in proposal.capability_ids:
                item["evidence_ids"].append(evidence_id)
        for item in profile["projects"]:
            if item["id"] == proposal.project_id:
                item["evidence_ids"].append(evidence_id)
        # No category or scope is promoted; owner confirms a specific, bounded statement.
        result = revise_profile(
            db,
            ProfileUpdate(
                expected_revision=proposal.base_revision,
                reason=f"Кот подтвердил свидетельство GitHub {snapshot.repository}@{snapshot.commit_sha[:8]}",
                profile=profile,
            ),
            commit=False,
        )
        proposal.status, proposal.accepted_revision = "accepted", result["revision"]
        db.commit()
        return {"revision": result["revision"]}

    @router.post("/github/proposals/{proposal_id}/reject")
    def reject(proposal_id: str, db=Depends(session)):
        proposal = db.scalar(
            select(EvidenceProposal).where(EvidenceProposal.id == proposal_id).with_for_update()
        )
        if not proposal:
            raise HTTPException(404, "Предложение не найдено.")
        if proposal.status == "accepted":
            raise HTTPException(409, "Свидетельство уже включено в профиль; исправь профиль новой редакцией.")
        proposal.status = "rejected"
        db.commit()
        return {"status": proposal.status}

    return router
