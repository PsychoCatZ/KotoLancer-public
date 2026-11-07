import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai_context import ContextError, build_context, freshness
from .ai_provider import OpenAIProvider, ProviderError
from .ai_schemas import RESULT_TYPES, SCHEMA_VERSION, ConsultantRequest
from .client_message import clarification_message, platform_copy
from .config import Settings
from .models import AIJob, AIRun, Analysis, Order, OrderEvent, OrderMessage, ProfileHead, ReplyDraft, now, uid

ACTIVE_STATUSES = ("queued", "running")


class ConsultantError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def latest_analysis(db, order_id):
    return db.scalar(
        select(Analysis).where(Analysis.order_id == order_id).order_by(Analysis.created_at.desc()).limit(1)
    )


def enqueue(db: Session, order_id: str, data: ConsultantRequest, settings: Settings, provider=None):
    # Shared lock serializes quota reservations and revision snapshots across orders.
    db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order or order.deleted_at:
        raise ConsultantError("Заказ не найден.", 404)
    existing = db.scalar(
        select(AIJob).where(AIJob.order_id == order_id, AIJob.request_id == str(data.request_id))
    )
    if existing:
        if existing.kind != data.kind or existing.context.get("instruction") != data.message:
            raise ConsultantError("Идентификатор запроса уже использован для другого действия.")
        return existing
    if order.accepted_at is not None:
        raise ConsultantError("Заказ принят: техническое консультирование прекращено.")
    if not settings.ai_configured and provider is None:
        raise ConsultantError("Нужно настроить ключ и модель OpenAI. Пока ни один запрос не отправлен.", 503)
    if settings.environment == "production" and provider is None:
        from .ai_budget import summary

        if summary(db, settings)["remaining_nusd"] <= 0:
            raise ConsultantError("Расчётный денежный предел исчерпан. Новый запрос не создан.", 429)
    if data.kind == "chat" and not data.message.strip():
        raise ConsultantError("Напиши сообщение для обсуждения заказа.", 422)
    if db.scalar(
        select(AIJob.id).where(AIJob.order_id == order_id, AIJob.status.in_(ACTIVE_STATUSES)).limit(1)
    ):
        raise ConsultantError("В этом заказе уже есть незавершённый запрос.")
    day = (
        now()
        .astimezone(ZoneInfo("Asia/Krasnoyarsk"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
    )
    reserved = db.scalar(select(func.count()).select_from(AIJob).where(AIJob.created_at >= day))
    if reserved >= settings.openai_daily_request_limit:
        raise ConsultantError("Достигнут суточный предел запусков AI. Нового платного запроса нет.", 429)
    previous = latest_analysis(db, order_id)
    if data.kind == "draft":
        if not previous:
            raise ConsultantError("Сначала получи анализ заказа, затем подготовим отклик.")
        previous_job = db.get(AIJob, previous.job_id)
        if freshness(db, previous_job, order)[0]:
            raise ConsultantError("Перед откликом обнови анализ: сведения о заказе или профиль изменились.")
        if (
            previous.result["ethics"]["verdict"] != "clear"
            or previous.result["technical_verdict"] == "not_suitable"
        ):
            raise ConsultantError(
                "Отклик с предложением выполнить работу недоступен: сначала разреши причины отказа или этические вопросы."
            )
    job = AIJob(id=uid(), order_id=order.id, request_id=str(data.request_id), kind=data.kind, context={})
    db.add(job)
    db.flush()
    if data.kind == "chat":
        db.add(OrderMessage(order_id=order.id, job_id=job.id, author="owner", content=data.message))
        order.conversation_revision += 1
        order.updated_at = now()
        db.flush()
    try:
        job.context = build_context(db, order, settings, data.kind, data.message)
    except ContextError as exc:
        raise ConsultantError(str(exc), 422) from exc
    db.add(OrderEvent(order_id=order.id, kind="ai_queued", detail={"job_id": job.id, "kind": job.kind}))
    db.commit()
    return job


def normalize_result(kind: str, raw: str, context: dict):
    if kind == "github_review":
        from .github_review import normalize

        return normalize(raw, context)
    result = RESULT_TYPES[kind].model_validate_json(raw).model_dump()
    sources = context["sources"]

    def validate_sources(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "source_ids" and any(source not in sources for source in child):
                    raise ValueError("Unknown evidence reference")
                validate_sources(child)
        elif isinstance(value, list):
            for child in value:
                validate_sources(child)

    validate_sources(result)
    if kind == "analysis":
        profile = context["profile"]
        proven = {f"capability:{c['id']}" for c in profile["capabilities"] if c["category"] == "A"}
        proven |= {
            f"evidence:{ref}"
            for c in profile["capabilities"]
            if c["category"] == "A"
            for ref in c["evidence_ids"]
        }
        proven |= {f"project:{p['id']}" for p in profile["projects"]}
        proven |= {f"evidence:{ref}" for p in profile["projects"] for ref in p["evidence_ids"]}
        for component in result["components"]:
            if component["category"] == "A" and not proven.intersection(component["source_ids"]):
                raise ValueError("A without practical evidence")
        if any(c["required"] and c["category"] == "C" for c in result["components"]):
            result["technical_verdict"] = "not_suitable"
        elif result["technical_verdict"] == "capable" and any(
            c["required"] and c["category"] == "unknown" for c in result["components"]
        ):
            result["technical_verdict"] = "needs_check"
        if result["ethics"]["verdict"] == "clarify":
            result["proposed_stack"] = []
            result["effort"] = None
        if result["ethics"]["verdict"] == "reject":
            # Do not publish a generated implementation plan for an ethical refusal.
            result["summary"] = result["ethics"]["reason"]
            result["scope"] = "По этическим причинам эту работу не берём."
            result["proposed_stack"] = []
            result["effort"] = None
            result["questions"] = []
            result["components"] = []
            result["requirements"] = []
            result["risks"] = []
            result["technical_reason"] = "Техническая выполнимость не меняет этический отказ."
        if result["ethics"]["verdict"] == "reject" or result["technical_verdict"] == "not_suitable":
            result["recommendation"] = "decline"
        elif result["ethics"]["verdict"] == "clarify" or result["technical_verdict"] in {
            "needs_check",
            "insufficient_data",
        }:
            result["recommendation"] = "clarify"
        else:
            result["recommendation"] = "discuss"
        result["client_message"] = clarification_message(
            result, context.get("communication_context", {}).get("platform", "")
        )
    elif result["ethics"]["verdict"] != "clear":
        if kind == "draft":
            raise ValueError("Draft with ethical concerns")
        result["answer"] = result["ethics"]["reason"]
    if kind == "draft":
        result["content"] = platform_copy(
            result["content"], context.get("communication_context", {}).get("platform", "")
        )
    return result


def claim_next(db: Session, settings: Settings):
    expired = db.scalars(
        select(AIJob)
        .where(AIJob.status == "running", AIJob.lease_until < now())
        .with_for_update(skip_locked=True)
    )
    for job in expired:
        job.status, job.error_code = "failed", "unknown_outcome"
        job.error_message = "Обработчик потерял связь. Исход запроса и списание могут быть неизвестны. Автоматического повтора нет."
        job.lease_token, job.finished_at = None, now()
    db.commit()
    job = db.scalar(
        select(AIJob)
        .where(AIJob.status == "queued")
        .order_by(AIJob.created_at, AIJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    order = db.get(Order, job.order_id) if job.order_id else None
    state, reason = freshness(db, job, order)
    if state:
        job.status, job.error_message, job.finished_at = state, reason, now()
        db.commit()
        return (job.id, None)
    job.status, job.started_at, job.lease_token = "running", now(), uid()
    job.lease_until = now() + timedelta(seconds=settings.openai_timeout_seconds * 2 + 60)
    db.add(
        AIRun(
            job_id=job.id,
            provider="pending",
            model=job.context["model"],
            rules_version=job.context["rules"]["version"],
            schema_version=SCHEMA_VERSION,
        )
    )
    db.commit()
    return (job.id, job.lease_token)


def process_next(factory, settings: Settings, provider):
    with factory() as db:
        claim = claim_next(db, settings)
    if claim is None:
        return False
    job_id, lease = claim
    if lease is None:
        return True
    with factory() as db:
        job = db.get(AIJob, job_id)
        context, kind = job.context, job.kind
    started = time.monotonic()
    result, output, error = None, None, None
    try:
        if context.get("schema_version") != SCHEMA_VERSION:
            raise ContextError(
                "Формат консультанта обновился. Создай новый запрос; старый не отправлен модели."
            )
        if isinstance(provider, OpenAIProvider) and provider.client is None:
            from .ai_budget import ProductionBudget

            output = provider.generate(
                kind, context, guard=ProductionBudget(factory, settings, job_id, lease)
            )
        else:
            output = provider.generate(kind, context)
        result = normalize_result(kind, output.text, context)
    except ProviderError as exc:
        error, output = (exc.code, exc.message), exc.result
    except ContextError as exc:
        error = ("context_changed", str(exc))
    except (ValidationError, ValueError):
        error = (
            "invalid_output",
            "Ответ AI не прошёл проверку структуры или источников. Он не сохранён как готовый результат.",
        )
    except Exception:
        error = (
            "worker_error",
            "Обработка завершилась ошибкой. Подробности запроса и секреты не публикуются; автоматического повтора нет.",
        )
    with factory() as db:
        # Profile + order locks make publication atomic with edits and future acceptance.
        db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        job = db.scalar(select(AIJob).where(AIJob.id == job_id).with_for_update())
        order = (
            db.scalar(select(Order).where(Order.id == job.order_id).with_for_update())
            if job.order_id
            else None
        )
        run = db.get(AIRun, job.id)
        if run:
            run.provider, run.duration_ms = provider.name, int((time.monotonic() - started) * 1000)
            if output:
                run.model, run.response_id, run.usage = output.model, output.response_id, output.usage
        if job.status != "running" or job.lease_token != lease:
            db.commit()
            return True  # A late worker may record usage, but cannot resurrect its expired request.
        state, reason = freshness(db, job, order)
        job.finished_at, job.lease_until, job.lease_token = now(), None, None
        if state:
            job.status, job.error_message = state, reason
            if state == "stale" and result is not None and error is None:
                job.result = result
                if kind == "analysis":
                    db.add(Analysis(order_id=order.id, job_id=job.id, result=result))
                if kind != "github_review":
                    db.add(
                        OrderEvent(
                            order_id=order.id, kind="ai_stale", detail={"kind": kind, "job_id": job.id}
                        )
                    )
        elif error:
            job.status, job.error_code, job.error_message = "failed", error[0], error[1]
        else:
            job.status, job.result = "completed", result
            if kind == "analysis":
                db.add(Analysis(order_id=order.id, job_id=job.id, result=result))
            elif kind == "chat":
                db.add(
                    OrderMessage(
                        order_id=order.id, job_id=job.id, author="assistant", content=result["answer"]
                    )
                )
            elif kind == "draft":
                db.add(ReplyDraft(order_id=order.id, job_id=job.id, content=result["content"]))
            if kind != "github_review":
                db.add(
                    OrderEvent(
                        order_id=order.id, kind="ai_completed", detail={"kind": kind, "job_id": job.id}
                    )
                )
        db.commit()
    return True


def job_dict(db, job, order=None):
    order = order or (db.get(Order, job.order_id) if job.order_id else None)
    stale, reason = freshness(db, job, order)
    run = db.get(AIRun, job.id)
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "created_at": timestamp(job.created_at),
        "started_at": timestamp(job.started_at),
        "finished_at": timestamp(job.finished_at),
        "error_code": job.error_code,
        "error_message": job.error_message,
        "outdated": bool(stale),
        "outdated_reason": reason,
        "versions": job.context["versions"],
        "coverage": job.context["coverage"],
        "sources": job.context["sources"],
        "context_size": job.context["size"],
        "model": run.model if run else job.context["model"],
        "usage": run.usage if run else None,
        "reserved_nusd": run.reserved_nusd if run else 0,
        "rules_version": run.rules_version if run else job.context["rules"]["version"],
        "rules_sha256": job.context["rules"]["sha256"],
        "duration_ms": run.duration_ms if run else None,
    }


def timestamp(value: datetime | None):
    return value.replace(tzinfo=UTC).isoformat() if value and value.tzinfo is None else value
