"""Read-only summaries of the latest saved assessment, never new AI requests."""

from sqlalchemy import func, select

from .ai_context import freshness
from .models import AIJob, Analysis


def recommendation_summaries(db, orders):
    if not orders:
        return {}
    ranked = (
        select(
            Analysis.id,
            func.row_number()
            .over(partition_by=Analysis.order_id, order_by=(Analysis.created_at.desc(), Analysis.id.desc()))
            .label("position"),
        )
        .where(Analysis.order_id.in_(orders))
        .subquery()
    )
    rows = db.execute(
        select(Analysis, AIJob)
        .join(ranked, ranked.c.id == Analysis.id)
        .join(AIJob, AIJob.id == Analysis.job_id)
        .where(ranked.c.position == 1)
    )
    result = {}
    for analysis, job in rows:
        value = analysis.result.get("recommendation")
        if value not in {"discuss", "clarify", "decline"}:
            continue
        outdated = job.status != "completed" or bool(freshness(db, job, orders[analysis.order_id])[0])
        result[analysis.order_id] = {"value": value, "outdated": outdated, "job_id": job.id}
    return result


def recommendation_bucket(summary):
    if not summary:
        return "unassessed"
    return "outdated" if summary["outdated"] else summary["value"]
