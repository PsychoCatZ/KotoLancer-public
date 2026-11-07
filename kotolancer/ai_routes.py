from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_budget import summary as budget_summary
from .ai_schemas import ConsultantRequest, DraftUpdate
from .client_message import clarification_message
from .consultant import ConsultantError, enqueue, job_dict, timestamp
from .models import AIJob, AIWorkerState, Analysis, Order, OrderEvent, OrderMessage, ReplyDraft, now
from .profile import ConflictError


def consultant_routes(session, settings, provider=None):
    router = APIRouter()

    def get_order(db, order_id):
        row = db.get(Order, order_id)
        if not row:
            raise HTTPException(404, "Заказ не найден.")
        return row

    @router.post("/orders/{order_id}/consultant", status_code=202)
    def submit(order_id: str, data: ConsultantRequest, db: Session = Depends(session)):
        try:
            job = enqueue(db, order_id, data, settings, provider)
        except ConsultantError as exc:
            db.rollback()
            raise HTTPException(exc.status, str(exc)) from exc
        return job_dict(db, job)

    @router.get("/orders/{order_id}/consultant")
    def consultant(order_id: str, db: Session = Depends(session)):
        order = get_order(db, order_id)
        jobs = list(
            db.scalars(
                select(AIJob).where(AIJob.order_id == order_id).order_by(AIJob.created_at.desc()).limit(20)
            )
        )
        job_views = {job.id: job_dict(db, job, order) for job in jobs}

        def provenance(job_id):
            return job_views.get(job_id) or job_dict(db, db.get(AIJob, job_id), order)

        analyses = [
            {
                "id": row.id,
                "result": {
                    **row.result,
                    "client_message": clarification_message(row.result, order.source),
                },
                "created_at": timestamp(row.created_at),
                "request": provenance(row.job_id),
            }
            for row in db.scalars(
                select(Analysis)
                .where(Analysis.order_id == order_id)
                .order_by(Analysis.created_at.desc())
                .limit(20)
            )
        ]
        messages = [
            {
                "id": row.id,
                "author": row.author,
                "content": row.content,
                "job_id": row.job_id,
                "created_at": timestamp(row.created_at),
            }
            for row in db.scalars(
                select(OrderMessage).where(OrderMessage.order_id == order_id).order_by(OrderMessage.id)
            )
        ]
        drafts = [
            {
                "id": row.id,
                "content": row.content,
                "version": row.version,
                "created_at": timestamp(row.created_at),
                "updated_at": timestamp(row.updated_at),
                "request": provenance(row.job_id),
            }
            for row in db.scalars(
                select(ReplyDraft)
                .where(ReplyDraft.order_id == order_id)
                .order_by(ReplyDraft.created_at.desc())
                .limit(10)
            )
        ]
        marker = db.get(AIWorkerState, 1)
        heartbeat = marker.heartbeat_at if marker else None
        if heartbeat and heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        return {
            "configured": settings.ai_configured or provider is not None,
            "provider": provider.name if provider else "openai",
            "model": settings.openai_model,
            "worker_seen": bool(
                heartbeat and heartbeat > now() - timedelta(seconds=settings.openai_timeout_seconds * 2 + 60)
            ),
            "consulting_allowed": order.accepted_at is None,
            "jobs": list(job_views.values()),
            "analyses": analyses,
            "messages": messages,
            "drafts": drafts,
            "daily_request_limit": settings.openai_daily_request_limit,
            "budget": budget_summary(db, settings),
        }

    @router.put("/orders/{order_id}/drafts/{draft_id}")
    def update_draft(order_id: str, draft_id: str, data: DraftUpdate, db: Session = Depends(session)):
        order = get_order(db, order_id)
        if order.accepted_at is not None:
            raise HTTPException(409, "Заказ принят: подготовка технического отклика прекращена.")
        draft = db.get(ReplyDraft, draft_id)
        if not draft or draft.order_id != order_id:
            raise HTTPException(404, "Черновик не найден в этом заказе.")
        if draft.version != data.expected_version:
            raise ConflictError("Черновик уже изменился. Обнови страницу перед сохранением.")
        if draft.content != data.content:
            db.add(
                OrderEvent(
                    order_id=order_id,
                    kind="draft_edited",
                    detail={"draft_id": draft.id, "before": draft.content, "after": data.content},
                )
            )
            draft.content, draft.updated_at = data.content, now()
            db.commit()
        return {"id": draft.id, "content": draft.content, "version": draft.version}

    return router
