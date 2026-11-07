import base64
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_rules import rules_snapshot
from .ai_schemas import SCHEMA_VERSION
from .config import Settings
from .models import (
    AIJob,
    Analysis,
    Material,
    MaterialExtract,
    Order,
    OrderMessage,
    ProfileHead,
    ProfileRevision,
)


class ContextError(Exception):
    pass


def file_path(settings: Settings, key: str):
    root = settings.storage_dir.resolve()
    path = (root / key).resolve()
    if path.parent != root or not path.is_file():
        raise ContextError("Оригинал одного из материалов отсутствует. Проверь вложения заказа.")
    return path


def build_context(db: Session, order: Order, settings: Settings, kind: str, instruction: str):
    head = db.get(ProfileHead, 1)
    profile = db.get(ProfileRevision, head.revision_id).snapshot
    sources = {"order": {"label": "Описание заказа", "kind": "order"}}
    for collection, prefix, label_field in (
        ("capabilities", "capability", "label"),
        ("projects", "project", "name"),
        ("evidence", "evidence", "statement"),
    ):
        for item in profile[collection]:
            sources[f"{prefix}:{item['id']}"] = {"label": item[label_field], "kind": prefix}
    materials, attachments, coverage = [], [], []
    binary_size = 0
    for row in db.scalars(
        select(Material).where(Material.order_id == order.id).order_by(Material.created_at, Material.id)
    ):
        reference = f"material:{row.id}"
        sources[reference] = {"label": row.title, "kind": "material"}
        extracted = db.get(MaterialExtract, row.id)
        warnings = list(extracted.warnings) if extracted else []
        mode = "text"
        if row.file_key:
            path = file_path(settings, row.file_key)
            if hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256:
                raise ContextError("Содержимое вложения изменилось вне приложения. Загрузи оригинал заново.")
            if row.extraction_status == "failed":
                raise ContextError(f"Материал «{row.title}» не разобран. Проверь файл перед анализом.")
            if row.kind in {"image", "pdf"}:
                attachments.append(
                    {
                        "source_id": reference,
                        "key": row.file_key,
                        "mime": row.mime,
                        "kind": row.kind,
                        "sha256": row.sha256,
                    }
                )
                binary_size += row.size
                mode = "image" if row.kind == "image" else "pdf_with_page_images"
                warnings = []  # The original is now actually supplied for visual reading.
            elif row.kind == "docx":
                mode = "docx_text_and_images"
                with ZipFile(path) as archive:
                    for entry in archive.infolist():
                        if not entry.filename.startswith("word/media/") or entry.is_dir():
                            continue
                        suffix = Path(entry.filename).suffix.lower()
                        mime = {
                            ".png": "image/png",
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".webp": "image/webp",
                        }.get(suffix)
                        if mime:
                            attachments.append(
                                {
                                    "source_id": reference,
                                    "key": row.file_key,
                                    "mime": mime,
                                    "kind": "image",
                                    "entry": entry.filename,
                                    "sha256": row.sha256,
                                }
                            )
                            binary_size += entry.file_size
                        else:
                            warnings.append(f"Встроенный файл {entry.filename}: формат не передан модели.")
                warnings = [w for w in warnings if not w.startswith("В DOCX есть изображения")]
        materials.append(
            {
                "source_id": reference,
                "title": row.title,
                "role": row.role,
                "text": row.text,
                "blocks": extracted.blocks if extracted else [],
            }
        )
        coverage.append({"source_id": reference, "title": row.title, "mode": mode, "warnings": warnings})
    messages = []
    for row in db.scalars(
        select(OrderMessage).where(OrderMessage.order_id == order.id).order_by(OrderMessage.id)
    ):
        reference = f"message:{row.id}"
        sources[reference] = {
            "label": f"{'Кот' if row.author == 'owner' else 'Ассистент'}: {row.content[:120]}",
            "kind": "message",
        }
        messages.append({"source_id": reference, "author": row.author, "content": row.content})
    previous = db.scalar(
        select(Analysis).where(Analysis.order_id == order.id).order_by(Analysis.created_at.desc()).limit(1)
    )
    versions = {
        "order": order.version,
        "materials": order.materials_revision,
        "conversation": order.conversation_revision,
        "profile": head.revision_id,
    }
    previous_context = db.get(AIJob, previous.job_id).context if previous else None
    previous_versions = previous_context.get("versions") if previous_context else None
    changes = None
    if previous_versions and "sources" in previous_context:
        previous_materials = {
            key for key, source in previous_context["sources"].items() if source["kind"] == "material"
        }
        current_materials = {item["source_id"] for item in materials}
        changes = {
            "revision_changed": {
                key: versions[key] != previous_versions[key] if key in previous_versions else None
                for key in versions
            },
            "added_material_source_ids": sorted(current_materials - previous_materials),
            "removed_material_source_ids": sorted(previous_materials - current_materials),
        }
    context = {
        "kind": kind,
        "rules": rules_snapshot(),
        "schema_version": SCHEMA_VERSION,
        "instruction": instruction,
        "versions": versions,
        "order": {
            "id": order.id,
            "title": order.title,
            "description": order.description,
            "notes": order.notes,
        },
        "communication_context": {"platform": order.source},
        "profile": profile,
        "sources": sources,
        "materials": materials,
        "messages": messages,
        "previous_analysis": (
            {"id": previous.id, "result": previous.result, "versions": previous_versions}
            if previous
            else None
        ),
        "changes_since_previous_analysis": changes,
        "coverage": coverage,
        "attachments": attachments,
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort,
    }
    chars = len(json.dumps(context, ensure_ascii=False))
    if chars > settings.ai_max_context_chars:
        raise ContextError(
            "Контекст заказа превышает настроенный предел. Ничего не обрезано и запрос не отправлен; нужен разбор по частям."
        )
    if binary_size > settings.ai_max_binary_bytes or len(attachments) > 20:
        raise ContextError(
            "Слишком много визуальных материалов для одного запроса. Запрос не отправлен; нужен разбор по частям."
        )
    context["size"] = {"text_chars": chars, "binary_bytes": binary_size, "attachments": len(attachments)}
    return context


def input_content(context: dict, settings: Settings):
    public_context = {key: value for key, value in context.items() if key not in {"attachments", "rules"}}
    content = [{"type": "input_text", "text": json.dumps(public_context, ensure_ascii=False)}]
    for item in context["attachments"]:
        path = file_path(settings, item["key"])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ContextError(
                "Файл изменился после постановки запроса. Повтори анализ с актуальным материалом."
            )
        if item.get("entry"):
            with ZipFile(path) as archive:
                data = archive.read(item["entry"])
        encoded = base64.b64encode(data).decode("ascii")
        content.append({"type": "input_text", "text": f"Вложение источника {item['source_id']}"})
        if item["kind"] == "pdf":
            content.append(
                {
                    "type": "input_file",
                    "filename": path.name,
                    "file_data": f"data:application/pdf;base64,{encoded}",
                }
            )
        else:
            content.append({"type": "input_image", "image_url": f"data:{item['mime']};base64,{encoded}"})
    return content


def freshness(db: Session, job: AIJob, order: Order):
    if job.kind == "github_review":
        from .github_review import freshness as review_freshness

        return review_freshness(db, job)
    if order.deleted_at:
        return "suppressed", "Заказ удалён в корзину."
    if order.accepted_at is not None:
        return "suppressed", "Заказ принят: техническое консультирование прекращено."
    versions = job.context["versions"]
    head = db.get(ProfileHead, 1)
    if (
        order.version != versions["order"]
        or order.materials_revision != versions["materials"]
        or order.conversation_revision != versions["conversation"]
        or head.revision_id != versions["profile"]
    ):
        return "stale", "Материалы, переписка или профиль изменились. Результат относится к прежним данным."
    return None, None
