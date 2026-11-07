import copy
import io

import pytest
from alembic import command
from alembic.config import Config
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject
from sqlalchemy import select

from kotolancer.app import create_app
from kotolancer.config import ROOT, Settings
from kotolancer.db import make_engine, session_factory
from kotolancer.models import Order, now
from kotolancer.profile import current_profile, initialize_profile


def create_order(client, title="Бот для входящих заявок", **fields):
    response = client.post("/api/orders", json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def png():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), (80, 140, 90)).save(out, format="PNG")
    return out.getvalue()


def pdf(with_text=False):
    out = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    if with_text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = StreamObject()
        content.set_data(b"BT /F1 12 Tf 20 100 Td (Incoming requests only) Tj ET")
        page[NameObject("/Contents")] = content
    writer.write(out)
    return out.getvalue()


def test_demo_seed_preserves_profile_contract_without_github_gate(client):
    response = client.get("/api/profile").json()
    profile = response["profile"]
    assert len(profile["projects"]) == 6
    assert len(profile["capabilities"]) == 39
    assert all(c["evidence_ids"] for c in profile["capabilities"] if c["category"] == "A")
    assert {c["label"]: c["category"] for c in profile["capabilities"]}["n8n"] == "B"
    assert next(c for c in profile["capabilities"] if c["label"].startswith("RAG"))["category"] == "B"
    assert next(c for c in profile["capabilities"] if c["label"] == "Python")["category"] == "A"
    assert all(e["source"] == "owner" for e in profile["evidence"])
    assert all(p["name"].startswith("Учебный проект ") for p in profile["projects"])


def test_profile_edit_preserves_previous_revision_and_seed_does_not_overwrite(client, settings):
    old = client.get("/api/profile").json()
    changed = copy.deepcopy(old["profile"])
    changed["capabilities"][0]["scope"] = (
        "Уточнённый владельцем опыт Python; без расширения до банковской экспертизы."
    )
    result = client.put(
        "/api/profile",
        json={"expected_revision": old["revision"], "reason": "Уточнение границ", "profile": changed},
    )
    assert result.status_code == 200
    assert result.json()["revision"] != old["revision"]
    assert client.get(f"/api/profile/revisions/{old['revision']}").json()["profile"] == old["profile"]
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        assert initialize_profile(db) is False
        assert current_profile(db)["profile"] == changed
    engine.dispose()


def test_profile_stale_write_rolls_back_entire_revision(client):
    initial = client.get("/api/profile").json()
    body = {
        "expected_revision": initial["revision"],
        "reason": "Ручное уточнение",
        "profile": initial["profile"],
    }
    assert client.put("/api/profile", json=body).status_code == 200
    assert client.put("/api/profile", json=body).status_code == 409
    assert len(client.get("/api/profile/revisions").json()) == 2


def test_profile_rejects_broken_evidence_and_extra_fields(client):
    body = client.get("/api/profile").json()
    profile = body["profile"]
    profile["capabilities"][0]["evidence_ids"] = ["nonexistent"]
    assert (
        client.put(
            "/api/profile",
            json={"expected_revision": body["revision"], "reason": "bad reference", "profile": profile},
        ).status_code
        == 422
    )
    assert len(client.get("/api/profile/revisions").json()) == 1


def test_order_persists_across_app_restart(client, settings):
    order = create_order(client, description="Работаем с входящими сообщениями", source="Kwork")
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as restarted:
        saved = restarted.get(f"/api/orders/{order['id']}").json()
        assert saved["description"] == "Работаем с входящими сообщениями"
        assert restarted.get("/api/profile").json()["profile"]["projects"]


def test_order_edit_conflict_and_history(client):
    order = create_order(client)
    fields = {key: order[key] for key in ("title", "source", "source_url", "budget", "description", "notes")}
    fields["description"] = "Новая редакция объявления"
    edited = client.put(f"/api/orders/{order['id']}", json={**fields, "expected_version": order["version"]})
    assert edited.status_code == 200
    assert edited.json()["materials_revision"] == 1
    assert (
        client.put(
            f"/api/orders/{order['id']}", json={**fields, "expected_version": order["version"]}
        ).status_code
        == 409
    )
    events = client.get(f"/api/orders/{order['id']}/events").json()
    assert events[0]["detail"]["description"]["before"] == ""
    assert events[0]["detail"]["description"]["after"] == fields["description"]


def test_search_status_and_source_filters(client):
    one = create_order(client, title="Telegram помощник", source="Kwork", description="API интеграция")
    create_order(client, title="Лендинг", source="FL")
    assert len(client.get("/api/orders?q=интеграция&source=Kwork").json()) == 1
    assert (
        client.post(
            f"/api/orders/{one['id']}/status", json={"expected_version": one["version"], "status": "declined"}
        ).status_code
        == 200
    )
    assert len(client.get("/api/orders?status=declined").json()) == 1
    assert client.get("/api/orders?q=неизвестное").json() == []


def test_accepted_orders_cannot_reenter_discussion(client, settings):
    order = create_order(client)
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db:
        saved = db.get(Order, order["id"])
        saved.accepted_at = now()
        db.commit()
    engine.dispose()
    current = client.get(f"/api/orders/{order['id']}").json()
    assert current["consulting_allowed"] is False
    assert (
        client.post(
            f"/api/orders/{order['id']}/status",
            json={"expected_version": current["version"], "status": "discussion"},
        ).status_code
        == 409
    )


def test_inbox_attach_does_not_mix_orders(client):
    first, second = create_order(client), create_order(client, title="Другой заказ")
    material = client.post(
        "/api/materials/text", json={"title": "Уточнение", "text": "Только входящие"}
    ).json()
    assert len(client.get("/api/materials").json()) == 1
    assert (
        client.post(f"/api/materials/{material['id']}/attach", json={"order_id": first["id"]}).status_code
        == 200
    )
    assert client.get("/api/materials").json() == []
    assert len(client.get(f"/api/materials?order_id={first['id']}").json()) == 1
    assert client.get(f"/api/materials?order_id={second['id']}").json() == []
    assert (
        client.post(f"/api/materials/{material['id']}/attach", json={"order_id": second["id"]}).status_code
        == 409
    )
    assert client.get(f"/api/orders/{first['id']}").json()["materials_revision"] == 1
    assert client.get(f"/api/orders/{second['id']}").json()["materials_revision"] == 0


def test_missing_target_keeps_material_in_inbox(client):
    material = client.post("/api/materials/text", json={"title": "ТЗ", "text": "Не потерять"}).json()
    assert (
        client.post(f"/api/materials/{material['id']}/attach", json={"order_id": "missing"}).status_code
        == 404
    )
    assert client.get("/api/materials").json()[0]["order_id"] is None


@pytest.mark.parametrize(
    "name,content,status",
    [
        ("image.png", png(), "needs_visual"),
        ("scan.pdf", pdf(), "partial"),
        ("brief.pdf", pdf(with_text=True), "partial"),
        ("broken.pdf", b"not a pdf", "failed"),
    ],
)
def test_file_original_roundtrip_and_honest_extraction(client, name, content, status):
    order = create_order(client)
    response = client.post(f"/api/materials/file?order_id={order['id']}", files={"file": (name, content)})
    assert response.status_code == 201, response.text
    material = response.json()
    assert material["extraction_status"] == status
    assert material["warnings"]
    assert client.get(f"/api/materials/{material['id']}/file").content == content
    assert client.get(f"/api/orders/{order['id']}").json()["material_count"] == 1
    if name == "brief.pdf":
        assert "Incoming requests" in material["blocks"][0]["text"]


def test_docx_preserves_block_order_tables_and_warns_about_images(client):
    document = Document()
    document.add_paragraph("Начало ТЗ")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Поле"
    table.cell(0, 1).text = "Значение"
    document.add_paragraph("Конец ТЗ")
    document.add_picture(io.BytesIO(png()))
    out = io.BytesIO()
    document.save(out)
    result = client.post("/api/materials/file", files={"file": ("../ТЗ.docx", out.getvalue())})
    assert result.status_code == 201
    material = result.json()
    assert material["title"] == "ТЗ.docx"
    assert [block["text"] for block in material["blocks"]] == ["Начало ТЗ", "Поле | Значение", "Конец ТЗ"]
    assert any("изображения (1)" in note for note in material["warnings"])


def test_upload_limits_and_unsupported_files_do_not_create_records(client, settings):
    settings.max_upload_bytes = 8
    assert client.post("/api/materials/file", files={"file": ("big.pdf", b"x" * 9)}).status_code == 413
    assert client.post("/api/materials/file", files={"file": ("script.html", b"x")}).status_code == 422
    assert client.post("/api/materials/file", files={"file": ("empty.pdf", b"")}).status_code == 422
    assert client.get("/api/materials").json() == []


def test_text_validation_and_unsafe_links(client):
    assert client.post("/api/orders", json={"title": " "}).status_code == 422
    assert (
        client.post("/api/orders", json={"title": "Заказ", "source_url": "javascript:alert(1)"}).status_code
        == 422
    )
    assert client.post("/api/materials/text", json={"title": " ", "text": " "}).status_code == 422


def test_all_private_routes_require_access(client):
    order = create_order(client)
    material = client.post("/api/materials/file", files={"file": ("test.png", png())}).json()
    client.headers.clear()
    for path in (
        "/api/health",
        "/api/profile",
        "/api/profile/revisions",
        "/api/orders",
        f"/api/orders/{order['id']}",
        "/api/materials",
        f"/api/materials/{material['id']}/file",
    ):
        assert client.get(path).status_code == 403


def test_local_mode_rejects_foreign_origin_and_host(client):
    assert client.get("/api/profile", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.get("/api/profile", headers={"Host": "attacker.invalid"}).status_code == 403


def test_production_cannot_enable_local_bypass(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", auth_mode="local")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_mode="token", access_token="short")
    protected = settings.model_copy(
        update={"environment": "production", "auth_mode": "token", "access_token": "x" * 40}
    )
    with TestClient(create_app(protected), headers={"X-Koto-Client": "workspace"}) as client:
        assert client.get("/api/profile").status_code == 401
        assert client.get("/api/profile", headers={"Authorization": f"Bearer {'x' * 40}"}).status_code == 200


def test_migration_schema_matches_models(settings):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.check(config)


def test_two_orm_sessions_do_not_silently_overwrite_order(client, settings):
    from sqlalchemy.orm.exc import StaleDataError

    order = create_order(client)
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    with factory() as one, factory() as two:
        first = one.scalar(select(Order).where(Order.id == order["id"]))
        second = two.get(Order, order["id"])
        first.notes = "Первая правка"
        one.commit()
        second.notes = "Устаревшая правка"
        with pytest.raises(StaleDataError):
            two.commit()
        two.rollback()
    engine.dispose()
