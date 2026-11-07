import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from kotolancer.ai_budget import ProductionBudget, summary
from kotolancer.ai_provider import ProviderError, ProviderResult
from kotolancer.app import create_app
from kotolancer.consultant import claim_next, process_next
from kotolancer.db import session_factory
from kotolancer.models import AIJob, AIRun, EvidenceProposal, GitHubSnapshot, Order, now
from tests.test_consultant import FixtureProvider, submit
from tests.test_foundation import create_order
from tests.test_production_budget import arguments, sdk_count


def result():
    return {
        "summary": "В выбранном файле виден обработчик фото.",
        "observations": [
            {
                "capability_id": "cap-014",
                "observation": "Обработчик изображения передаёт фотографию в функцию сохранения.",
                "limitation": "Выполнение и функция сохранения в этом просмотре не проверялись.",
                "citations": [{"path": "src/bot.py", "line_start": 1, "line_end": 2}],
            }
        ],
    }


class ReviewProvider(FixtureProvider):
    name = "review-fixture"

    def generate(self, kind, context):
        if kind != "github_review":
            return super().generate(kind, context)
        self.calls.append(copy.deepcopy(context))
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        return ProviderResult(
            json.dumps(self.payload if self.payload is not None else result()),
            "fixture-only",
            "fixture-response",
            {"input_tokens": 100, "output_tokens": 100},
        )


@pytest.fixture
def review(settings):
    settings.openai_model = "fixture-only"
    provider = ReviewProvider()
    app = create_app(settings, ai_provider=provider)
    factory = session_factory(app.state.engine)
    with TestClient(app, headers={"X-Koto-Client": "workspace"}) as client:
        with factory() as db:
            snapshot = GitHubSnapshot(
                repository="example-owner/repo",
                commit_sha="a" * 40,
                facts={
                    "mode": "implementation",
                    "warnings": ["Код не запускался"],
                    "files": [
                        {
                            "path": "src/bot.py",
                            "sha": "b" * 40,
                            "kind": "implementation",
                            "text": "def handle_image(message):\n    return save(message.photo)\n",
                            "bytes": 58,
                        },
                        {
                            "path": "README.md",
                            "sha": "c" * 40,
                            "kind": "declaration",
                            "text": "Declared system\n",
                            "bytes": 16,
                        },
                    ],
                },
            )
            db.add(snapshot)
            db.commit()
            snapshot_id = snapshot.id
        yield client, provider, factory, settings, snapshot_id


def enqueue(review, **fields):
    c, _, _, _, snapshot = review
    response = c.post(
        "/api/github/reviews",
        json={
            "request_id": str(uuid4()),
            "snapshot_id": snapshot,
            "expected_revision": c.get("/api/profile").json()["revision"],
            **fields,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def latest(review):
    return review[0].get("/api/github/reviews", params={"snapshot_id": review[4]}).json()["jobs"][0]


def test_review_to_proposal_to_owner_confirmation_preserves_categories(review):
    c, p, f, s, _ = review
    before = c.get("/api/profile").json()
    job = enqueue(review)
    assert job["rules_version"] == "github-review-v1"
    assert process_next(f, s, p)
    output = latest(review)
    assert output["status"] == "completed" and output["result"]["observations"][0]["citations"][0][
        "excerpt"
    ].startswith("def handle_image")
    assert c.get("/api/profile").json() == before and c.get("/api/github/proposals").json() == []
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
    url = "/api/github/reviews/" + job["id"] + "/proposals"
    prepared = c.post(url, json={"index": 0, "expected_revision": 1})
    assert prepared.status_code == 201
    assert c.post(url, json={"index": 0, "expected_revision": 1}).json() == prepared.json()
    assert c.get("/api/profile").json() == before
    assert c.post("/api/github/proposals/" + prepared.json()["id"] + "/accept").status_code == 200
    after = c.get("/api/profile").json()
    assert after["revision"] == 2
    assert [(x["category"], x["scope"]) for x in after["profile"]["capabilities"]] == [
        (x["category"], x["scope"]) for x in before["profile"]["capabilities"]
    ]
    with f() as db:
        row = db.get(EvidenceProposal, prepared.json()["id"])
        assert row.review_job_id == job["id"] and row.review_item == 0


@pytest.mark.parametrize("case", ["file", "line", "capability", "declaration", "duplicate", "extra_category"])
def test_hallucinations_and_unrequested_fields_are_not_published(review, case):
    c, p, f, s, _ = review
    payload = result()
    item = payload["observations"][0]
    if case == "file":
        item["citations"][0]["path"] = "not-selected.py"
    if case == "line":
        item["citations"][0]["line_end"] = 100
    if case == "capability":
        item["capability_id"] = "invented-capability"
    if case == "declaration":
        item["citations"] = [{"path": "README.md", "line_start": 1, "line_end": 1}]
    if case == "duplicate":
        payload["observations"].append(copy.deepcopy(item))
    if case == "extra_category":
        item["category"] = "A"
    p.payload = payload
    enqueue(review)
    assert process_next(f, s, p)
    assert latest(review)["status"] == "failed" and latest(review)["error_code"] == "invalid_output"
    assert c.get("/api/profile").json()["revision"] == 1 and not c.get("/api/github/proposals").json()


@pytest.mark.parametrize("when", ["before", "during", "after"])
def test_profile_revision_fences_generation_and_preparation(review, when):
    c, p, f, s, _ = review
    job = enqueue(review)

    def edit():
        current = c.get("/api/profile").json()
        assert (
            c.put(
                "/api/profile",
                json={
                    "expected_revision": current["revision"],
                    "reason": "Ручное уточнение",
                    "profile": current["profile"],
                },
            ).status_code
            == 200
        )

    if when == "before":
        edit()
    if when == "during":
        p.hook = edit
    assert process_next(f, s, p)
    if when == "after":
        edit()
    state = latest(review)
    assert state["outdated"]
    if when == "before":
        assert not p.calls
    assert (
        c.post(
            "/api/github/reviews/" + job["id"] + "/proposals", json={"index": 0, "expected_revision": 2}
        ).status_code
        == 409
    )


def test_cancelled_and_expired_requests_have_no_retry_or_late_result(review):
    c, p, f, s, _ = review
    job = enqueue(review)
    p.hook = lambda: c.post("/api/github/reviews/" + job["id"] + "/cancel")
    assert process_next(f, s, p) and latest(review)["status"] == "cancelled"
    assert latest(review)["result"] is None
    enqueue(review)
    with f() as db:
        ident, _ = claim_next(db, s)
        item = db.get(AIJob, ident)
        item.lease_until = now() - timedelta(seconds=1)
        db.commit()
    assert not process_next(f, s, p) and len(p.calls) == 1
    assert latest(review)["status"] == "failed"


def test_empty_result_is_valid_and_does_not_claim_no_competence(review):
    c, p, f, s, _ = review
    p.payload = {
        "summary": "В этих фрагментах недостаточно оснований для новых наблюдений.",
        "observations": [],
    }
    job = enqueue(review)
    assert process_next(f, s, p)
    assert latest(review)["status"] == "completed"
    assert (
        c.post(
            "/api/github/reviews/" + job["id"] + "/proposals", json={"index": 0, "expected_revision": 1}
        ).status_code
        == 404
    )
    assert c.get("/api/profile").json()["revision"] == 1


def test_shared_daily_limit_idempotency_and_active_guard(review):
    c, p, f, s, snapshot = review
    key = str(uuid4())
    job = enqueue(review, request_id=key)
    assert enqueue(review, request_id=key)["id"] == job["id"]
    assert (
        c.post(
            "/api/github/reviews",
            json={"request_id": str(uuid4()), "snapshot_id": snapshot, "expected_revision": 1},
        ).status_code
        == 409
    )
    c.post("/api/github/reviews/" + job["id"] + "/cancel")
    s.openai_daily_request_limit = 1
    assert submit(c, create_order(c)).status_code == 429
    assert (
        c.post(
            "/api/github/reviews",
            json={"request_id": str(uuid4()), "snapshot_id": snapshot, "expected_revision": 1},
        ).status_code
        == 429
    )


def test_review_uses_same_durable_monetary_allowance(review):
    c, p, f, s, _ = review
    s.openai_model = "gpt-6-luna"
    s.openai_reasoning_effort = "low"
    s.openai_api_key = SecretStr("fake")
    s.openai_total_budget_nusd = 4_000_000
    order = create_order(c)
    assert submit(c, order).status_code == 202
    with f() as db:
        ident, _ = claim_next(db, s)
        row = db.get(AIJob, ident)
        row.status = "failed"
        run = db.get(AIRun, ident)
        run.reserved_nusd = 3_000_000
        db.commit()
    enqueue(review)
    with f() as db:
        ident, lease = claim_next(db, s)
    sdk, calls = sdk_count()
    with pytest.raises(ProviderError) as error:
        ProductionBudget(f, s, ident, lease).reserve(sdk, arguments())
    assert error.value.code == "budget_exhausted" and len(calls) == 1
    with f() as db:
        assert summary(db, s)["reserved_nusd"] == 3_000_000


def test_changed_snapshot_and_oversized_or_declaration_context(review):
    c, p, f, s, snapshot = review
    enqueue(review)
    with f() as db:
        row = db.get(GitHubSnapshot, snapshot)
        facts = copy.deepcopy(row.facts)
        facts["files"][0]["text"] = "changed"
        row.facts = facts
        db.commit()
    assert process_next(f, s, p) and latest(review)["status"] == "stale" and not p.calls
    with f() as db:
        row = db.get(GitHubSnapshot, snapshot)
        facts = copy.deepcopy(row.facts)
        facts["mode"] = "catalog"
        row.facts = facts
        db.commit()
    assert (
        c.post(
            "/api/github/reviews",
            json={"request_id": str(uuid4()), "snapshot_id": snapshot, "expected_revision": 1},
        ).status_code
        == 422
    )


def test_upgrade_keeps_existing_order_jobs_runs_and_proposals(settings):
    from alembic import command
    from alembic.config import Config

    from kotolancer.config import ROOT
    from kotolancer.db import make_engine

    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.downgrade(config, "0007_github_reads")
    engine = make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO orders (id,title,source,source_url,budget,description,notes,status,materials_revision,conversation_revision,created_at,updated_at,version) VALUES ('old-order','Existing','','','','','','new',1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO ai_jobs (id,order_id,request_id,kind,status,context,created_at) VALUES ('old-job','old-order','old-request','analysis','completed','{}',CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "INSERT INTO ai_runs (job_id,provider,model,rules_version,schema_version,reserved_nusd,created_at) VALUES ('old-job','fixture','fixture','v3','v1',12345,CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "INSERT INTO github_snapshots (id,repository,commit_sha,facts,created_at) VALUES ('old-source','example-owner/repo','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','{}',CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "INSERT INTO evidence_proposals (id,snapshot_id,base_revision,statement,capability_ids,status,created_at) VALUES ('old-proposal','old-source',1,'Existing owner proposal','[]','pending',CURRENT_TIMESTAMP)"
        )
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT order_id FROM ai_jobs WHERE id='old-job'").scalar() == "old-order"
        assert (
            conn.exec_driver_sql("SELECT reserved_nusd FROM ai_runs WHERE job_id='old-job'").scalar() == 12345
        )
        assert (
            conn.exec_driver_sql("SELECT statement FROM evidence_proposals WHERE id='old-proposal'").scalar()
            == "Existing owner proposal"
        )
        assert (
            conn.exec_driver_sql(
                "SELECT review_job_id FROM evidence_proposals WHERE id='old-proposal'"
            ).scalar()
            is None
        )
        if engine.dialect.name == "sqlite":
            assert not conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    engine.dispose()


def test_successful_shared_reservation_and_cancel_before_generation(review):
    c, _, f, s, _ = review
    s.openai_model = "gpt-6-luna"
    s.openai_reasoning_effort = "low"
    s.openai_total_budget_nusd = 1_000_000_000
    job = enqueue(review)
    with f() as db:
        ident, lease = claim_next(db, s)
    sdk, calls = sdk_count()
    reserved = ProductionBudget(f, s, ident, lease).reserve(sdk, arguments())
    assert reserved > 0 and len(calls) == 1
    with f() as db:
        assert summary(db, s)["reserved_nusd"] == reserved
    assert c.post("/api/github/reviews/" + job["id"] + "/cancel").status_code == 200
    with pytest.raises(ProviderError) as error:
        ProductionBudget(f, s, ident, lease).reserve(sdk, arguments())
    assert error.value.code == "budget_lease_lost"
    with f() as db:
        assert summary(db, s)["reserved_nusd"] == reserved


def test_provider_uses_enum_citations_and_numbered_data_without_tools(review):
    from kotolancer.ai_provider import OpenAIProvider

    c, _, f, s, _ = review
    job = enqueue(review)
    with f() as db:
        context = db.get(AIJob, job["id"]).context
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(result()),
            model="fixture-only",
            id="fake",
            usage=None,
            status="completed",
            output=[],
        )

    provider = OpenAIProvider(s, client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    provider.generate("github_review", context)
    request = captured[0]
    schema = request["text"]["format"]["schema"]
    assert request["text"]["format"]["strict"] and "tools" not in request
    assert set(schema["$defs"]["ReviewCitation"]["properties"]["path"]["enum"]) == {"src/bot.py", "README.md"}
    assert "cap-014" in schema["$defs"]["Observation"]["properties"]["capability_id"]["enum"]
    assert request["store"] is False and "недоверенные данные" in request["instructions"]
    body = json.loads(request["input"][0]["content"][0]["text"])
    assert body["files"][0]["lines"][0]["number"] == 1 and "rules" not in body


def test_no_ai_context_limit_and_owner_auth(review):
    c, _, f, s, snapshot = review
    s.ai_max_context_chars = 1000
    response = c.post(
        "/api/github/reviews",
        json={"request_id": str(uuid4()), "snapshot_id": snapshot, "expected_revision": 1},
    )
    assert response.status_code == 422
    s.auth_mode = "token"
    s.access_token = "fixture-access-token-long-enough-for-validation"
    assert c.get("/api/github/reviews", params={"snapshot_id": snapshot}).status_code == 401
    assert (
        c.post(
            "/api/github/reviews",
            json={"request_id": str(uuid4()), "snapshot_id": snapshot, "expected_revision": 1},
        ).status_code
        == 401
    )


def test_multiple_observations_can_be_confirmed_in_one_profile_revision(review):
    c, p, f, s, _ = review
    payload = result()
    second = copy.deepcopy(payload["observations"][0])
    second["capability_id"] = "cap-001"
    payload["observations"].append(second)
    p.payload = payload
    job = enqueue(review)
    assert process_next(f, s, p)
    proposal = c.post(
        "/api/github/reviews/" + job["id"] + "/proposals", json={"index": -1, "expected_revision": 1}
    )
    assert proposal.status_code == 201
    assert c.post("/api/github/proposals/" + proposal.json()["id"] + "/accept").status_code == 200
    after = c.get("/api/profile").json()
    assert after["revision"] == 2
    with f() as db:
        row = db.get(EvidenceProposal, proposal.json()["id"])
        assert set(row.capability_ids) == {"cap-001", "cap-014"} and len(row.citations) == 1
    assert next(x for x in after["profile"]["capabilities"] if x["id"] == "cap-014")["category"] == "A"
