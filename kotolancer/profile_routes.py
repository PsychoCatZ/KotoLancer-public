import copy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from .models import EvidenceProposal, ProfileHead, ProfileRevision
from .profile import ConflictError, revise_profile
from .schemas import CapabilityProfile, ProfileUpdate, StrictModel


class RevokeEvidence(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=800)


def profile_routes(session):
    router = APIRouter()

    @router.post("/profile/evidence/{evidence_id}/revoke")
    def revoke(evidence_id: str, data: RevokeEvidence, db=Depends(session)):
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        head = db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
        proposal = None
        if evidence_id.startswith("github-"):
            proposal = db.scalar(
                select(EvidenceProposal)
                .where(EvidenceProposal.id == evidence_id.removeprefix("github-"))
                .with_for_update()
            )
        if proposal and proposal.status == "revoked":
            return {"status": "revoked"}
        if head.revision_id != data.expected_revision:
            raise ConflictError("Профиль изменился. Обнови мастерскую перед отзывом свидетельства.")
        profile = copy.deepcopy(db.get(ProfileRevision, head.revision_id).snapshot)
        evidence = next((e for e in profile["evidence"] if e["id"] == evidence_id), None)
        if not evidence:
            raise HTTPException(404, "Свидетельство не найдено в действующем профиле.")
        if evidence["source"] != "github":
            raise HTTPException(409, "Здесь можно отозвать только свидетельство GitHub.")
        profile["evidence"] = [e for e in profile["evidence"] if e["id"] != evidence_id]
        for item in [*profile["capabilities"], *profile["projects"]]:
            item["evidence_ids"] = [i for i in item["evidence_ids"] if i != evidence_id]
        if any(c["category"] == "A" and not c["evidence_ids"] for c in profile["capabilities"]):
            raise HTTPException(
                409,
                "Это единственное основание категории A. Сначала явно уточни компетенцию и её основание в «Нашем опыте».",
            )
        if not profile["evidence"]:
            raise HTTPException(409, "В профиле должно остаться хотя бы одно основание.")
        revise_profile(
            db,
            ProfileUpdate(
                expected_revision=data.expected_revision,
                reason=f"Отзыв GitHub {evidence_id}: {data.reason}",
                profile=CapabilityProfile.model_validate(profile),
            ),
            commit=False,
        )
        if proposal:
            proposal.status = "revoked"
        db.commit()
        return {"status": "revoked"}

    return router
