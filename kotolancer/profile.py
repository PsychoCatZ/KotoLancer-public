from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import ProfileHead, ProfileRevision
from .schemas import CapabilityProfile, ProfileUpdate

SEED = Path(__file__).parent / "data" / "initial_profile.json"


class ConflictError(Exception):
    pass


def initialize_profile(session: Session):
    if session.get(ProfileHead, 1):
        return False
    profile = CapabilityProfile.model_validate_json(SEED.read_text(encoding="utf-8"))
    revision = ProfileRevision(
        snapshot=profile.model_dump(), reason="Начальный профиль, предоставленный Котом 16.10.2026"
    )
    session.add(revision)
    session.flush()
    session.add(ProfileHead(id=1, revision_id=revision.id, seed_id="owner-profile-2026-10-16"))
    session.commit()
    return True


def current_profile(session: Session):
    head = session.get(ProfileHead, 1)
    if not head:
        raise RuntimeError("Initialize the database using python -m kotolancer.manage init")
    revision = session.get(ProfileRevision, head.revision_id)
    return revision_dict(revision)


def revision_dict(revision: ProfileRevision):
    return {
        "revision": revision.id,
        "profile": revision.snapshot,
        "reason": revision.reason,
        "author": revision.author,
        "created_at": revision.created_at,
    }


def revise_profile(session: Session, data: ProfileUpdate, *, commit=True):
    revision = ProfileRevision(snapshot=data.profile.model_dump(), reason=data.reason)
    session.add(revision)
    session.flush()
    result = session.execute(
        update(ProfileHead)
        .where(ProfileHead.id == 1, ProfileHead.revision_id == data.expected_revision)
        .values(revision_id=revision.id)
    )
    if result.rowcount != 1:
        session.rollback()
        raise ConflictError("Профиль уже изменён. Обнови страницу и повтори правку.")
    if commit:
        session.commit()
    return revision_dict(revision)


def revision_history(session: Session):
    revisions = session.scalars(select(ProfileRevision).order_by(ProfileRevision.id.desc()))
    return [
        {"revision": r.id, "reason": r.reason, "author": r.author, "created_at": r.created_at}
        for r in revisions
    ]
