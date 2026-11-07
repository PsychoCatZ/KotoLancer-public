import copy
import hashlib
import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select

from .ai_context import ContextError
from .ai_schemas import SCHEMA_VERSION
from .github_evidence import CitationInput, citations_from_snapshot
from .github_review_schemas import ReviewResult
from .models import AIJob, EvidenceProposal, GitHubSnapshot, ProfileHead, ProfileRevision, now, uid


def rules_snapshot():
    text = (Path(__file__).parent / "data/github_review_rules_v1.txt").read_text(encoding="utf-8")
    return {"version": "github-review-v1", "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def facts_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot.facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_context(db, snapshot, settings):
    files = copy.deepcopy(snapshot.facts.get("files", []))
    if snapshot.facts.get("mode") != "implementation" or not any(
        f.get("kind") == "implementation" for f in files
    ):
        raise ContextError(
            "Сначала выбери и прочитай исходники реализации; README и списка зависимостей недостаточно."
        )
    head = db.get(ProfileHead, 1)
    profile = db.get(ProfileRevision, head.revision_id).snapshot
    context = {
        "schema_version": SCHEMA_VERSION,
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort,
        "rules": rules_snapshot(),
        "versions": {"profile": head.revision_id},
        "github_snapshot": {
            "id": snapshot.id,
            "repository": snapshot.repository,
            "commit_sha": snapshot.commit_sha,
            "facts_sha256": facts_hash(snapshot),
        },
        "capabilities": [
            {k: c[k] for k in ["id", "label", "category", "scope"]} for c in profile["capabilities"]
        ],
        "files": [
            {
                "path": f["path"],
                "sha": f["sha"],
                "kind": f.get("kind", "declaration"),
                "lines": [{"number": i + 1, "text": line} for i, line in enumerate(f["text"].splitlines())],
            }
            for f in files
        ],
        "attachments": [],
        "sources": {},
        "coverage": snapshot.facts.get("warnings", []),
    }
    # Never silently trim selected files. A narrower owner selection is an explicit action.
    chars = len(json.dumps(context, ensure_ascii=False))
    if chars > settings.ai_max_context_chars:
        raise ContextError(
            "Выбранные исходники слишком велики для одного разбора. Прочитай меньше файлов отдельным снимком."
        )
    context["size"] = {"text_chars": chars, "binary_bytes": 0, "attachments": 0}
    return context


def freshness(db, job):
    context = job.context
    snapshot = db.get(GitHubSnapshot, context["github_snapshot"]["id"])
    source = context["github_snapshot"]
    if (
        db.get(ProfileHead, 1).revision_id != context["versions"]["profile"]
        or context["rules"]["sha256"] != rules_snapshot()["sha256"]
        or not snapshot
        or snapshot.repository != source["repository"]
        or snapshot.commit_sha != source["commit_sha"]
        or facts_hash(snapshot) != source["facts_sha256"]
    ):
        return "stale", "Профиль, правила или исходный снимок изменились. Подготовь новый разбор."
    return None, None


def normalize(raw, context):
    result = ReviewResult.model_validate_json(raw).model_dump()
    capabilities = {c["id"] for c in context["capabilities"]}
    files = [
        {
            "path": f["path"],
            "sha": f["sha"],
            "kind": f["kind"],
            "text": "\n".join(line["text"] for line in f["lines"]),
        }
        for f in context["files"]
    ]
    source = context["github_snapshot"]
    snapshot = SimpleNamespace(
        repository=source["repository"], commit_sha=source["commit_sha"], facts={"files": files}
    )
    seen = set()
    for observation in result["observations"]:
        if observation["capability_id"] not in capabilities:
            raise ValueError("Unknown capability")
        try:
            locations = citations_from_snapshot(
                snapshot, [CitationInput(**c) for c in observation["citations"]]
            )
        except HTTPException as exc:
            raise ValueError("Invalid code citation") from exc
        if not any(c["kind"] == "implementation" for c in locations):
            raise ValueError("Declaration is not implementation evidence")
        key = (
            observation["capability_id"],
            tuple((c["path"], c["line_start"], c["line_end"]) for c in locations),
        )
        if key in seen:
            raise ValueError("Repeated observation")
        seen.add(key)
        observation["citations"] = locations  # Exact excerpts come from server context, never the model.
    if (
        len(
            {
                (c["path"], c["line_start"], c["line_end"])
                for o in result["observations"]
                for c in o["citations"]
            }
        )
        > 5
    ):
        raise ValueError("Too many distinct evidence locations")
    return result


def constrain_schema(node, context):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "capability_id" and isinstance(value, dict):
                value["enum"] = [c["id"] for c in context["capabilities"]]
            elif key == "path" and isinstance(value, dict):
                value["enum"] = [f["path"] for f in context["files"]]
            else:
                constrain_schema(value, context)
    elif isinstance(node, list):
        for child in node:
            constrain_schema(child, context)


def enqueue(db, data, settings, provider=None):
    from .ai_budget import summary
    from .consultant import ConsultantError

    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    head = db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
    existing = db.scalar(
        select(AIJob).where(AIJob.kind == "github_review", AIJob.request_id == str(data.request_id))
    )
    if existing:
        if (
            existing.context["github_snapshot"]["id"] != data.snapshot_id
            or existing.context["versions"]["profile"] != data.expected_revision
        ):
            raise ConsultantError("Ключ запуска уже использован для другого разбора.")
        return existing
    if head.revision_id != data.expected_revision:
        raise ConsultantError("Профиль изменился. Обнови страницу.")
    if not settings.ai_configured and provider is None:
        raise ConsultantError("Для разбора нужно подключить AI. Запрос модели не отправлен.", 503)
    if (
        settings.environment == "production"
        and provider is None
        and summary(db, settings)["remaining_nusd"] <= 0
    ):
        raise ConsultantError("Общий расчётный резерв исчерпан. Запрос не создан.", 429)
    day = (
        now()
        .astimezone(ZoneInfo("Asia/Krasnoyarsk"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
    )
    if (
        db.scalar(select(func.count()).select_from(AIJob).where(AIJob.created_at >= day))
        >= settings.openai_daily_request_limit
    ):
        raise ConsultantError("Общий суточный предел запусков AI достигнут.", 429)
    if db.scalar(
        select(AIJob.id)
        .where(AIJob.kind == "github_review", AIJob.status.in_(["queued", "running"]))
        .limit(1)
    ):
        raise ConsultantError("Разбор исходников уже выполняется. Дождись его завершения.")
    snapshot = db.get(GitHubSnapshot, data.snapshot_id)
    if not snapshot:
        raise ConsultantError("Снимок не найден.", 404)
    try:
        context = build_context(db, snapshot, settings)
    except ContextError as exc:
        raise ConsultantError(str(exc), 422) from exc
    job = AIJob(
        id=uid(), order_id=None, request_id=str(data.request_id), kind="github_review", context=context
    )
    db.add(job)
    db.commit()
    return job


def prepare_observation(db, job_id, data):
    from .consultant import ConsultantError

    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    head = db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
    job = db.scalar(select(AIJob).where(AIJob.id == job_id, AIJob.kind == "github_review").with_for_update())
    if not job:
        raise ConsultantError("Разбор не найден.", 404)
    existing = db.scalar(
        select(EvidenceProposal).where(
            EvidenceProposal.review_job_id == job.id, EvidenceProposal.review_item == data.index
        )
    )
    if existing:
        return existing
    if head.revision_id != data.expected_revision or job.status != "completed" or freshness(db, job)[0]:
        raise ConsultantError("Этот разбор ещё не готов или устарел. Подготовь актуальный разбор.")
    observations = (job.result or {}).get("observations", [])
    if not observations or data.index >= len(observations):
        raise ConsultantError("Наблюдение не найдено.", 404)
    selected = observations if data.index == -1 else [observations[data.index]]
    source = job.context["github_snapshot"]
    details = "\n".join(f"{o['observation']} Граница вывода: {o['limitation']}" for o in selected)
    statement = f"По выбранным фрагментам {source['repository']}@{source['commit_sha'][:8]}:\n{details}"
    citations = {(c["path"], c["line_start"], c["line_end"]): c for o in selected for c in o["citations"]}
    proposal = EvidenceProposal(
        snapshot_id=source["id"],
        review_job_id=job.id,
        review_item=data.index,
        base_revision=head.revision_id,
        statement=statement,
        citations=copy.deepcopy(list(citations.values())),
        capability_ids=list(dict.fromkeys(o["capability_id"] for o in selected)),
        project_id=None,
    )
    db.add(proposal)
    db.commit()
    return proposal
