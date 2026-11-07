from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from .schemas import StrictModel


class WorkDetails(StrictModel):
    expected_version: int = Field(ge=1)
    agreed_scope: str = Field(default="", max_length=8000)
    agreed_stack: str = Field(default="", max_length=3000)
    terms: str = Field(default="", max_length=5000)
    due_on: date | None = None
    started_on: date | None = None


class AcceptOrder(WorkDetails):
    confirm: Literal[True]


class WorkStatus(StrictModel):
    expected_version: int = Field(ge=1)
    status: Literal["accepted", "in_progress", "completed", "closed", "cancelled"]
    reason: str = Field(min_length=3, max_length=1000)


class OutcomeInput(StrictModel):
    expected_version: int = Field(ge=1)
    result: Literal["succeeded", "partial", "failed"]
    delivered: str = Field(min_length=3, max_length=1500)
    validation: str = Field(min_length=3, max_length=1000)
    excluded: str = Field(default="", max_length=1000)
    learned: str = Field(default="", max_length=1000)
    capability_ids: list[str] = Field(default_factory=list, max_length=20)
    finished_on: date

    @field_validator("capability_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Выбери каждую компетенцию только один раз")
        return value


class ProposalReview(StrictModel):
    expected_version: int = Field(ge=1)
    expected_profile_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
