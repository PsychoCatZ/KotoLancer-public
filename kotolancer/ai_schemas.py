from typing import Literal
from uuid import UUID

from pydantic import Field

from .github_review_schemas import ReviewResult
from .schemas import StrictModel

SCHEMA_VERSION = "consultant-schema-v2"


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(max_length=30)


class Component(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    category: Literal["A", "B", "C", "unknown"]
    required: bool
    explanation: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(max_length=30)
    verification: str | None = Field(max_length=2000)


class Ethics(StrictModel):
    verdict: Literal["clear", "clarify", "reject"]
    reason: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(max_length=30)


class Question(StrictModel):
    text: str = Field(min_length=1, max_length=1500)
    why_it_matters: str = Field(min_length=1, max_length=1500)


class StackItem(StrictModel):
    technology: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)


class AnalysisResult(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    technical_verdict: Literal["capable", "needs_check", "not_suitable", "insufficient_data"] = Field(
        description="Только техническая посильность, независимо от этики и цены. Этический отказ не является основанием not_suitable. Обычные настройки реализации не являются основанием needs_check."
    )
    technical_reason: str = Field(min_length=1, max_length=3000)
    ethics: Ethics
    requirements: list[Claim] = Field(max_length=40)
    components: list[Component] = Field(max_length=30)
    scope: str = Field(min_length=1, max_length=4000)
    questions: list[Question] = Field(max_length=12)
    client_message: str | None = Field(
        min_length=1,
        max_length=5000,
        description="Одно готовое сообщение заказчику с существенными уточняющими вопросами, без внутреннего анализа. null, если вопросов нет или заказ отклонён.",
    )
    risks: list[Claim] = Field(max_length=20)
    proposed_stack: list[StackItem] = Field(max_length=15)
    effort: str | None = Field(max_length=2000)
    changes_from_previous: str | None = Field(max_length=3000)


class ChatResult(StrictModel):
    answer: str = Field(min_length=1, max_length=12000)
    ethics: Ethics
    source_ids: list[str] = Field(max_length=50)
    suggests_reanalysis: bool


class DraftResult(StrictModel):
    content: str = Field(
        min_length=1,
        max_length=12000,
        description="Только готовый текст отклика заказчику; по умолчанию 1–2 коротких абзаца. Без внутреннего анализа, A/B/C, technical_verdict, этического разбора и служебных заголовков. Основания опыта и этика — в отдельных полях.",
    )
    experience_claims: list[Claim] = Field(max_length=20)
    ethics: Ethics


RESULT_TYPES = {
    "analysis": AnalysisResult,
    "chat": ChatResult,
    "draft": DraftResult,
    "github_review": ReviewResult,
}


class ConsultantRequest(StrictModel):
    request_id: UUID
    kind: Literal["analysis", "chat", "draft"]
    message: str = Field(default="", max_length=8000)


class DraftUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=20000)
