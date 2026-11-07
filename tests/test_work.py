import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from kotolancer.consultant import process_next
from kotolancer.models import AIJob
from tests.test_consultant import analysis_result, state, submit
from tests.test_consultant import consultant as consultant
from tests.test_foundation import create_order


def today():
    return datetime.now(ZoneInfo("Asia/Krasnoyarsk")).date().isoformat()


def get(client, order):
    return client.get(f"/api/orders/{order['id']}").json()


def work(client, order):
    return client.get(f"/api/orders/{order['id']}/work").json()


def accept(client, order, **changes):
    return client.post(
        f"/api/orders/{order['id']}/accept",
        json={
            "expected_version": get(client, order)["version"],
            "confirm": True,
            "agreed_scope": "Один входящий бот",
            "agreed_stack": "Python",
            "terms": "Только согласованный объём",
            **changes,
        },
    )


def finish(client, order, **changes):
    return client.post(
        f"/api/orders/{order['id']}/outcomes",
        json={
            "expected_version": get(client, order)["version"],
            "result": "succeeded",
            "delivered": "Сделан входящий бот, без массовых рассылок",
            "validation": "Проверены входящий запрос и сохранённый результат",
            "excluded": "Без CRM и RAG",
            "learned": "Практическая интеграция API",
            "capability_ids": ["cap-001", "cap-038"],
            "finished_on": today(),
            **changes,
        },
    )


def change_status(client, order, status):
    return client.post(
        f"/api/orders/{order['id']}/work/status",
        json={
            "expected_version": get(client, order)["version"],
            "status": status,
            "reason": "Исправление учёта владельцем",
        },
    )


def review(client, order, proposal, action, **changes):
    return client.post(
        f"/api/orders/{order['id']}/experience/{proposal['id']}/{action}",
        json={
            "expected_version": get(client, order)["version"],
            "expected_profile_revision": client.get("/api/profile").json()["revision"],
            "reason": "Проверил результат и границы опыта",
            **changes,
        },
    )


def test_acceptance_is_explicit_and_snapshot_is_not_edited(client):
    order = create_order(client)
    url = f"/api/orders/{order['id']}/accept"
    assert client.post(url, json={"expected_version": order["version"], "confirm": False}).status_code == 422
    accepted = accept(client, order)
    assert accepted.status_code == 200
    first = accepted.json()["accepted_at"]
    assert first and not accepted.json()["consulting_allowed"]
    original = work(client, order)["work"]["acceptance_snapshot"]
    body = {
        "expected_version": get(client, order)["version"],
        "agreed_scope": "Расширение согласовано отдельно",
        "agreed_stack": "Python + API",
        "terms": "Новая договорённость",
        "due_on": today(),
    }
    assert client.put(f"/api/orders/{order['id']}/work", json=body).status_code == 200
    assert work(client, order)["work"]["acceptance_snapshot"] == original
    assert accept(client, order).status_code == 409
    assert change_status(client, order, "cancelled").status_code == 200
    assert change_status(client, order, "in_progress").status_code == 200
    assert get(client, order)["accepted_at"] == first
    assert (
        client.post(
            f"/api/orders/{order['id']}/status",
            json={"expected_version": get(client, order)["version"], "status": "discussion"},
        ).status_code
        == 409
    )
    assert client.get("/api/profile").json()["revision"] == 1


def test_queued_and_late_ai_never_publish_after_acceptance(consultant):
    client, provider, factory, settings = consultant
    queued = create_order(client)
    job = submit(client, queued).json()
    assert accept(client, queued).status_code == 200
    process_next(factory, settings, provider)
    assert state(client, queued)["analyses"] == [] and provider.calls == []
    late = create_order(client)
    submit(client, late)
    provider.hook = lambda: accept(client, late)
    process_next(factory, settings, provider)
    assert state(client, late)["analyses"] == []
    assert state(client, late)["jobs"][0]["status"] == "suppressed"
    assert submit(client, late, "chat", "Верни консультации").status_code == 409
    assert (
        client.put(
            f"/api/orders/{late['id']}/drafts/missing", json={"expected_version": 1, "content": "Текст"}
        ).status_code
        == 409
    )
    with factory() as db:
        assert db.get(AIJob, job["id"]).status == "suppressed"


def test_known_ethical_refusal_prevents_acceptance(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    provider.payload = analysis_result()
    provider.payload["ethics"]["verdict"] = "reject"
    submit(client, order)
    process_next(factory, settings, provider)
    assert accept(client, order).status_code == 409
    assert get(client, order)["accepted_at"] is None


def test_success_proposes_bounded_evidence_and_owner_confirms_once(client):
    order = create_order(client)
    before = client.get("/api/profile").json()
    assert accept(client, order).status_code == 200
    assert finish(client, order).status_code == 201
    assert get(client, order)["status"] == "completed"
    assert client.get("/api/profile").json() == before
    proposal = work(client, order)["proposals"][0]
    assert "Без CRM и RAG" in proposal["statement"]
    assert review(client, order, proposal, "accept").status_code == 200
    after = client.get("/api/profile").json()
    assert after["revision"] == 2
    assert [c["category"] for c in after["profile"]["capabilities"]] == [
        c["category"] for c in before["profile"]["capabilities"]
    ]
    evidence = after["profile"]["evidence"][-1]
    assert evidence["source"] == "completed_order" and "Без CRM и RAG" in evidence["statement"]
    assert "cap-038" in [
        c["id"] for c in after["profile"]["capabilities"] if evidence["id"] in c["evidence_ids"]
    ]
    assert review(client, order, proposal, "accept").json()["revision"] == 2
    assert client.get("/api/profile").json()["revision"] == 2
    assert change_status(client, order, "closed").status_code == 200
    assert get(client, order)["consulting_allowed"] is False


@pytest.mark.parametrize("result", ["partial", "failed"])
def test_non_success_does_not_offer_experience(client, result):
    order = create_order(client)
    accept(client, order)
    assert finish(client, order, result=result).status_code == 201
    assert work(client, order)["proposals"] == []
    assert client.get("/api/profile").json()["revision"] == 1
    assert get(client, order)["status"] == ("cancelled" if result == "failed" else "completed")


def test_profile_edit_conflicts_then_refresh_preserves_manual_changes(client):
    order = create_order(client)
    accept(client, order)
    finish(client, order)
    p = work(client, order)["proposals"][0]
    profile = client.get("/api/profile").json()
    updated = copy.deepcopy(profile["profile"])
    updated["rules"].append("Ручная поправка владельца")
    assert (
        client.put(
            "/api/profile",
            json={"expected_revision": profile["revision"], "reason": "Ручная поправка", "profile": updated},
        ).status_code
        == 200
    )
    assert review(client, order, p, "accept").status_code == 409
    assert client.get("/api/profile").json()["profile"] == updated
    assert review(client, order, p, "refresh").status_code == 200
    assert review(client, order, p, "accept").status_code == 200
    assert client.get("/api/profile").json()["profile"]["rules"][-1] == "Ручная поправка владельца"


def test_reopening_invalidates_pending_proposal_and_old_outcome_is_kept(client):
    order = create_order(client)
    accept(client, order)
    finish(client, order)
    old = work(client, order)
    p = old["proposals"][0]
    assert change_status(client, order, "in_progress").status_code == 200
    assert work(client, order)["work"]["outcome_id"] is None
    assert review(client, order, p, "accept").status_code == 409
    assert finish(client, order, delivered="Исправленный фактический объём").status_code == 201
    data = work(client, order)
    assert len(data["outcomes"]) == 2
    assert data["outcomes"][-1]["delivered"] == old["outcomes"][0]["delivered"]
    assert data["outcomes"][0]["revision"] == 2


def test_confirmed_evidence_must_be_revoked_before_correcting_fact(client):
    order = create_order(client)
    accept(client, order)
    finish(client, order)
    p = work(client, order)["proposals"][0]
    review(client, order, p, "accept")
    assert finish(client, order, delivered="Иная работа").status_code == 409
    assert change_status(client, order, "in_progress").status_code == 409
    assert review(client, order, p, "revoke").status_code == 200
    profile = client.get("/api/profile").json()
    assert profile["revision"] == 3
    assert all(e["id"] != "completed-" + p["id"] for e in profile["profile"]["evidence"])
    assert change_status(client, order, "in_progress").status_code == 200
    assert get(client, order)["consulting_allowed"] is False


def test_rejecting_and_stale_order_do_not_mutate_profile(client):
    order = create_order(client)
    accept(client, order)
    old_version = get(client, order)["version"]
    assert finish(client, order, capability_ids=["not-real"]).status_code == 422
    assert finish(client, order).status_code == 201
    p = work(client, order)["proposals"][0]
    assert review(client, order, p, "accept", expected_version=old_version).status_code == 409
    assert review(client, order, p, "reject").status_code == 200
    assert client.get("/api/profile").json()["revision"] == 1
    assert review(client, order, p, "accept").status_code == 409


def test_unaccepted_order_dates_and_protected_routes(client):
    order = create_order(client)
    assert finish(client, order).status_code == 409
    assert (
        accept(
            client,
            order,
            started_on=(datetime.now(ZoneInfo("Asia/Krasnoyarsk")).date() + timedelta(days=1)).isoformat(),
        ).status_code
        == 422
    )
    assert accept(client, order).status_code == 200
    assert change_status(client, order, "closed").status_code == 409
    client.headers.clear()
    assert client.get(f"/api/orders/{order['id']}/work").status_code == 403
    assert client.post(f"/api/orders/{order['id']}/outcomes", json={}).status_code == 403


def test_two_accepts_cannot_create_two_snapshots(client):
    from concurrent.futures import ThreadPoolExecutor

    order = create_order(client)
    payload = {
        "expected_version": order["version"],
        "confirm": True,
        "agreed_scope": "Первый согласованный объём",
    }

    def send(_):
        return client.post(f"/api/orders/{order['id']}/accept", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    events = client.get(f"/api/orders/{order['id']}/events").json()
    assert sum(e["kind"] == "order_accepted" for e in events) == 1
    assert work(client, order)["work"]["acceptance_snapshot"]["agreed_scope"] == payload["agreed_scope"]


def test_parallel_profile_confirmations_do_not_overwrite_each_other(client):
    from concurrent.futures import ThreadPoolExecutor

    orders = [create_order(client, title="Первый"), create_order(client, title="Второй")]
    requests = []
    for order in orders:
        accept(client, order)
        finish(client, order)
        proposal = work(client, order)["proposals"][0]
        requests.append(
            (
                order,
                proposal,
                {
                    "expected_version": get(client, order)["version"],
                    "expected_profile_revision": 1,
                    "reason": "Подтверждён фактический объём",
                },
            )
        )

    def send(item):
        order, p, body = item
        return client.post(f"/api/orders/{order['id']}/experience/{p['id']}/accept", json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, requests))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert client.get("/api/profile").json()["revision"] == 2
    assert (
        sum(
            e["source"] == "completed_order" for e in client.get("/api/profile").json()["profile"]["evidence"]
        )
        == 1
    )


def test_sole_a_evidence_cannot_be_revoked_without_owner_category_correction(client):
    order = create_order(client)
    accept(client, order)
    finish(client, order)
    p = work(client, order)["proposals"][0]
    review(client, order, p, "accept")
    profile = client.get("/api/profile").json()
    updated = copy.deepcopy(profile["profile"])
    c = next(c for c in updated["capabilities"] if c["id"] == "cap-038")
    c["category"] = "A"
    c["evidence_ids"] = ["completed-" + p["id"]]
    assert (
        client.put(
            "/api/profile",
            json={
                "expected_revision": profile["revision"],
                "reason": "Владелец уточнил компетенцию",
                "profile": updated,
            },
        ).status_code
        == 200
    )
    assert review(client, order, p, "revoke").status_code == 409
    assert client.get("/api/profile").json()["profile"] == updated
    assert work(client, order)["proposals"][0]["status"] == "accepted"


def test_upgrade_from_budget_schema_preserves_existing_rows_and_reservations(client, settings):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from kotolancer.config import ROOT
    from kotolancer.db import make_engine, session_factory
    from kotolancer.models import AIJob, AIRun

    order = create_order(client, title="Заказ до обновления", notes="Не менять при миграции")
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.downgrade(config, "0005_production_ai_budget")
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        job = AIJob(
            order_id=order["id"], request_id="migration-proof", kind="analysis", status="failed", context={}
        )
        db.add(job)
        db.flush()
        db.add(
            AIRun(
                job_id=job.id,
                provider="fixture-only",
                model="gpt-6-luna",
                rules_version="historical",
                schema_version="v1",
                reserved_nusd=123456789,
            )
        )
        db.commit()
        job_id = job.id
    command.upgrade(config, "head")
    assert get(client, order)["notes"] == "Не менять при миграции"
    assert get(client, order)["accepted_at"] is None
    assert client.get("/api/profile").json()["revision"] == 1
    with session_factory(engine)() as db:
        assert db.get(AIRun, job_id).reserved_nusd == 123456789
    assert {"order_work", "order_outcomes", "completion_proposals"}.issubset(
        inspect(engine).get_table_names()
    )
    engine.dispose()
