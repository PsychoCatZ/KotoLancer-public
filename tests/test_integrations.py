import base64
import hashlib
import hmac
import io
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from kotolancer.app import create_app
from kotolancer.db import make_engine, session_factory
from kotolancer.github_reader import GitHubError, GitHubReader
from kotolancer.models import Material, TelegramCursor, TelegramUpdate
from kotolancer.telegram_auth import verify_init_data
from kotolancer.telegram_bot import TelegramClient, TelegramError, ingest_next, persist_batch


def init_data(token, owner=42, age=0, extra=None):
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": owner}), **(extra or {})}
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(
        key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode({**fields, "hash": signature})


@pytest.mark.parametrize("case", ["modified", "other_owner", "expired", "future", "duplicate", "unsigned"])
def test_init_data_rejects_forgery_owner_and_time(case):
    token = "123456:fake-token-not-live"
    raw = init_data(token)
    if case == "modified":
        raw = raw.replace("42", "43")
    if case == "other_owner":
        raw = init_data(token, owner=43)
    if case == "expired":
        raw = init_data(token, age=4000)
    if case == "future":
        raw = init_data(token, age=-100)
    if case == "duplicate":
        raw += "&auth_date=123"
    if case == "unsigned":
        raw = urlencode({"user": '{"id":42}', "auth_date": str(int(time.time()))})
    with pytest.raises(ValueError):
        verify_init_data(raw, token, 42, 3600)


def test_telegram_auth_is_required_for_all_private_api(settings):
    settings.auth_mode = "telegram"
    settings.telegram_bot_token = SecretStr("123456:fake-token-not-live")
    settings.telegram_owner_id = 42
    raw = init_data(
        settings.telegram_bot_token.get_secret_value(),
        extra={"signature": "third-party-signature", "query_id": "test"},
    )
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as client:
        assert client.get("/api/orders").status_code == 401
        client.headers["X-Telegram-Init-Data"] = raw
        assert client.get("/api/orders").status_code == 200
        client.headers["X-Telegram-Init-Data"] = init_data(
            settings.telegram_bot_token.get_secret_value(), owner=43
        )
        assert client.get("/api/materials").status_code == 401
    assert verify_init_data(raw, settings.telegram_bot_token.get_secret_value(), 42, 3600) == 42


def update(update_id=1, owner=42, chat_type="private", **message):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": owner},
            "chat": {"id": owner, "type": chat_type},
            **message,
        },
    }


class BotFixture:
    def __init__(self):
        self.downloads, self.replies = [], []

    def download(self, file_id):
        self.downloads.append(file_id)
        result = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(result, format="PNG")
        return result.getvalue()

    def call(self, method, **body):
        self.replies.append((method, body))
        return {}


def test_telegram_inbox_durable_idempotent_caption_and_foreign_sender(settings):
    settings.telegram_owner_id = 42
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    bot = BotFixture()
    batch = [
        update(text="Объявление", forward_origin={"sender_user": {"id": 900}}),
        update(2, photo=[{"file_id": "small"}, {"file_id": "large"}], caption="ТЗ на скриншоте"),
        update(3, owner=43, text="Не Кот"),
        update(4, chat_type="group", text="Группа"),
    ]
    persist_batch(factory, 123, batch, 42)
    # New session factory simulates resuming after polling saved but before material ingestion.
    factory = session_factory(engine)
    while ingest_next(factory, settings, bot, 123):
        pass
    persist_batch(factory, 123, batch, 42)
    assert ingest_next(factory, settings, bot, 123) is False
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 2
        assert db.get(TelegramCursor, 123).next_offset == 5
        assert db.get(TelegramUpdate, (123, 3)).payload == {}
        photo = db.get(Material, db.get(TelegramUpdate, (123, 2)).material_ids[0])
        assert photo.text == "ТЗ на скриншоте" and photo.sha256 and photo.order_id is None
        assert (settings.storage_dir / photo.file_key).is_file()
    assert bot.downloads == ["large"]
    assert len(bot.replies) == 2
    engine.dispose()


def test_telegram_unsupported_file_failure_is_visible_and_command_not_material(settings):
    settings.telegram_owner_id = 42
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    bot = BotFixture()
    persist_batch(
        factory,
        123,
        [update(text="/start"), update(2, document={"file_id": "file", "file_name": "unsafe.html"})],
        42,
    )
    while ingest_next(factory, settings, bot, 123):
        pass
    with factory() as db:
        assert db.get(TelegramUpdate, (123, 1)).status == "command"
        assert db.get(TelegramUpdate, (123, 2)).status == "failed"
        assert db.scalar(select(func.count()).select_from(Material)) == 0
    engine.dispose()


def test_telegram_http_failure_redacts_token(settings):
    secret = "123456:secret-token-must-not-leak"
    settings.telegram_bot_token = SecretStr(secret)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"description": secret}))
    )
    with pytest.raises(TelegramError) as error:
        TelegramClient(settings, client).call("getMe")
    assert secret not in str(error.value)
    client.close()


def test_telegram_unknown_commit_keeps_original_and_does_not_duplicate(settings):
    settings.telegram_owner_id = 42
    engine = make_engine(settings.database_url)
    regular = session_factory(engine)
    bot = BotFixture()
    persist_batch(regular, 123, [update(photo=[{"file_id": "photo"}])], 42)

    class LostAcknowledgement(Session):
        def commit(self):
            super().commit()
            raise OSError("Connection lost after server committed")

    broken = sessionmaker(engine, class_=LostAcknowledgement, expire_on_commit=False)
    with pytest.raises(OSError):
        ingest_next(broken, settings, bot, 123)
    with regular() as db:
        row = db.get(TelegramUpdate, (123, 1))
        assert row.status == "saved"
        material = db.get(Material, row.material_ids[0])
        assert (settings.storage_dir / material.file_key).is_file()
        assert db.scalar(select(func.count()).select_from(Material)) == 1
    assert ingest_next(regular, settings, bot, 123) is False
    engine.dispose()


def test_github_inspection_only_get_exact_commit_and_bounded_files(settings):
    settings.github_token = SecretStr("github_pat_test-not-live")
    content = b'[project]\nname="example"\n'
    blob_sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    seen = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/repo"):
            body = {"default_branch": "main", "language": "Python", "private": True}
        elif "/commits/" in path:
            body = {"sha": "a" * 40, "commit": {"tree": {"sha": "b" * 40}}}
        elif "/trees/" in path:
            body = {
                "truncated": False,
                "tree": [
                    {
                        "path": "pyproject.toml",
                        "sha": blob_sha,
                        "size": len(content),
                        "type": "blob",
                        "mode": "100644",
                    },
                    {"path": ".env", "sha": "d" * 40, "size": 20, "type": "blob", "mode": "100644"},
                ],
            }
        else:
            body = {"sha": blob_sha, "encoding": "base64", "content": base64.b64encode(content).decode()}
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        reader = GitHubReader(settings, transport)
        result = reader.inspect("repo")
        assert result["commit_sha"] == "a" * 40
        assert [f["path"] for f in result["facts"]["files"]] == ["pyproject.toml"]
        assert len(seen) == 4 and result["facts"]["warnings"]
        with pytest.raises(GitHubError):
            reader.inspect("../another-owner/repo")
        assert len(seen) == 4


class GitFixture:
    progress = None

    def inspect(self, repository, source=None, paths=None):
        return {
            "repository": f"example-owner/{repository}",
            "commit_sha": "a" * 40,
            "facts": {
                "files": [{"path": "README.md", "text": "Sample evidence"}],
                "warnings": ["Selected files only"],
            },
        }


def read_fixture(client, settings):
    from kotolancer.github_jobs import process_read

    job = client.post("/api/github/inspect", json={"repository": "repo"})
    assert job.status_code == 202 and job.json()["status"] == "queued"
    engine = make_engine(settings.database_url)
    assert process_read(session_factory(engine), settings, GitFixture())
    engine.dispose()
    return client.get("/api/github/snapshots").json()[0]


def test_github_evidence_requires_confirmation_preserves_levels_and_is_idempotent(settings):
    with TestClient(
        create_app(settings, github_reader=GitFixture()), headers={"X-Koto-Client": "workspace"}
    ) as client:
        before = client.get("/api/profile").json()
        snapshot = read_fixture(client, settings)
        assert client.get("/api/profile").json()["revision"] == before["revision"]
        body = {
            "snapshot_id": snapshot["id"],
            "expected_revision": before["revision"],
            "statement": "README этого коммита описывает обычный workflow, реализация не проверена.",
            "capability_ids": ["cap-038"],
        }
        proposal = client.post("/api/github/proposals", json=body).json()
        assert client.get("/api/profile").json()["revision"] == before["revision"]
        accepted = client.post(f"/api/github/proposals/{proposal['id']}/accept").json()
        after = client.get("/api/profile").json()
        assert after["revision"] == accepted["revision"]
        assert [c["category"] for c in after["profile"]["capabilities"]] == [
            c["category"] for c in before["profile"]["capabilities"]
        ]
        assert len(after["profile"]["evidence"]) == len(before["profile"]["evidence"]) + 1
        assert client.post(f"/api/github/proposals/{proposal['id']}/accept").json() == accepted
        # A second proposal based on the old profile cannot silently apply after the first.
        assert client.post("/api/github/proposals", json=body).status_code == 409


def test_stale_github_proposal_cannot_apply_or_erase_manual_changes(settings):
    with TestClient(
        create_app(settings, github_reader=GitFixture()), headers={"X-Koto-Client": "workspace"}
    ) as client:
        before = client.get("/api/profile").json()
        snapshot = read_fixture(client, settings)
        proposal = client.post(
            "/api/github/proposals",
            json={
                "snapshot_id": snapshot["id"],
                "expected_revision": before["revision"],
                "statement": "В README есть описание проекта, выполнение не подтверждено.",
                "capability_ids": ["cap-001"],
            },
        ).json()
        before["profile"]["rules"].append("Ручная правка Кота")
        changed = client.put(
            "/api/profile",
            json={
                "expected_revision": before["revision"],
                "reason": "Manual correction",
                "profile": before["profile"],
            },
        ).json()
        assert client.post(f"/api/github/proposals/{proposal['id']}/accept").status_code == 409
        assert client.get("/api/profile").json()["revision"] == changed["revision"]
