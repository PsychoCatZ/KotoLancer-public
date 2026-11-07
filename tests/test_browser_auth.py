from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from kotolancer.app import create_app
from kotolancer.browser_auth import PENDING, SESSION, hashed
from kotolancer.db import make_engine, session_factory
from kotolancer.models import BrowserLogin, BrowserSession, Material, TelegramUpdate, now
from kotolancer.telegram_bot import ingest_next, persist_batch

HEADERS = {"X-Koto-Client": "workspace", "Origin": "https://testserver"}


class Bot:
    def __init__(self):
        self.calls = []

    def call(self, method, **body):
        self.calls.append((method, body))
        return {}


@pytest.fixture
def browser(settings):
    settings.auth_mode = "telegram"
    settings.telegram_owner_id = 42
    settings.telegram_bot_token = SecretStr("999:synthetic-only-token")
    settings.mini_app_url = "https://testserver"
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    with TestClient(create_app(settings), base_url="https://testserver", headers=HEADERS) as client:
        yield client, settings, factory
    engine.dispose()


def challenge(client):
    response = client.post("/auth/browser/start")
    assert response.status_code == 200
    data = response.json()
    return data, parse_qs(urlsplit(data["telegram_url"]).query)["start"][0].removeprefix("login_")


def deliver(factory, settings, payload):
    bot = Bot()
    persist_batch(factory, 999, [payload], 42)
    ingest_next(factory, settings, bot, 999)
    return bot


def callback(token, ident=2, owner=42, chat_type="private", bot_id=999, decision="ok"):
    return {
        "update_id": ident,
        "callback_query": {
            "id": str(ident),
            "from": {"id": owner},
            "data": f"login_{decision}:{token}",
            "message": {"from": {"id": bot_id}, "chat": {"id": 42, "type": chat_type}},
        },
    }


def approve(browser):
    client, settings, factory = browser
    data, token = challenge(client)
    deliver(factory, settings, callback(token))
    result = client.post("/auth/browser/complete")
    assert result.json()["status"] == "authenticated"
    return data, token, result


def test_browser_login_needs_explicit_owner_callback_and_bound_browser(browser):
    client, settings, factory = browser
    data, token = challenge(client)
    assert client.get("/api/orders").status_code == 401
    assert client.post("/auth/browser/complete").json()["status"] == "pending"
    bot = deliver(
        factory,
        settings,
        {
            "update_id": 1,
            "message": {
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "/start login_" + token,
            },
        },
    )
    assert data["code"] in bot.calls[0][1]["text"]
    assert client.post("/auth/browser/complete").json()["status"] == "pending"
    deliver(factory, settings, callback(token))
    with TestClient(create_app(settings), base_url="https://testserver", headers=HEADERS) as stranger:
        assert stranger.post("/auth/browser/complete").json()["status"] == "expired"
        assert stranger.get("/api/orders").status_code == 401
    response = client.post("/auth/browser/complete")
    assert response.json()["status"] == "authenticated"
    assert client.get("/api/profile").status_code == 200
    assert client.post("/api/orders", json={"title": "Browser owner"}).status_code == 201
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 0
        assert all(not row.payload for row in db.scalars(select(TelegramUpdate)))
        row = db.scalar(select(BrowserSession))
        assert row.token_hash == hashed(client.cookies.get(SESSION))
        assert token not in row.token_hash
    cookies = response.headers.get_list("set-cookie")
    assert any(
        SESSION in v and "HttpOnly" in v and "Secure" in v and "SameSite=strict" in v and "Path=/" in v
        for v in cookies
    )


@pytest.mark.parametrize(
    "owner,chat_type,bot_id", [(43, "private", 999), (42, "group", 999), (42, "private", 111)]
)
def test_foreign_or_group_or_wrong_bot_callback_cannot_approve(browser, owner, chat_type, bot_id):
    client, settings, factory = browser
    _, token = challenge(client)
    bot = deliver(factory, settings, callback(token, owner=owner, chat_type=chat_type, bot_id=bot_id))
    assert bot.calls == []
    assert client.post("/auth/browser/complete").json()["status"] == "pending"


def test_owner_can_reject_and_approval_cannot_reverse_rejection(browser):
    client, settings, factory = browser
    _, token = challenge(client)
    deliver(factory, settings, callback(token, decision="no"))
    deliver(factory, settings, callback(token, ident=3))
    assert client.post("/auth/browser/complete").json()["status"] == "rejected"
    assert client.get("/api/orders").status_code == 401


def test_login_expiry_blocks_late_callback(browser):
    client, settings, factory = browser
    _, token = challenge(client)
    with factory() as db:
        row = db.scalar(select(BrowserLogin))
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    deliver(factory, settings, callback(token))
    assert client.post("/auth/browser/complete").json()["status"] == "expired"
    assert client.get("/api/orders").status_code == 401


@pytest.mark.parametrize("origin", [None, "https://evil.invalid", "null", "https://testserver.evil.invalid"])
def test_browser_auth_rejects_missing_or_foreign_origin(browser, origin):
    client, _, _ = browser
    client.headers.pop("Origin")
    if origin:
        client.headers["Origin"] = origin
    assert client.post("/auth/browser/start").status_code == 403
    assert client.post("/auth/browser/complete").status_code == 403
    assert client.post("/auth/browser/logout").status_code == 403


def test_session_persists_app_restart_and_logout_revokes_copied_cookie(browser):
    client, settings, _ = browser
    approve(browser)
    secret = client.cookies.get(SESSION)
    with TestClient(create_app(settings), base_url="https://testserver", headers=HEADERS) as other:
        other.cookies.set(SESSION, secret)
        assert other.get("/api/orders").status_code == 200
        assert other.get("/api/workspace").json()["browser_session"] is True
        assert client.post("/auth/browser/logout").status_code == 200
        assert other.get("/api/orders").status_code == 401
    assert client.get("/api/orders").status_code == 401


def test_expired_session_and_rotated_owner_binding_are_rejected(browser):
    client, settings, factory = browser
    approve(browser)
    with factory() as db:
        row = db.scalar(select(BrowserSession))
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/orders").status_code == 401
    with factory() as db:
        row = db.scalar(select(BrowserSession))
        row.expires_at = now() + timedelta(days=1)
        db.commit()
    settings.telegram_owner_id = 43
    assert client.get("/api/orders").status_code == 401


def test_session_does_not_override_invalid_mini_app_signature_and_guards_writes(browser):
    client, _, _ = browser
    approve(browser)
    assert client.get("/api/orders", headers={"X-Telegram-Init-Data": "forged"}).status_code == 401
    assert (
        client.post(
            "/api/orders", json={"title": "bad"}, headers={"Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )


def test_public_login_creation_is_bounded_and_never_sends_bot_messages(browser):
    client, _, factory = browser
    for _ in range(20):
        client.cookies.clear()
        challenge(client)
    client.cookies.clear()
    assert client.post("/auth/browser/start").status_code == 429
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BrowserLogin)) == 20
        assert db.scalar(select(func.count()).select_from(TelegramUpdate)) == 0


def test_consumed_request_cannot_create_another_session(browser):
    client, settings, factory = browser
    _, token, _ = approve(browser)
    with factory() as db:
        login = db.scalar(select(BrowserLogin))
        browser_hash = login.browser_hash
    client.cookies.clear()
    # Approval capability is independent from the HttpOnly browser cookie and is not redeemable.
    client.cookies.set(PENDING, token)
    assert hashed(token) != browser_hash
    assert client.post("/auth/browser/complete").json()["status"] == "expired"
    deliver(factory, settings, callback(token, ident=3))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BrowserSession)) == 1


def test_two_concurrent_polls_only_issue_one_session(browser):
    from concurrent.futures import ThreadPoolExecutor

    client, settings, factory = browser
    _, token = challenge(client)
    pending = client.cookies.get(PENDING)
    deliver(factory, settings, callback(token))

    def poll(_):
        with TestClient(create_app(settings), base_url="https://testserver", headers=HEADERS) as other:
            other.cookies.set(PENDING, pending)
            response = other.post("/auth/browser/complete")
            assert response.status_code in {200, 409}
            return response.json().get("status", "conflict")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(poll, range(2)))
    assert results.count("authenticated") == 1
    assert set(results) <= {"authenticated", "consumed", "conflict"}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BrowserSession)) == 1
