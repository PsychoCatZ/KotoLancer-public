from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceLocation(StrictModel):
    repository: str = Field(max_length=150)
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    path: str = Field(min_length=1, max_length=300)
    blob_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class Evidence(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: Literal["owner", "github", "completed_order"]
    statement: str = Field(min_length=1, max_length=5000)
    reference: str = Field(min_length=1, max_length=1000)
    citations: list[EvidenceLocation] = Field(default_factory=list, max_length=5)


class Capability(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    category: Literal["A", "B", "C"]
    scope: str = Field(min_length=1, max_length=5000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class Project(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=5000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class CapabilityProfile(StrictModel):
    capabilities: list[Capability] = Field(min_length=1, max_length=500)
    projects: list[Project] = Field(max_length=200)
    evidence: list[Evidence] = Field(min_length=1, max_length=1000)
    risk_areas: list[str] = Field(max_length=100)
    rules: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def consistent_references(self):
        for items in (self.capabilities, self.projects, self.evidence):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError("Identifiers must be unique within their section")
        evidence_ids = {item.id for item in self.evidence}
        for item in [*self.capabilities, *self.projects]:
            if not set(item.evidence_ids).issubset(evidence_ids):
                raise ValueError(f"Unknown evidence reference for {item.id}")
        for item in self.capabilities:
            if item.category == "A" and not item.evidence_ids:
                raise ValueError("A requires an evidence reference, including owner testimony")
        return self


class ProfileUpdate(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
    profile: CapabilityProfile


class OrderFields(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    source: str = Field(default="", max_length=100)
    source_url: str = Field(default="", max_length=2000)
    budget: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=100_000)
    notes: str = Field(default="", max_length=50_000)

    @field_validator("source_url")
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlsplit

        if value:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("Use an http or https URL")
        return value


class OrderUpdate(OrderFields):
    expected_version: int = Field(ge=1)


class StatusUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    status: Literal["new", "discussion", "declined"]


class VisibilityUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    action: Literal["archive", "delete", "restore"]


class TextMaterial(StrictModel):
    order_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=100_000)
    role: Literal["brief", "correspondence", "note"] = "brief"


class AttachMaterial(StrictModel):
    order_id: str = Field(min_length=1)
