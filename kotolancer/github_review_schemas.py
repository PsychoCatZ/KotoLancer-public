from uuid import UUID

from pydantic import Field

from .schemas import StrictModel


class ReviewCitation(StrictModel):
    path: str = Field(min_length=1, max_length=300)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class Observation(StrictModel):
    capability_id: str = Field(min_length=1, max_length=100)
    observation: str = Field(min_length=10, max_length=900)
    limitation: str = Field(min_length=10, max_length=500)
    citations: list[ReviewCitation] = Field(min_length=1, max_length=3)


class ReviewResult(StrictModel):
    summary: str = Field(min_length=1, max_length=1600)
    observations: list[Observation] = Field(max_length=3)


class ReviewRequest(StrictModel):
    request_id: UUID
    snapshot_id: str
    expected_revision: int = Field(ge=1)


class ObservationRequest(StrictModel):
    index: int = Field(ge=-1, le=2)  # -1 explicitly prepares all observations as one proposal.
    expected_revision: int = Field(ge=1)
