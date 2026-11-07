from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .ai_budget import summary
from .consultant import ConsultantError, job_dict
from .github_review import enqueue, prepare_observation
from .github_review_schemas import ObservationRequest, ReviewRequest
from .models import AIJob, now


def github_review_routes(session, settings, provider=None):
    router = APIRouter()

    @router.post("/github/reviews", status_code=202)
    def submit(data: ReviewRequest, db=Depends(session)):
        try:
            return job_dict(db, enqueue(db, data, settings, provider))
        except ConsultantError as exc:
            db.rollback()
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/github/reviews")
    def reviews(snapshot_id: str, db=Depends(session)):
        rows = db.scalars(
            select(AIJob)
            .where(
                AIJob.kind == "github_review",
                AIJob.context["github_snapshot"]["id"].as_string() == snapshot_id,
            )
            .order_by(AIJob.created_at.desc())
            .limit(20)
        ).all()
        return {
            "configured": settings.ai_configured or provider is not None,
            "fixture": provider is not None,
            "budget": summary(db, settings),
            "jobs": [{**job_dict(db, job), "result": job.result} for job in rows],
        }

    @router.post("/github/reviews/{job_id}/proposals", status_code=201)
    def prepare(job_id: str, data: ObservationRequest, db=Depends(session)):
        try:
            proposal = prepare_observation(db, job_id, data)
            return {"id": proposal.id, "status": proposal.status}
        except ConsultantError as exc:
            db.rollback()
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/github/reviews/{job_id}/cancel")
    def cancel(job_id: str, db=Depends(session)):
        job = db.scalar(
            select(AIJob).where(AIJob.id == job_id, AIJob.kind == "github_review").with_for_update()
        )
        if not job:
            raise HTTPException(404, "Разбор не найден.")
        if job.status in ["queued", "running"]:
            job.status, job.error_message = (
                "cancelled",
                "Разбор отменён. Уже отправленный запрос может быть оплачен.",
            )
            job.finished_at, job.lease_until, job.lease_token = now(), None, None
            db.commit()
        return {"id": job.id, "status": job.status}

    return router
