import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from .ai_context import freshness
from .consultant import latest_analysis
from .models import (
    AIJob,
    CompletionProposal,
    Material,
    Order,
    OrderEvent,
    OrderOutcome,
    OrderWork,
    ProfileHead,
    ProfileRevision,
    now,
)
from .profile import ConflictError, revise_profile
from .schemas import CapabilityProfile, ProfileUpdate
from .work_schemas import AcceptOrder, OutcomeInput, ProposalReview, WorkDetails, WorkStatus


def work_routes(session, order_view):
    router = APIRouter()

    def lock(db, order_id, expected):
        # Same head -> order lock order as enqueue/budget; owner operations are atomic.
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        head = db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        row = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not row:
            raise HTTPException(404, "Заказ не найден.")
        if row.version != expected:
            raise ConflictError("Карточка изменилась. Обнови её перед действием.")
        return head, row

    def event(db, row, kind, detail):
        row.updated_at = now()
        db.add(OrderEvent(order_id=row.id, kind=kind, detail=detail))

    def view(db, row):
        return order_view(
            row, db.scalar(select(func.count()).select_from(Material).where(Material.order_id == row.id))
        )

    def required_work(db, row):
        work = db.get(OrderWork, row.id)
        if row.accepted_at is None or not work:
            raise HTTPException(409, "Сначала явно прими заказ.")
        return work

    def snapshot(db, head):
        return copy.deepcopy(db.get(ProfileRevision, head.revision_id).snapshot)

    def evidence_id(proposal):
        return "completed-" + proposal.id

    def confirmed(db, head, order_id):
        ids = {e["id"] for e in snapshot(db, head)["evidence"]}
        return any(
            evidence_id(p) in ids
            for p in db.scalars(select(CompletionProposal).where(CompletionProposal.order_id == order_id))
        )

    def invalidate(db, row):
        for p in db.scalars(
            select(CompletionProposal).where(
                CompletionProposal.order_id == row.id, CompletionProposal.status == "pending"
            )
        ):
            p.status = "superseded"

    def details(work):
        return {
            k: (
                getattr(work, k).isoformat()
                if getattr(work, k) is not None and k.endswith("_on")
                else getattr(work, k)
            )
            for k in ["agreed_scope", "agreed_stack", "terms", "due_on", "started_on"]
        }

    def check_dates(start, end=None):
        today = datetime.now(ZoneInfo("Asia/Krasnoyarsk")).date()
        if start and start > today:
            raise HTTPException(422, "Фактическое начало не может быть в будущем.")
        if end and (end > today or start and end < start):
            raise HTTPException(422, "Проверь даты: итог не раньше начала и не в будущем.")

    def validate_capabilities(profile, ids):
        if not set(ids).issubset({c["id"] for c in profile["capabilities"]}):
            raise HTTPException(422, "Выбранная компетенция отсутствует в текущем профиле.")

    def outcome_dict(o):
        return {
            k: getattr(o, k)
            for k in [
                "id",
                "revision",
                "result",
                "delivered",
                "validation",
                "excluded",
                "learned",
                "capability_ids",
                "finished_on",
                "created_at",
            ]
        }

    @router.get("/orders/{order_id}/work")
    def get_work(order_id: str, db=Depends(session)):
        row = db.get(Order, order_id)
        if not row:
            raise HTTPException(404, "Заказ не найден.")
        head = db.get(ProfileHead, 1)
        work = db.get(OrderWork, order_id)
        proposals = list(
            db.scalars(
                select(CompletionProposal)
                .where(CompletionProposal.order_id == order_id)
                .order_by(CompletionProposal.created_at.desc())
            )
        )
        return {
            "work": (
                {
                    **details(work),
                    "acceptance_snapshot": work.acceptance_snapshot,
                    "outcome_id": work.outcome_id,
                }
                if work
                else None
            ),
            "outcomes": [
                outcome_dict(o)
                for o in db.scalars(
                    select(OrderOutcome)
                    .where(OrderOutcome.order_id == order_id)
                    .order_by(OrderOutcome.revision.desc())
                )
            ],
            "proposals": [
                {
                    "id": p.id,
                    "outcome_id": p.outcome_id,
                    "statement": p.statement,
                    "capability_ids": p.capability_ids,
                    "status": p.status,
                    "base_revision": p.base_revision,
                    "accepted_revision": p.accepted_revision,
                    "stale": p.base_revision != head.revision_id
                    or not work
                    or p.outcome_id != work.outcome_id
                    or row.status not in ["completed", "closed"],
                }
                for p in proposals
            ],
            "profile_revision": head.revision_id,
            "confirmed_evidence": confirmed(db, head, order_id),
            "capabilities": [
                {k: c[k] for k in ["id", "label", "category"]} for c in snapshot(db, head)["capabilities"]
            ],
        }

    @router.post("/orders/{order_id}/accept")
    def accept(order_id: str, data: AcceptOrder, db=Depends(session)):
        head, row = lock(db, order_id, data.expected_version)
        if row.accepted_at is not None:
            raise HTTPException(409, "Заказ уже принят; первое принятие сохраняется.")
        if row.status == "declined":
            raise HTTPException(409, "Сначала верни заказ в обсуждение.")
        previous = latest_analysis(db, order_id)
        if (
            previous
            and not freshness(db, db.get(AIJob, previous.job_id), row)[0]
            and previous.result["ethics"]["verdict"] != "clear"
        ):
            raise HTTPException(
                409, "В актуальном анализе есть этические вопросы или отказ. Сначала разреши их."
            )
        check_dates(data.started_on)
        values = data.model_dump(exclude={"expected_version", "confirm"})
        record = {
            **data.model_dump(mode="json", exclude={"expected_version", "confirm"}),
            "order_version": row.version,
            "materials_revision": row.materials_revision,
            "profile_revision": head.revision_id,
            "title": row.title,
            "description": row.description,
            "budget": row.budget,
        }
        db.add(OrderWork(order_id=order_id, acceptance_snapshot=record, **values))
        before = row.status
        row.accepted_at = now()
        row.status = "accepted"
        event(db, row, "order_accepted", {"before": before, "snapshot": record})
        db.commit()
        return view(db, row)

    @router.put("/orders/{order_id}/work")
    def update_work(order_id: str, data: WorkDetails, db=Depends(session)):
        _, row = lock(db, order_id, data.expected_version)
        work = required_work(db, row)
        outcome = db.get(OrderOutcome, work.outcome_id) if work.outcome_id else None
        check_dates(data.started_on, outcome.finished_on if outcome else None)
        before = details(work)
        for k, v in data.model_dump(exclude={"expected_version"}).items():
            setattr(work, k, v)
        after = details(work)
        if before != after:
            event(db, row, "work_updated", {"before": before, "after": after})
            db.commit()
        return view(db, row)

    @router.post("/orders/{order_id}/work/status")
    def status(order_id: str, data: WorkStatus, db=Depends(session)):
        head, row = lock(db, order_id, data.expected_version)
        work = required_work(db, row)
        if row.status == data.status:
            return view(db, row)
        if data.status in ["completed", "closed"]:
            outcome = db.get(OrderOutcome, work.outcome_id) if work.outcome_id else None
            if not outcome or outcome.result == "failed":
                raise HTTPException(409, "Сначала зафиксируй выполненную работу и её итог.")
        else:
            if confirmed(db, head, order_id):
                raise HTTPException(
                    409, "Сначала отзови свидетельство этого итога из профиля — оно уже подтверждено."
                )
            invalidate(db, row)
            work.outcome_id = None
        before = row.status
        row.status = data.status
        event(db, row, "status_changed", {"before": before, "after": data.status, "reason": data.reason})
        db.commit()
        return view(db, row)

    @router.post("/orders/{order_id}/outcomes", status_code=201)
    def save_outcome(order_id: str, data: OutcomeInput, db=Depends(session)):
        head, row = lock(db, order_id, data.expected_version)
        work = required_work(db, row)
        if row.status == "closed":
            raise HTTPException(409, "Для исправления сначала явно открой учёт заказа снова.")
        if confirmed(db, head, order_id):
            raise HTTPException(
                409, "Сначала отзови подтверждённое свидетельство. Старый итог останется в истории."
            )
        check_dates(work.started_on, data.finished_on)
        validate_capabilities(snapshot(db, head), data.capability_ids)
        revision = (
            db.scalar(select(func.max(OrderOutcome.revision)).where(OrderOutcome.order_id == order_id)) or 0
        ) + 1
        outcome = OrderOutcome(
            order_id=order_id, revision=revision, **data.model_dump(exclude={"expected_version"})
        )
        db.add(outcome)
        db.flush()
        invalidate(db, row)
        work.outcome_id = outcome.id
        before = row.status
        row.status = "cancelled" if data.result == "failed" else "completed"
        event(
            db,
            row,
            "outcome_saved",
            {
                "outcome_id": outcome.id,
                "revision": revision,
                "result": data.result,
                "before": before,
                "after": row.status,
            },
        )
        if data.result == "succeeded" and data.capability_ids:
            statement = f"Фактически выполнено: {data.delivered}\nПодтверждение: {data.validation}"
            if data.excluded:
                statement += "\nЗа пределами работы: " + data.excluded
            if data.learned:
                statement += "\nПрактический результат обучения: " + data.learned
            db.add(
                CompletionProposal(
                    order_id=order_id,
                    outcome_id=outcome.id,
                    base_revision=head.revision_id,
                    statement=statement,
                    capability_ids=data.capability_ids,
                )
            )
        db.commit()
        return view(db, row)

    @router.post("/orders/{order_id}/experience/{proposal_id}/{action}")
    def review(order_id: str, proposal_id: str, action: str, data: ProposalReview, db=Depends(session)):
        if action not in ["accept", "reject", "refresh", "revoke"]:
            raise HTTPException(404, "Действие не найдено.")
        head, row = lock(db, order_id, data.expected_version)
        work = required_work(db, row)
        p = db.scalar(
            select(CompletionProposal)
            .where(CompletionProposal.id == proposal_id, CompletionProposal.order_id == order_id)
            .with_for_update()
        )
        if not p:
            raise HTTPException(404, "Предложение не найдено в этом заказе.")
        if action == "accept" and p.status == "accepted":
            return {"revision": p.accepted_revision}
        if action == "revoke" and p.status == "revoked":
            return {"revision": p.revoked_revision}
        if data.expected_profile_revision != head.revision_id:
            raise ConflictError("Профиль изменился. Обнови предложение перед подтверждением.")
        profile = snapshot(db, head)
        eid = evidence_id(p)
        if action == "revoke":
            if p.status != "accepted":
                raise HTTPException(409, "Это свидетельство не подтверждено.")
            profile["evidence"] = [e for e in profile["evidence"] if e["id"] != eid]
            for c in [*profile["capabilities"], *profile["projects"]]:
                c["evidence_ids"] = [i for i in c["evidence_ids"] if i != eid]
            if any(c["category"] == "A" and not c["evidence_ids"] for c in profile["capabilities"]):
                raise HTTPException(
                    409,
                    "Это единственное основание категории A. Сначала скорректируй компетенцию в «Наш опыт».",
                )
        else:
            outcome = db.get(OrderOutcome, p.outcome_id)
            if (
                p.outcome_id != work.outcome_id
                or row.status not in ["completed", "closed"]
                or outcome.result != "succeeded"
            ):
                raise HTTPException(
                    409, "Итог изменился или заказ открыт снова. Это предложение уже не актуально."
                )
            if action == "reject":
                if p.status != "pending":
                    raise HTTPException(409, "Предложение уже рассмотрено.")
                p.status = "rejected"
                event(db, row, "experience_rejected", {"proposal_id": p.id, "reason": data.reason})
                db.commit()
                return {"status": p.status}
            if action == "refresh":
                if p.status == "accepted":
                    raise HTTPException(409, "Подтверждённое свидетельство нельзя обновить молча.")
                validate_capabilities(profile, p.capability_ids)
                p.base_revision = head.revision_id
                p.status = "pending"
                event(db, row, "experience_refreshed", {"proposal_id": p.id, "reason": data.reason})
                db.commit()
                return {"status": p.status}
            if p.status != "pending" or p.base_revision != head.revision_id:
                raise ConflictError("Предложение устарело. Обнови его, сохранив ручные правки профиля.")
            validate_capabilities(profile, p.capability_ids)
            if any(e["id"] == eid for e in profile["evidence"]):
                raise ConflictError("Свидетельство с этим идентификатором уже существует.")
            profile["evidence"].append(
                {
                    "id": eid,
                    "source": "completed_order",
                    "statement": p.statement,
                    "reference": f"Заказ {order_id}, итог {outcome.id}, редакция {outcome.revision}",
                }
            )
            for c in profile["capabilities"]:
                if c["id"] in p.capability_ids:
                    c["evidence_ids"].append(eid)
        try:
            validated = CapabilityProfile.model_validate(profile)
        except ValidationError as exc:
            raise HTTPException(
                422, "Предложение не помещается в текущий профиль. Проверь его вручную."
            ) from exc
        result = revise_profile(
            db,
            ProfileUpdate(expected_revision=head.revision_id, reason=data.reason, profile=validated),
            commit=False,
        )
        if action == "accept":
            p.status = "accepted"
            p.accepted_revision = result["revision"]
        else:
            p.status = "revoked"
            p.revoked_revision = result["revision"]
        event(
            db,
            row,
            "experience_" + action,
            {"proposal_id": p.id, "profile_revision": result["revision"], "reason": data.reason},
        )
        db.commit()
        return {"revision": result["revision"]}

    return router
