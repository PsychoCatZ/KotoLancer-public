from sqlalchemy import func, select

from kotolancer.consultant import process_next
from kotolancer.models import AIRun, Analysis
from tests.test_consultant import consultant as consultant
from tests.test_consultant import state, submit
from tests.test_foundation import create_order, png


def move(client, order, action):
    return client.post(
        f"/api/orders/{order['id']}/visibility",
        json={"action": action, "expected_version": order["version"]},
    )


def test_archive_delete_and_restore_keep_order_status_and_materials(client):
    order = create_order(client)
    material = client.post(
        "/api/materials/text", json={"title": "ТЗ", "text": "Описание", "order_id": order["id"]}
    ).json()
    order = client.get(f"/api/orders/{order['id']}").json()
    archived = move(client, order, "archive")
    assert archived.status_code == 200
    archived = archived.json()
    assert client.get("/api/orders").json() == []
    assert client.get("/api/orders?view=archived").json()[0]["id"] == order["id"]
    assert move(client, order, "delete").status_code == 409  # stale browser tab
    deleted = move(client, archived, "delete").json()
    assert client.get("/api/orders?view=archived").json() == []
    assert client.get("/api/orders?view=deleted").json()[0]["id"] == order["id"]
    for suffix in ["", "/consultant", "/work", "/events"]:
        assert client.get(f"/api/orders/{order['id']}{suffix}").status_code == 404
    assert client.get(f"/api/materials?order_id={order['id']}").status_code == 404
    assert client.get("/api/materials").json() == []  # not detached into Inbox
    restored = move(client, deleted, "restore").json()
    assert restored["status"] == order["status"]
    assert restored["archived_at"] is restored["deleted_at"] is None
    assert client.get(f"/api/materials?order_id={order['id']}").json()[0]["id"] == material["id"]


def test_deletion_preserves_ai_spending_and_blocks_active_jobs(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    assert submit(client, order).status_code == 202
    assert move(client, order, "delete").status_code == 409
    assert process_next(factory, settings, provider)
    with factory() as db:
        run = db.scalar(select(AIRun))
        run.reserved_nusd = 123456789
        db.commit()
    current = client.get(f"/api/orders/{order['id']}").json()
    deleted = move(client, current, "delete").json()
    assert submit(client, order).status_code == 404
    with factory() as db:
        assert db.scalar(select(func.sum(AIRun.reserved_nusd))) == 123456789
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1
    assert move(client, deleted, "restore").status_code == 200
    assert len(state(client, order)["analyses"]) == 1


def test_delete_only_unattached_inbox_materials(client, settings):
    inbox = client.post("/api/materials/file", files={"file": ("clipboard.png", png(), "image/png")}).json()
    original = list(settings.storage_dir.iterdir())
    assert len(original) == 1
    assert client.delete(f"/api/materials/{inbox['id']}").status_code == 200
    assert client.get("/api/materials").json() == []
    assert not original[0].exists()
    assert client.get(f"/api/materials/{inbox['id']}/file").status_code == 404
    order = create_order(client)
    assert (
        client.post(f"/api/materials/{inbox['id']}/attach", json={"order_id": order["id"]}).status_code == 404
    )
    attached = client.post("/api/materials/file", files={"file": ("attached.png", png(), "image/png")}).json()
    assert (
        client.post(f"/api/materials/{attached['id']}/attach", json={"order_id": order["id"]}).status_code
        == 200
    )
    assert client.delete(f"/api/materials/{attached['id']}").status_code == 409
    assert client.get(f"/api/materials/{attached['id']}/file").status_code == 200


def test_deleted_order_file_cannot_be_downloaded(client):
    order = create_order(client)
    material = client.post(
        f"/api/materials/file?order_id={order['id']}", files={"file": ("test.png", png(), "image/png")}
    ).json()
    order = client.get(f"/api/orders/{order['id']}").json()
    deleted = move(client, order, "delete").json()
    assert client.get(f"/api/materials/{material['id']}/file").status_code == 404
    move(client, deleted, "restore")
    assert client.get(f"/api/materials/{material['id']}/file").status_code == 200
