import base64
import hashlib
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from kotolancer.app import create_app
from kotolancer.db import make_engine, session_factory
from kotolancer.github_jobs import process_read
from kotolancer.github_reader import GitHubError, GitHubReader
from kotolancer.models import GitHubReadJob, now


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class Repository:
    def __init__(self):
        self.seen = []
        self.changed = False
        self.corrupt = False
        self.on_blob = None
        self.files = {
            "README.md": b"Declared project\n",
            "src/bot.py": b"def handle_image(message):\n    return save(message.photo)\n",
            "src/other.py": b"print('not read')\n",
        }
        self.entries = [
            {"path": p, "sha": blob(data), "size": len(data), "type": "blob", "mode": "100644"}
            for p, data in self.files.items()
        ]
        self.entries += [
            {"path": p, "sha": "f" * 40, "size": 2, "type": "blob", "mode": mode}
            for p, mode in [
                (".env", "100644"),
                ("secrets.py", "100644"),
                ("node_modules/pkg/a.py", "100644"),
                ("src/link.py", "120000"),
                ("dist/a.py", "100644"),
                ("../a.py", "100644"),
            ]
        ]

    def handle(self, request):
        assert request.method == "GET" and request.url.host == "api.github.com"
        self.seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/repo"):
            body = {"default_branch": "main", "private": True}
        elif "/commits/" in path:
            body = {"sha": ("c" if self.changed else "a") * 40, "commit": {"tree": {"sha": "b" * 40}}}
        elif "/trees/" in path:
            body = {"tree": self.entries, "truncated": False}
        else:
            if self.on_blob:
                self.on_blob()
            sha = path.rsplit("/", 1)[-1]
            data = next(data for data in self.files.values() if blob(data) == sha)
            body = {
                "sha": sha,
                "encoding": "base64",
                "content": base64.b64encode(b"corrupt" if self.corrupt else data).decode(),
            }
        return httpx.Response(200, json=body)


@pytest.fixture
def github_context(settings):
    from pydantic import SecretStr

    settings.github_token = SecretStr("fixture-not-live")
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    repo = Repository()
    transport = httpx.Client(transport=httpx.MockTransport(repo.handle))
    reader = GitHubReader(settings, transport)
    with TestClient(
        create_app(settings, github_reader=reader), headers={"X-Koto-Client": "workspace"}
    ) as client:
        yield settings, factory, repo, reader, client
    transport.close()
    engine.dispose()


def enqueue(ctx, **fields):
    response = ctx[4].post(
        "/api/github/inspect", json={"repository": "repo", "request_key": str(uuid4()), **fields}
    )
    assert response.status_code == 202, response.text
    return response.json()


def finish(ctx):
    assert process_read(ctx[1], ctx[0], ctx[3])
    jobs = ctx[4].get("/api/github/reads").json()
    assert jobs[0]["status"] == "succeeded", jobs[0]
    return next(s for s in ctx[4].get("/api/github/snapshots").json() if s["id"] == jobs[0]["snapshot_id"])


def implementation(ctx):
    enqueue(ctx)
    base = finish(ctx)
    enqueue(ctx, source_snapshot_id=base["id"], paths=["src/bot.py"])
    return finish(ctx)


def test_enqueue_is_fast_idempotent_and_survives_new_factory(github_context):
    ctx = github_context
    key = str(uuid4())
    job = enqueue(ctx, request_key=key)
    assert ctx[2].seen == []
    assert enqueue(ctx, request_key=key)["id"] == job["id"]
    assert (
        ctx[4].post("/api/github/inspect", json={"repository": "different", "request_key": key}).status_code
        == 409
    )
    assert ctx[4].post("/api/github/inspect", json={"repository": "repo"}).status_code == 409
    snapshot = finish(ctx)
    assert snapshot["commit_sha"] == "a" * 40
    assert {f["path"] for f in snapshot["facts"]["available_files"]} == set(ctx[2].files)
    assert ctx[4].get("/api/profile").json()["revision"] == 1


def test_sources_pin_commit_and_get_only_selected_verified_blobs(github_context):
    ctx = github_context
    enqueue(ctx)
    base = finish(ctx)
    ctx[2].changed = True
    ctx[2].seen.clear()
    enqueue(ctx, source_snapshot_id=base["id"], paths=["src/bot.py"])
    source = finish(ctx)
    assert source["commit_sha"] == base["commit_sha"]
    assert ctx[2].seen == ["/repos/example-owner/repo/git/blobs/" + blob(ctx[2].files["src/bot.py"])]
    assert source["facts"]["files"][0]["kind"] == "implementation"
    assert source["facts"]["files"][0]["lines"] == 2


def test_exact_citations_survive_confirmation_without_grade_promotion(github_context):
    ctx = github_context
    source = implementation(ctx)
    before = ctx[4].get("/api/profile").json()
    response = ctx[4].post(
        "/api/github/proposals",
        json={
            "snapshot_id": source["id"],
            "expected_revision": 1,
            "capability_ids": ["cap-014"],
            "statement": "В выбранном фрагменте есть обработчик изображения; запуск не проверялся.",
            "citations": [{"path": "src/bot.py", "line_start": 1, "line_end": 2}],
        },
    )
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert ctx[4].get("/api/profile").json()["revision"] == 1
    saved = ctx[4].get("/api/github/proposals").json()[0]
    assert "def handle_image" in saved["citations"][0]["excerpt"]
    accepted = ctx[4].post("/api/github/proposals/" + proposal["id"] + "/accept")
    assert accepted.status_code == 200, accepted.text
    after = ctx[4].get("/api/profile").json()
    assert [c["category"] for c in after["profile"]["capabilities"]] == [
        c["category"] for c in before["profile"]["capabilities"]
    ]
    evidence = after["profile"]["evidence"][-1]
    assert evidence["citations"][0]["commit_sha"] == "a" * 40
    assert evidence["reference"].endswith("/src/bot.py#L1-L2")
    assert "excerpt" not in evidence["citations"][0]
    assert ctx[4].post("/api/github/proposals/" + proposal["id"] + "/accept").json() == accepted.json()


@pytest.mark.parametrize(
    "citation",
    [
        [],
        [{"path": "missing.py", "line_start": 1, "line_end": 1}],
        [{"path": "src/bot.py", "line_start": 2, "line_end": 1}],
        [{"path": "src/bot.py", "line_start": 1, "line_end": 99}],
    ],
)
def test_invented_or_missing_locations_rejected(github_context, citation):
    ctx = github_context
    source = implementation(ctx)
    r = ctx[4].post(
        "/api/github/proposals",
        json={
            "snapshot_id": source["id"],
            "expected_revision": 1,
            "capability_ids": ["cap-001"],
            "citations": citation,
        },
    )
    assert r.status_code == 422
    assert ctx[4].get("/api/profile").json()["revision"] == 1


def test_cancel_queued_and_inflight_discards_late_result(github_context):
    ctx = github_context
    job = enqueue(ctx)
    ctx[4].post("/api/github/reads/" + job["id"] + "/cancel").raise_for_status()
    assert not process_read(ctx[1], ctx[0], ctx[3])
    job = enqueue(ctx)
    ctx[2].on_blob = lambda: ctx[4].post("/api/github/reads/" + job["id"] + "/cancel").raise_for_status()
    assert process_read(ctx[1], ctx[0], ctx[3])
    assert ctx[4].get("/api/github/reads").json()[0]["status"] == "cancelled"
    assert ctx[4].get("/api/github/snapshots").json() == []


def test_expired_lease_fails_without_retry_and_old_result_cannot_write(github_context):
    ctx = github_context
    job = enqueue(ctx)
    with ctx[1]() as db:
        row = db.get(GitHubReadJob, job["id"])
        row.status, row.lease, row.lease_until = "running", "old", now() - timedelta(seconds=1)
        db.commit()
    assert not process_read(ctx[1], ctx[0], ctx[3])
    assert ctx[2].seen == []
    assert ctx[4].get("/api/github/reads").json()[0]["status"] == "failed"


def test_blob_corruption_and_network_exception_are_terminal_and_redacted(github_context):
    ctx = github_context
    ctx[2].corrupt = True
    enqueue(ctx)
    assert process_read(ctx[1], ctx[0], ctx[3])
    assert ctx[4].get("/api/github/reads").json()[0]["status"] == "failed"
    assert ctx[4].get("/api/github/snapshots").json() == []
    assert not process_read(ctx[1], ctx[0], ctx[3])
    ctx[2].corrupt = False
    ctx[2].on_blob = lambda: (_ for _ in ()).throw(RuntimeError("SECRET-NOT-FOR-OUTPUT"))
    enqueue(ctx)
    assert process_read(ctx[1], ctx[0], ctx[3])
    assert "SECRET" not in ctx[4].get("/api/github/reads").text


def test_stale_manual_profile_and_rejection_are_preserved(github_context):
    ctx = github_context
    source = implementation(ctx)
    proposal = (
        ctx[4]
        .post(
            "/api/github/proposals",
            json={
                "snapshot_id": source["id"],
                "expected_revision": 1,
                "capability_ids": ["cap-001"],
                "citations": [{"path": "src/bot.py", "line_start": 1, "line_end": 1}],
            },
        )
        .json()
    )
    current = ctx[4].get("/api/profile").json()
    current["profile"]["rules"].append("Ручная правка остаётся")
    ctx[4].put(
        "/api/profile",
        json={"expected_revision": 1, "reason": "Owner correction", "profile": current["profile"]},
    ).raise_for_status()
    assert ctx[4].post("/api/github/proposals/" + proposal["id"] + "/accept").status_code == 409
    assert ctx[4].post("/api/github/proposals/" + proposal["id"] + "/reject").status_code == 200
    assert ctx[4].get("/api/profile").json()["profile"]["rules"][-1] == "Ручная правка остаётся"


def test_deadline_requests_and_invalid_paths(github_context):
    ctx = github_context
    enqueue(ctx)
    source = finish(ctx)
    for paths in [[".env"], ["../a.py"], ["src/bot.py", "src/bot.py"]]:
        assert (
            ctx[4]
            .post(
                "/api/github/inspect",
                json={"repository": "repo", "source_snapshot_id": source["id"], "paths": paths},
            )
            .status_code
            == 422
        )
    import time

    ctx[3].deadline = time.monotonic() - 1
    with pytest.raises(GitHubError):
        ctx[3].get("/repos/example-owner/repo")


def test_replaced_lease_cannot_publish_a_snapshot(github_context):
    ctx = github_context
    job = enqueue(ctx)

    def replace():
        with ctx[1]() as db:
            row = db.get(GitHubReadJob, job["id"])
            row.lease = "different-owner"
            db.commit()

    ctx[2].on_blob = replace
    assert process_read(ctx[1], ctx[0], ctx[3])
    assert ctx[4].get("/api/github/snapshots").json() == []
    with ctx[1]() as db:
        assert db.get(GitHubReadJob, job["id"]).lease == "different-owner"


def test_running_cancel_and_profile_require_owner_auth(settings):
    from tests.test_integrations import GitFixture

    settings.auth_mode = "token"
    settings.access_token = "x" * 40
    with TestClient(
        create_app(settings, github_reader=GitFixture()), headers={"X-Koto-Client": "workspace"}
    ) as c:
        assert c.get("/api/github/reads").status_code == 401
        assert c.post("/api/github/inspect", json={"repository": "repo"}).status_code == 401
        assert c.post("/api/github/reads/anything/cancel").status_code == 401
        assert c.get("/api/github/snapshots/anything").status_code == 401


def test_metadata_claim_cannot_be_relabelled_as_implementation(github_context):
    ctx = github_context
    enqueue(ctx)
    snapshot = finish(ctx)
    assert snapshot["facts"]["files"][0]["kind"] == "declaration"
    proposal = ctx[4].post(
        "/api/github/proposals",
        json={
            "snapshot_id": snapshot["id"],
            "expected_revision": 1,
            "capability_ids": ["cap-001"],
            "citations": [{"path": "README.md", "line_start": 1, "line_end": 1}],
        },
    )
    assert proposal.status_code == 201
    saved = ctx[4].get("/api/github/proposals").json()[0]
    assert saved["citations"][0]["kind"] == "declaration"
    assert "работоспособность" in saved["statement"]


def test_existing_snapshot_and_proposal_survive_migration(settings):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from kotolancer.config import ROOT

    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.downgrade(config, "0006_order_lifecycle")
    engine = make_engine(settings.database_url)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO github_snapshots (id,repository,commit_sha,facts,created_at) VALUES ('legacy','example-owner/repo',:sha,:facts,:stamp)"
            ),
            {"sha": "a" * 40, "facts": '{"files":[],"warnings":[]}', "stamp": now()},
        )
        c.execute(
            text(
                "INSERT INTO evidence_proposals (id,snapshot_id,base_revision,statement,capability_ids,status,created_at) VALUES ('legacy','legacy',1,'Legacy owner observation',:caps,'pending',:stamp)"
            ),
            {"caps": '["cap-001"]', "stamp": now()},
        )
    command.upgrade(config, "head")
    with engine.connect() as c:
        assert (
            c.execute(text("SELECT statement FROM evidence_proposals WHERE id='legacy'")).scalar()
            == "Legacy owner observation"
        )
        assert c.execute(text("SELECT count(*) FROM github_read_jobs")).scalar() == 0
    with session_factory(engine)() as db:
        from kotolancer.models import EvidenceProposal

        assert db.get(EvidenceProposal, "legacy").citations == []
    engine.dispose()
