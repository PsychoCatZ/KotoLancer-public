import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from kotolancer.ai_context import build_context
from kotolancer.db import make_engine, session_factory
from kotolancer.models import AIJob, Analysis, Order, now
from tests.test_foundation import create_order


def save_analysis(db, settings, order_id, value="discuss", offset=0):
    order = db.get(Order, order_id)
    job = AIJob(
        order_id=order_id,
        request_id=str(uuid4()),
        kind="analysis",
        status="completed",
        context=build_context(db, order, settings, "analysis", ""),
    )
    db.add(job)
    db.flush()
    db.add(
        Analysis(
            order_id=order_id,
            job_id=job.id,
            result={"recommendation": value},
            created_at=now() + timedelta(seconds=offset),
        )
    )
    return job


def test_combined_filters_and_unassessed_do_not_run_ai(client, settings):
    ids = {
        v: create_order(client, title=f"Кот {v}", source="Kwork")["id"]
        for v in ["discuss", "clarify", "decline", "unassessed"]
    }
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        for value, ident in ids.items():
            if value != "unassessed":
                save_analysis(db, settings, ident, value)
        db.commit()
        count = db.scalar(select(func.count()).select_from(AIJob))
    for value, ident in ids.items():
        response = client.get(
            "/api/orders", params={"recommendation": value, "q": "Кот", "source": "Kwork", "status": "new"}
        )
        assert response.status_code == 200
        assert [o["id"] for o in response.json()] == [ident]
    assert client.get("/api/orders?recommendation=outdated").json() == []
    assert client.get("/api/orders?recommendation=decline&source=Other").json() == []
    assert client.get("/api/orders?recommendation=unknown").status_code == 422
    with session_factory(engine)() as db:
        assert db.scalar(select(func.count()).select_from(AIJob)) == count
    engine.dispose()


@pytest.mark.parametrize("change", ["materials", "conversation", "order", "profile", "accepted", "job_stale"])
def test_old_assessments_are_not_current_recommendations(client, settings, change):
    ident = create_order(client)["id"]
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        job = save_analysis(db, settings, ident)
        order = db.get(Order, ident)
        if change in {"materials", "conversation"}:
            field = change + "_revision"
            setattr(order, field, getattr(order, field) + 1)
        elif change == "order":
            order.title = "Изменённое описание заказа"
        elif change == "profile":
            context = copy.deepcopy(job.context)
            context["versions"]["profile"] = 0
            job.context = context
        elif change == "accepted":
            order.accepted_at = now()
        else:
            job.status = "stale"
        db.commit()
    assert client.get("/api/orders?recommendation=discuss").json() == []
    row = client.get("/api/orders?recommendation=outdated").json()[0]
    assert row["id"] == ident and row["recommendation"]["value"] == "discuss"
    assert row["recommendation"]["outdated"]
    engine.dispose()


def test_latest_saved_analysis_wins_over_old_and_failed_jobs(client, settings):
    ident = create_order(client)["id"]
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        save_analysis(db, settings, ident, "decline", offset=-10)
        latest = save_analysis(db, settings, ident, "clarify")
        expected = latest.id
        db.add(
            AIJob(
                order_id=ident,
                request_id="failed-request",
                kind="analysis",
                status="failed",
                context=latest.context,
            )
        )
        db.commit()
    rows = client.get("/api/orders?recommendation=clarify").json()
    assert len(rows) == 1 and rows[0]["recommendation"]["job_id"] == expected
    assert client.get("/api/orders?recommendation=decline").json() == []
    engine.dispose()
