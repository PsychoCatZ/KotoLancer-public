import copy
import io
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openai import OpenAI
from sqlalchemy import func, select

from kotolancer.ai_context import build_context, input_content
from kotolancer.ai_provider import OpenAIProvider, ProviderError, ProviderResult
from kotolancer.ai_schemas import AnalysisResult, DraftResult
from kotolancer.app import create_app
from kotolancer.consultant import claim_next, process_next
from kotolancer.db import session_factory
from kotolancer.models import AIJob, Order, OrderMessage, ProfileHead, ProfileRevision, now
from tests.test_foundation import create_order, pdf, png


def analysis_result():
    return {
        "summary": "Предварительно посильная интеграция с входящими обращениями.",
        "technical_verdict": "capable",
        "technical_reason": "Базовые компоненты знакомы.",
        "ethics": {"verdict": "clear", "reason": "Работа с входящими обращениями.", "source_ids": ["order"]},
        "requirements": [{"text": "Принимать сообщения пользователя", "source_ids": ["order"]}],
        "components": [
            {
                "name": "Python",
                "category": "A",
                "required": True,
                "explanation": "Практический опыт владельца.",
                "source_ids": ["capability:cap-001"],
                "verification": None,
            }
        ],
        "scope": "Один бот для входящих заявок.",
        "questions": [],
        "client_message": None,
        "risks": [],
        "proposed_stack": [{"technology": "Python", "purpose": "Обработка запросов"}],
        "effort": None,
        "changes_from_previous": None,
    }


class FixtureProvider:
    """Explicit dependency injection in tests only. Never selected by runtime configuration."""

    name = "test-fixture"

    def __init__(self):
        self.calls = []
        self.payload = None
        self.hook = None
        self.error = None

    def generate(self, kind, context):
        self.calls.append(copy.deepcopy(context))
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        if kind == "analysis":
            payload = self.payload or analysis_result()
        elif kind == "chat":
            payload = self.payload or {
                "answer": "Уточни источник данных и предел объёма.",
                "ethics": {"verdict": "clear", "reason": "Нет указаний на спам.", "source_ids": ["order"]},
                "source_ids": ["order"],
                "suggests_reanalysis": True,
            }
        else:
            payload = self.payload or {
                "content": "Есть практический опыт Python. Уточните источник данных.",
                "experience_claims": [
                    {"text": "Практический опыт Python", "source_ids": ["capability:cap-001"]}
                ],
                "ethics": {"verdict": "clear", "reason": "Входящие обращения", "source_ids": ["order"]},
            }
        return ProviderResult(
            json.dumps(payload, ensure_ascii=False),
            "fixture-only",
            "fixture-response",
            {"input_tokens": 100, "output_tokens": 200},
        )


@pytest.fixture
def consultant(settings):
    provider = FixtureProvider()
    settings.openai_model = "fixture-only"
    app = create_app(settings, ai_provider=provider)
    with TestClient(app, headers={"X-Koto-Client": "workspace"}) as client:
        yield client, provider, session_factory(app.state.engine), settings


def submit(client, order, kind="analysis", message="", request_id=None):
    return client.post(
        f"/api/orders/{order['id']}/consultant",
        json={"kind": kind, "message": message, "request_id": request_id or str(uuid4())},
    )


def state(client, order):
    return client.get(f"/api/orders/{order['id']}/consultant").json()


def test_without_key_no_job_no_call_and_protected_routes(client):
    order = create_order(client)
    assert submit(client, order).status_code == 503
    assert state(client, order)["jobs"] == []
    client.headers.clear()
    assert client.get(f"/api/orders/{order['id']}/consultant").status_code == 403
    assert submit(client, order).status_code == 403


def test_analysis_persists_provenance_and_actual_usage(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client, budget="Не влияет на пригодность")
    queued = submit(client, order)
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"
    assert process_next(factory, settings, provider)
    data = state(client, order)
    result = data["analyses"][0]
    assert result["result"]["recommendation"] == "discuss"
    assert result["request"]["versions"]["profile"] == 1
    assert result["request"]["usage"]["input_tokens"] == 100
    assert result["request"]["outdated"] is False
    assert "budget" not in provider.calls[0]["order"]
    assert len(provider.calls[0]["profile"]["projects"]) == 6
    assert process_next(factory, settings, provider) is False


def test_idempotency_and_one_active_job_per_order(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    request_id = str(uuid4())
    first = submit(client, order, request_id=request_id)
    assert submit(client, order, request_id=request_id).json()["id"] == first.json()["id"]
    assert (
        submit(client, order, kind="chat", message="Другое действие", request_id=request_id).status_code
        == 409
    )
    assert submit(client, order).status_code == 409
    process_next(factory, settings, provider)
    assert submit(client, order, request_id=request_id).json()["status"] == "completed"
    assert len(provider.calls) == 1


def test_materials_and_messages_never_cross_orders(consultant):
    client, provider, factory, settings = consultant
    one, two = create_order(client), create_order(client, title="Чужой заказ")
    client.post("/api/materials/text", json={"title": "ТЗ", "text": "ONLY-ORDER-TWO", "order_id": two["id"]})
    submit(client, one, "chat", "Вопрос первого заказа")
    process_next(factory, settings, provider)
    assert "ONLY-ORDER-TWO" not in json.dumps(provider.calls[0])
    assert state(client, two)["messages"] == []
    assert len(state(client, one)["messages"]) == 2


def test_conversation_reanalysis_draft_edit_and_restart(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    submit(client, order)
    process_next(factory, settings, provider)
    old_id = state(client, order)["analyses"][0]["id"]
    submit(client, order, "chat", "Новый источник данных уже согласован")
    process_next(factory, settings, provider)
    assert state(client, order)["analyses"][0]["request"]["outdated"]
    assert submit(client, order, "draft").status_code == 409
    submit(client, order)
    process_next(factory, settings, provider)
    assert provider.calls[-1]["previous_analysis"]["id"] == old_id
    assert len(provider.calls[-1]["messages"]) == 2
    submit(client, order, "draft")
    process_next(factory, settings, provider)
    draft = state(client, order)["drafts"][0]
    body = {"expected_version": draft["version"], "content": "Исправленный Котом текст"}
    assert client.put(f"/api/orders/{order['id']}/drafts/{draft['id']}", json=body).status_code == 200
    assert client.put(f"/api/orders/{order['id']}/drafts/{draft['id']}", json=body).status_code == 409
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as restarted:
        saved = state(restarted, order)
        assert saved["drafts"][0]["content"] == body["content"]
        assert len(saved["analyses"]) == 2
        assert len(saved["messages"]) == 2


@pytest.mark.parametrize("change", ["materials", "profile", "accepted"])
def test_late_result_cannot_publish_after_state_change(consultant, change):
    client, provider, factory, settings = consultant
    order = create_order(client)
    submit(client, order)

    def mutate():
        with factory() as db:
            row = db.get(Order, order["id"])
            if change == "accepted":
                row.accepted_at = now()
            elif change == "materials":
                row.materials_revision += 1
            else:
                head = db.get(ProfileHead, 1)
                revision = ProfileRevision(
                    snapshot=db.get(ProfileRevision, head.revision_id).snapshot, reason="Уточнение владельца"
                )
                db.add(revision)
                db.flush()
                head.revision_id = revision.id
            db.commit()

    provider.hook = mutate
    process_next(factory, settings, provider)
    data = state(client, order)
    assert data["jobs"][0]["status"] == ("suppressed" if change == "accepted" else "stale")
    assert data["jobs"][0]["usage"]["output_tokens"] == 200
    if change == "accepted":
        assert data["analyses"] == []
    else:
        assert len(data["analyses"]) == 1
        assert data["analyses"][0]["request"]["outdated"] is True


def test_stale_queued_job_does_not_call_provider(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    submit(client, order)
    client.post(
        "/api/materials/text", json={"title": "Новое ТЗ", "text": "Изменён объём", "order_id": order["id"]}
    )
    assert process_next(factory, settings, provider)
    assert provider.calls == []
    assert state(client, order)["jobs"][0]["status"] == "stale"


def test_accepted_order_blocks_analysis_chat_and_draft(consultant):
    client, _, factory, _ = consultant
    order = create_order(client)
    with factory() as db:
        db.get(Order, order["id"]).accepted_at = now()
        db.commit()
    for kind in ("analysis", "chat", "draft"):
        assert submit(client, order, kind, "Поговорим").status_code == 409
    assert state(client, order)["jobs"] == []


@pytest.mark.parametrize("bad", ["unknown_source", "unproven_A", "extra_action", "bad_shape"])
def test_invalid_model_results_do_not_become_analyses(consultant, bad):
    client, provider, factory, settings = consultant
    order = create_order(client)
    payload = analysis_result()
    if bad == "unknown_source":
        payload["requirements"][0]["source_ids"] = ["github:not-read"]
    elif bad == "unproven_A":
        payload["components"][0]["source_ids"] = ["order"]
    elif bad == "extra_action":
        payload["change_order_status"] = "accepted"
    else:
        payload = {"text": "Не тот формат"}
    provider.payload = payload
    submit(client, order)
    process_next(factory, settings, provider)
    data = state(client, order)
    assert data["jobs"][0]["error_code"] == "invalid_output"
    assert data["analyses"] == []
    assert client.get(f"/api/orders/{order['id']}").json()["status"] == "new"


@pytest.mark.parametrize(
    "category,expected", [("C", "not_suitable"), ("unknown", "needs_check"), ("B", "capable")]
)
def test_required_component_controls_technical_outcome(consultant, category, expected):
    client, provider, factory, settings = consultant
    order = create_order(client)
    payload = analysis_result()
    payload["components"][0]["category"] = category
    provider.payload = payload
    submit(client, order)
    process_next(factory, settings, provider)
    assert state(client, order)["analyses"][0]["result"]["technical_verdict"] == expected


@pytest.mark.parametrize("verdict,recommendation", [("reject", "decline"), ("clarify", "clarify")])
def test_ethics_is_independent_and_blocks_sales_draft(consultant, verdict, recommendation):
    client, provider, factory, settings = consultant
    order = create_order(client)
    payload = analysis_result()
    payload["ethics"] = {
        "verdict": verdict,
        "reason": "Нежелательная рассылка или неясное согласие получателей.",
        "source_ids": ["order"],
    }
    provider.payload = payload
    submit(client, order)
    process_next(factory, settings, provider)
    result = state(client, order)["analyses"][0]["result"]
    assert result["technical_verdict"] == "capable"
    assert result["recommendation"] == recommendation
    assert submit(client, order, "draft").status_code == 409
    if verdict == "reject":
        assert result["proposed_stack"] == [] and result["components"] == []


def test_no_automatic_retry_after_uncertain_network_error(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    provider.error = ProviderError("unknown_outcome", "Связь потеряна; списание неизвестно.")
    submit(client, order)
    process_next(factory, settings, provider)
    assert process_next(factory, settings, provider) is False
    assert len(provider.calls) == 1
    assert state(client, order)["jobs"][0]["error_code"] == "unknown_outcome"


def test_expired_worker_is_fenced_and_not_automatically_retried(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    submit(client, order)

    def expire():
        with factory() as db:
            row = db.scalar(select(AIJob).where(AIJob.order_id == order["id"]))
            row.lease_until = now() - timedelta(seconds=1)
            db.commit()
        with factory() as db:
            assert claim_next(db, settings) is None

    provider.hook = expire
    process_next(factory, settings, provider)
    data = state(client, order)
    assert data["jobs"][0]["status"] == "failed"
    assert data["analyses"] == []
    assert len(provider.calls) == 1


def test_daily_limit_and_context_limit_leave_no_partial_messages(consultant):
    client, _, factory, settings = consultant
    settings.openai_daily_request_limit = 1
    one, two = create_order(client), create_order(client)
    assert submit(client, one).status_code == 202
    assert submit(client, two, "chat", "Не должно сохраниться").status_code == 429
    assert state(client, two)["messages"] == []
    settings.openai_daily_request_limit = 20
    settings.ai_max_context_chars = 1000
    assert submit(client, two, "chat", "Тоже не сохраняется").status_code == 422
    assert state(client, two)["jobs"] == []
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(OrderMessage)) == 0


def test_pdf_and_images_go_to_multimodal_input_without_base64_in_audit(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    for name, data in (("brief.pdf", pdf(with_text=True)), ("screenshot.png", png())):
        assert (
            client.post(
                f"/api/materials/file?order_id={order['id']}", files={"file": (name, data)}
            ).status_code
            == 201
        )
    submit(client, order)
    with factory() as db:
        context = db.scalar(select(AIJob).where(AIJob.order_id == order["id"])).context
    assert "base64" not in json.dumps(context)
    content = input_content(context, settings)
    assert any(item["type"] == "input_image" for item in content)
    assert any(item["type"] == "input_file" for item in content)
    assert context["size"]["attachments"] == 2


def test_docx_includes_embedded_image_and_keeps_layout_limitation(consultant):
    client, _, factory, settings = consultant
    order = create_order(client)
    document = Document()
    document.add_paragraph("Текст ТЗ")
    document.add_picture(io.BytesIO(png()))
    out = io.BytesIO()
    document.save(out)
    client.post(f"/api/materials/file?order_id={order['id']}", files={"file": ("brief.docx", out.getvalue())})
    submit(client, order)
    with factory() as db:
        context = db.scalar(select(AIJob).where(AIJob.order_id == order["id"])).context
    assert context["attachments"][0]["entry"].startswith("word/media/")
    assert context["coverage"][0]["warnings"]
    assert any(item["type"] == "input_image" for item in input_content(context, settings))


@pytest.mark.parametrize("kind", ["analysis", "draft"])
def test_sdk_contract_is_exercised_without_network(consultant, kind):
    client, _, factory, settings = consultant
    order = create_order(client)
    with factory() as db:
        context = build_context(db, db.get(Order, order["id"]), settings, kind, "")
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "response-fixture",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "fixture-only",
                "output": [
                    {
                        "id": "msg_fixture",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    analysis_result()
                                    if kind == "analysis"
                                    else {
                                        "content": "Здравствуйте! Есть опыт Python. Подскажите источники данных?",
                                        "experience_claims": [],
                                        "ethics": {
                                            "verdict": "clear",
                                            "reason": "Входящие запросы",
                                            "source_ids": ["order"],
                                        },
                                    }
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    sdk = OpenAI(
        api_key="test-key-not-live",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    result = OpenAIProvider(settings, sdk).generate(kind, context)
    assert (AnalysisResult if kind == "analysis" else DraftResult).model_validate_json(result.text)
    assert captured[0]["instructions"] == context["rules"]["text"]
    assert captured[0]["store"] is False
    assert captured[0]["service_tier"] == "default"
    assert captured[0]["text"]["format"]["strict"] is True
    schema = captured[0]["text"]["format"]["schema"]
    for definition in ("Claim", "Component", "Ethics") if kind == "analysis" else ("Claim", "Ethics"):
        allowed = schema["$defs"][definition]["properties"]["source_ids"]["items"]["enum"]
        assert set(allowed) == set(context["sources"])
        assert "capability:capability:cap-029" not in allowed
    if kind == "draft":
        assert "заказчику" in schema["properties"]["content"]["description"]
    assert "tools" not in captured[0]
    assert result.usage["input_tokens"] == 10


def test_provider_authentication_error_is_redacted(consultant):
    client, _, factory, settings = consultant
    order = create_order(client)
    with factory() as db:
        context = build_context(db, db.get(Order, order["id"]), settings, "analysis", "")
    sdk = OpenAI(
        api_key="test-key-not-live",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    401,
                    json={
                        "error": {
                            "message": "private key value must not appear",
                            "type": "invalid_request_error",
                        }
                    },
                )
            )
        ),
        max_retries=0,
    )
    with pytest.raises(ProviderError) as error:
        OpenAIProvider(settings, sdk).generate("analysis", context)
    assert error.value.code == "authentication"
    assert "private key" not in str(error.value)


def test_queued_request_survives_new_app_and_rules_are_snapshotted(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    assert submit(client, order).status_code == 202
    with factory() as db:
        job = db.scalar(select(AIJob).where(AIJob.order_id == order["id"]))
        assert len(job.context["rules"]["sha256"]) == 64
        assert "Спам-боты" in job.context["rules"]["text"]
    app = create_app(settings)
    restarted_factory = session_factory(app.state.engine)
    assert process_next(restarted_factory, settings, provider)
    app.state.engine.dispose()
    assert len(state(client, order)["analyses"]) == 1


def test_valid_reference_to_unproven_n8n_cannot_support_A(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    profile = client.get("/api/profile").json()["profile"]
    n8n = next(c for c in profile["capabilities"] if c["label"] == "n8n")
    payload = analysis_result()
    payload["components"][0].update(name="n8n", source_ids=[f"capability:{n8n['id']}"])
    provider.payload = payload
    submit(client, order)
    process_next(factory, settings, provider)
    assert state(client, order)["jobs"][0]["error_code"] == "invalid_output"


def test_unclear_ethics_cannot_publish_stack_estimate_or_draft(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    payload = analysis_result()
    payload["ethics"]["verdict"] = "clarify"
    payload["proposed_stack"] = [{"technology": "Python", "purpose": "Implementation"}]
    payload["effort"] = "One day"
    provider.payload = payload
    submit(client, order)
    process_next(factory, settings, provider)
    result = state(client, order)["analyses"][0]["result"]
    assert result["recommendation"] == "clarify"
    assert result["proposed_stack"] == []
    assert result["effort"] is None
    assert submit(client, order, "draft").status_code == 409


def test_budget_field_change_does_not_change_model_input(consultant):
    client, _, factory, settings = consultant
    order = create_order(client, budget="1000 RUB")
    with factory() as db:
        row = db.get(Order, order["id"])
        before = build_context(db, row, settings, "analysis", "")
        row.budget = "100000 RUB"
        db.commit()
        after = build_context(db, row, settings, "analysis", "")
    # The order revision changes for freshness tracking; the substantive payload does not.
    before.pop("versions")
    after.pop("versions")
    assert before == after


def test_reanalysis_distinguishes_chat_from_added_materials(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    original = client.post(
        "/api/materials/text",
        json={"title": "Старое объявление", "text": "Исходное ТЗ", "order_id": order["id"]},
    ).json()
    submit(client, order)
    process_next(factory, settings, provider)
    first_context = copy.deepcopy(provider.calls[-1])
    assert first_context["changes_since_previous_analysis"] is None
    submit(client, order, "chat", "В уже приложенном объявлении указан Excel. Уточни свой разбор.")
    process_next(factory, settings, provider)
    changes = provider.calls[-1]["changes_since_previous_analysis"]
    assert changes["added_material_source_ids"] == []
    assert changes["removed_material_source_ids"] == []
    assert changes["revision_changed"]["materials"] is False
    assert changes["revision_changed"]["conversation"] is True
    assert provider.calls[-1]["previous_analysis"]["versions"] == first_context["versions"]
    # A genuinely added material is distinguishable from the re-sent original.
    added = client.post(
        "/api/materials/text",
        json={"title": "Ответ заказчика", "text": "Поддержать два сайта", "order_id": order["id"]},
    ).json()
    submit(client, order)
    process_next(factory, settings, provider)
    changes = provider.calls[-1]["changes_since_previous_analysis"]
    assert changes["added_material_source_ids"] == [f"material:{added['id']}"]
    assert f"material:{original['id']}" not in changes["added_material_source_ids"]
    assert changes["revision_changed"]["materials"] is True
    # Another analysis without edits must not invent changed sources.
    submit(client, order)
    process_next(factory, settings, provider)
    changes = provider.calls[-1]["changes_since_previous_analysis"]
    assert not any(changes["revision_changed"].values())
    assert changes["added_material_source_ids"] == changes["removed_material_source_ids"] == []


def test_missing_old_provenance_does_not_claim_new_sources(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client)
    job = submit(client, order).json()
    process_next(factory, settings, provider)
    with factory() as db:
        previous = db.get(AIJob, job["id"])
        context = copy.deepcopy(previous.context)
        context.pop("versions")
        previous.context = context
        db.commit()
        current = build_context(db, db.get(Order, order["id"]), settings, "analysis", "")
        assert current["previous_analysis"]["versions"] is None
        assert current["changes_since_previous_analysis"] is None
        assert previous.context == context


def test_clarification_message_is_saved_and_kwork_copy_is_neutral(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client, source="Kwork", budget="5000")
    payload = analysis_result()
    payload["technical_verdict"] = "needs_check"
    payload["questions"] = [
        {"text": "Есть ли доступ к Instagram?", "why_it_matters": "Нужен источник данных."}
    ]
    payload["client_message"] = "Здравствуйте! Как получать данные из Instagram?"
    provider.payload = payload
    assert submit(client, order).status_code == 202
    assert process_next(factory, settings, provider)
    result = state(client, order)["analyses"][0]["result"]
    assert result["client_message"] == "Здравствуйте! Как получать данные из указанной в ТЗ социальной сети?"
    assert "Instagram" in result["questions"][0]["text"]
    assert provider.calls[0]["communication_context"] == {"platform": "Kwork"}
    assert len(provider.calls) == 1
    # Reading the message never starts a second generation.
    state(client, order)
    assert len(provider.calls) == 1


def test_legacy_questions_are_combined_without_rewriting_saved_analysis(consultant):
    from kotolancer.models import Analysis

    client, provider, factory, settings = consultant
    order = create_order(client, source="Kwork")
    assert submit(client, order).status_code == 202
    assert process_next(factory, settings, provider)
    with factory() as db:
        row = db.scalar(select(Analysis).where(Analysis.order_id == order["id"]))
        old = dict(row.result)
        old.pop("client_message", None)
        old["questions"] = [
            {"text": "Как получить данные из Instagram?", "why_it_matters": "Для доступа."},
            {"text": "Что входит в первую версию?", "why_it_matters": "Для объёма."},
        ]
        row.result = old
        db.commit()
    result = state(client, order)["analyses"][0]["result"]
    assert "1. Как получить данные из указанной в ТЗ социальной сети?" in result["client_message"]
    assert "2. Что входит в первую версию?" in result["client_message"]
    assert "Для доступа." not in result["client_message"]
    with factory() as db:
        row = db.scalar(select(Analysis).where(Analysis.order_id == order["id"]))
        assert "client_message" not in row.result
    assert len(provider.calls) == 1


@pytest.mark.parametrize("declined", [False, True])
def test_no_client_questions_for_empty_questions_or_refusal(consultant, declined):
    client, provider, factory, settings = consultant
    order = create_order(client)
    payload = analysis_result()
    payload["client_message"] = "Не публиковать этот текст."
    if declined:
        payload["ethics"]["verdict"] = "reject"
        payload["questions"] = [{"text": "Лишний вопрос?", "why_it_matters": "Не нужен при отказе."}]
    provider.payload = payload
    assert submit(client, order).status_code == 202
    assert process_next(factory, settings, provider)
    assert state(client, order)["analyses"][0]["result"]["client_message"] is None


def test_kwork_neutral_copy_does_not_change_other_platforms():
    from kotolancer.client_message import platform_copy

    text = "Доступ к Instagram и данные из Инстаграма?"
    assert platform_copy(text, "Внешний источник") == text
    assert platform_copy(text, " Kwork ") == (
        "Доступ к указанной в ТЗ социальной сети и данные из указанной в ТЗ социальной сети?"
    )


def test_kwork_reply_draft_uses_the_same_platform_copy(consultant):
    client, provider, factory, settings = consultant
    order = create_order(client, source="Kwork")
    assert submit(client, order).status_code == 202
    assert process_next(factory, settings, provider)
    provider.payload = {
        "content": "Уточните доступ к Instagram.",
        "experience_claims": [],
        "ethics": {"verdict": "clear", "reason": "Обычная интеграция.", "source_ids": ["order"]},
    }
    assert submit(client, order, "draft").status_code == 202
    assert process_next(factory, settings, provider)
    assert state(client, order)["drafts"][0]["content"] == "Уточните доступ к указанной в ТЗ социальной сети."
